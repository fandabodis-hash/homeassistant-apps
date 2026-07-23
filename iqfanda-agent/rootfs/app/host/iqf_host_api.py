#!/usr/bin/env python3

import json
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from setup_manager import setup_manager
from cloud_client import cloud_client
from credential_store import credential_store


HOST = "127.0.0.1"
PORT = 8091
WIFI_INTERFACE = "wlan0"


def run_nmcli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["nmcli", *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def get_wifi_status() -> dict:
    device_result = run_nmcli(
        "-t",
        "-f",
        "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS",
        "device",
        "show",
        WIFI_INTERFACE,
    )

    if device_result.returncode != 0:
        return {
            "ok": False,
            "connected": False,
            "interface": WIFI_INTERFACE,
            "ssid": None,
            "signal": None,
            "ip_address": None,
            "error": (
                device_result.stderr.strip()
                or "Nepodařilo se načíst stav Wi-Fi."
            ),
        }

    values: dict[str, str] = {}

    for line in device_result.stdout.splitlines():
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        values[key] = value

    connection_name = (
        values.get("GENERAL.CONNECTION") or None
    )
    state = values.get("GENERAL.STATE", "")
    ip_address = (
        values.get("IP4.ADDRESS[1]")
        or values.get("IP4.ADDRESS")
        or None
    )

    connected = (
        state.startswith("100")
        and connection_name not in (None, "--")
    )

    ssid = None
    signal = None
    security = None

    wifi_result = run_nmcli(
        "-t",
        "-f",
        "ACTIVE,SSID,SIGNAL,SECURITY",
        "device",
        "wifi",
        "list",
        "ifname",
        WIFI_INTERFACE,
    )

    if wifi_result.returncode == 0:
        for line in wifi_result.stdout.splitlines():
            parts = line.split(":", 3)

            if len(parts) != 4:
                continue

            active, found_ssid, found_signal, found_security = parts

            if active == "yes":
                ssid = found_ssid or None

                try:
                    signal = int(found_signal)
                except ValueError:
                    signal = None

                security = found_security or None
                break

    return {
        "ok": True,
        "connected": connected,
        "interface": WIFI_INTERFACE,
        "connection_name": connection_name,
        "ssid": ssid,
        "signal": signal,
        "security": security,
        "ip_address": ip_address,
        "state": state,
    }


def scan_wifi_networks() -> dict:
    rescan_result = run_nmcli(
        "device",
        "wifi",
        "rescan",
        "ifname",
        WIFI_INTERFACE,
    )

    # Rescan může selhat například při právě probíhajícím skenu.
    # V takovém případě se pokusíme načíst poslední známý seznam.
    list_result = run_nmcli(
        "-t",
        "-g",
        "SSID,SIGNAL,SECURITY,FREQ",
        "device",
        "wifi",
        "list",
        "ifname",
        WIFI_INTERFACE,
        "--rescan",
        "yes",
    )

    if list_result.returncode != 0:
        return {
            "ok": False,
            "interface": WIFI_INTERFACE,
            "networks": [],
            "error": (
                list_result.stderr.strip()
                or rescan_result.stderr.strip()
                or "Nepodařilo se vyhledat Wi-Fi sítě."
            ),
        }

    networks_by_ssid: dict[str, dict] = {}

    for line in list_result.stdout.splitlines():
        parts = line.rsplit(":", 3)

        if len(parts) != 4:
            continue

        ssid = parts[0].replace("\\:", ":").strip()

        if not ssid:
            continue

        try:
            signal = int(parts[1])
        except ValueError:
            signal = 0

        security = parts[2].strip()
        frequency_text = parts[3].strip()

        frequency_match = re.search(
            r"\d+",
            frequency_text,
        )

        frequency = (
            int(frequency_match.group(0))
            if frequency_match
            else None
        )

        if frequency is None:
            band = None
        elif frequency < 3000:
            band = "2.4 GHz"
        elif frequency < 5900:
            band = "5 GHz"
        else:
            band = "6 GHz"

        network = {
            "ssid": ssid,
            "signal": signal,
            "security": security,
            "frequency": frequency,
            "band": band,
        }

        current = networks_by_ssid.get(ssid)

        if current is None or signal > current.get("signal", 0):
            networks_by_ssid[ssid] = network

    networks = sorted(
        networks_by_ssid.values(),
        key=lambda item: item.get("signal", 0),
        reverse=True,
    )

    return {
        "ok": True,
        "interface": WIFI_INTERFACE,
        "networks": networks,
    }



def check_internet_access() -> dict:
    """
    Ověří, zda má zařízení funkční odchozí přístup k internetu.

    Nepoužívá DNS, takže test funguje i před ověřením
    dostupnosti konkrétního cloudového serveru.
    """
    import socket

    targets = (
        ("1.1.1.1", 443),
        ("8.8.8.8", 443),
    )

    errors: list[str] = []

    for host, port in targets:
        try:
            with socket.create_connection(
                (host, port),
                timeout=3,
            ):
                return {
                    "ok": True,
                    "host": host,
                    "port": port,
                }

        except OSError as exc:
            errors.append(f"{host}:{port}: {exc}")

    return {
        "ok": False,
        "error": (
            "Internetové připojení není dostupné. "
            + " | ".join(errors)
        ),
    }


def connect_wifi(
    ssid: str,
    password: str,
    interface: str = WIFI_INTERFACE,
) -> dict:
    ssid = ssid.strip()
    password = str(password or "")

    if not ssid:
        return {
            "ok": False,
            "error": "SSID nesmí být prázdné.",
        }

    if len(password) < 8:
        return {
            "ok": False,
            "error": "Heslo Wi-Fi musí mít alespoň 8 znaků.",
        }

    profile_name = "IQF Client WiFi"

    # Instalační AP zde nevypínáme ani nemažeme.
    # Ukončení AP provede až Setup Manager po úspěšné
    # cloudové registraci a potvrzeném heartbeat.

    # Odstranění starého klientského profilu stejného názvu.
    run_nmcli(
        "connection",
        "delete",
        profile_name,
    )

    add_result = run_nmcli(
        "connection",
        "add",
        "type",
        "wifi",
        "ifname",
        interface,
        "con-name",
        profile_name,
        "ssid",
        ssid,
    )

    if add_result.returncode != 0:
        return {
            "ok": False,
            "interface": interface,
            "ssid": ssid,
            "error": (
                add_result.stderr.strip()
                or add_result.stdout.strip()
                or "Nepodařilo se vytvořit Wi-Fi profil."
            ),
        }

    modify_result = run_nmcli(
        "connection",
        "modify",
        profile_name,
        "802-11-wireless.mode",
        "infrastructure",
        "802-11-wireless-security.key-mgmt",
        "wpa-psk",
        "802-11-wireless-security.psk",
        password,
        "ipv4.method",
        "auto",
        "ipv6.method",
        "auto",
        "connection.autoconnect",
        "yes",
    )

    if modify_result.returncode != 0:
        run_nmcli(
            "connection",
            "delete",
            profile_name,
        )

        return {
            "ok": False,
            "interface": interface,
            "ssid": ssid,
            "error": (
                modify_result.stderr.strip()
                or modify_result.stdout.strip()
                or "Nepodařilo se nastavit Wi-Fi profil."
            ),
        }

    up_result = run_nmcli(
        "connection",
        "up",
        profile_name,
    )

    if up_result.returncode != 0:
        return {
            "ok": False,
            "interface": interface,
            "ssid": ssid,
            "error": (
                up_result.stderr.strip()
                or up_result.stdout.strip()
                or "Aktivace Wi-Fi profilu se nezdařila."
            ),
        }

    status_result = get_wifi_status()

    return {
        "ok": True,
        "interface": interface,
        "ssid": ssid,
        "profile_name": profile_name,
        "message": "Zařízení bylo připojeno k Wi-Fi.",
        "status": status_result,
    }


def get_network_info() -> dict:
    result = run_nmcli(
        "-t",
        "-f",
        "DEVICE,TYPE,STATE,CONNECTION",
        "device",
        "status",
    )

    if result.returncode != 0:
        return {
            "ok": False,
            "interfaces": [],
            "error": (
                result.stderr.strip()
                or "Nepodařilo se načíst síťová rozhraní."
            ),
        }

    interfaces = []

    for line in result.stdout.splitlines():
        parts = line.split(":", 3)

        if len(parts) != 4:
            continue

        device, device_type, state, connection = parts

        ip_result = run_nmcli(
            "-t",
            "-f",
            "IP4.ADDRESS",
            "device",
            "show",
            device,
        )

        ip_address = None

        if ip_result.returncode == 0:
            for ip_line in ip_result.stdout.splitlines():
                if ":" not in ip_line:
                    continue

                _, value = ip_line.split(":", 1)

                if value:
                    ip_address = value
                    break

        interfaces.append({
            "name": device,
            "type": device_type,
            "state": state,
            "connected": state == "connected",
            "connection": (
                None if connection == "--" else connection
            ),
            "ip_address": ip_address,
        })

    return {
        "ok": True,
        "interfaces": interfaces,
    }


class HostApiHandler(BaseHTTPRequestHandler):
    server_version = "IQFHostAPI/0.3"

    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(status_code)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        content_length = int(
            self.headers.get("Content-Length", "0")
        )

        if content_length <= 0:
            return {}

        raw_body = self.rfile.read(content_length)
        payload = json.loads(raw_body.decode("utf-8"))

        if not isinstance(payload, dict):
            raise ValueError("JSON tělo musí být objekt.")

        return payload

    def do_POST(self) -> None:
        if self.path == "/setup/start":
            try:
                payload = self._read_json_body()

                result = setup_manager.start(
                    ssid=str(payload.get("ssid", "")),
                    password=str(payload.get("password", "")),
                    provisioning_id=str(
                        payload.get("provisioning_id", "")
                    ),
                    first_name=str(
                        payload.get("first_name", "")
                    ),
                    last_name=str(
                        payload.get("last_name", "")
                    ),
                    email=str(
                        payload.get("email", "")
                    ),
                    account_password=str(
                        payload.get("account_password", "")
                    ),
                    site_name=str(
                        payload.get("site_name", "")
                    ),
                    site_type=str(
                        payload.get("site_type", "house")
                    ),
                    device_name=str(
                        payload.get(
                            "device_name",
                            "TNG IQ FANDA",
                        )
                    ),
                    software_version=payload.get(
                        "software_version"
                    ),
                    interface=str(
                        payload.get(
                            "interface",
                            WIFI_INTERFACE,
                        )
                    ),
                    connector=connect_wifi,
                    network_checker=check_internet_access,
                    registrar=cloud_client.register_device,
                    credential_saver=(
                        credential_store.save_registration
                    ),
                )

                if result.get("ok"):
                    status_code = 202
                elif result.get("error") == "Onboarding již probíhá.":
                    status_code = 409
                else:
                    status_code = 400

                self._send_json(status_code, result)

            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValueError,
            ) as exc:
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": f"Neplatný požadavek: {exc}",
                    },
                )

            except Exception as exc:
                self._send_json(
                    500,
                    {
                        "ok": False,
                        "error": str(exc),
                    },
                )

            return

        if self.path == "/setup/reset":
            self._send_json(
                200,
                {
                    "ok": True,
                    "setup": setup_manager.reset(),
                },
            )
            return

        if self.path == "/wifi/connect":
            try:
                payload = self._read_json_body()

                result = connect_wifi(
                    ssid=str(payload.get("ssid", "")),
                    password=str(payload.get("password", "")),
                    interface=str(
                        payload.get(
                            "interface",
                            WIFI_INTERFACE,
                        )
                    ),
                )

                status_code = 200 if result.get("ok") else 400
                self._send_json(status_code, result)

            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValueError,
            ) as exc:
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": f"Neplatný požadavek: {exc}",
                    },
                )

            except Exception as exc:
                self._send_json(
                    500,
                    {
                        "ok": False,
                        "error": str(exc),
                    },
                )

            return

        self._send_json(
            404,
            {
                "ok": False,
                "error": "Endpoint nebyl nalezen.",
            },
        )

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "TNG IQ FANDA Host API",
                    "version": "0.2.0",
                },
            )
            return

        if self.path == "/wifi/status":
            result = get_wifi_status()
            status_code = 200 if result.get("ok") else 503
            self._send_json(status_code, result)
            return

        if self.path == "/wifi/scan":
            result = scan_wifi_networks()
            status_code = 200 if result.get("ok") else 503
            self._send_json(status_code, result)
            return

        if self.path == "/setup/status":
            self._send_json(
                200,
                {
                    "ok": True,
                    "setup": setup_manager.get_status(),
                },
            )
            return

        if self.path == "/network/info":
            result = get_network_info()
            status_code = 200 if result.get("ok") else 503
            self._send_json(status_code, result)
            return

        self._send_json(
            404,
            {
                "ok": False,
                "error": "Endpoint nebyl nalezen.",
            },
        )

    def log_message(self, format: str, *args) -> None:
        print(
            f"[IQF HOST API] {self.address_string()} "
            f"{format % args}",
            flush=True,
        )


def main() -> None:
    server = ThreadingHTTPServer(
        (HOST, PORT),
        HostApiHandler,
    )

    print(
        f"[IQF HOST API] Naslouchám na http://{HOST}:{PORT}",
        flush=True,
    )

    server.serve_forever()


if __name__ == "__main__":
    main()
