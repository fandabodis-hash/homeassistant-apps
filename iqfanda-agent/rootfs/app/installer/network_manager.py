"""Sitove funkce Installeru TNG IQ FANDA."""

import json
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WIFI_INTERFACE = "wlan0"


def run_nmcli(*args: str) -> subprocess.CompletedProcess[str]:
    """
    Spusti prikaz NetworkManageru.

    Na Raspberry Pi se pouzije skutecne nmcli.
    Pokud nmcli neni dostupne, vrati se rizeny vysledek
    misto padu celeho Installer API.
    """

    command = ["nmcli", *args]

    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=command,
            returncode=127,
            stdout="",
            stderr=(
                "NetworkManager (nmcli) neni v tomto "
                "prostredi dostupny."
            ),
        )

    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=command,
            returncode=124,
            stdout="",
            stderr="Prikaz nmcli prekrocil casovy limit.",
        )

    except OSError as exc:
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr=f"Prikaz nmcli se nepodarilo spustit: {exc}",
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
