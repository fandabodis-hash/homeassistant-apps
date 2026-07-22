"""Lokalni HTTP API Installeru TNG IQ FANDA."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from installer.installer_service import (
    pripojit_wifi,
    resetovat_instalaci,
    spustit_instalaci,
    vyhledat_wifi_site,
    ziskat_stav_instalace,
    ziskat_stav_wifi,
)


VYCHOZI_HOST = "0.0.0.0"
VYCHOZI_PORT = 8099

POVINNA_POLE_INSTALACE = (
    "ssid",
    "password",
    "provisioning_id",
    "first_name",
    "last_name",
    "email",
    "account_password",
    "site_name",
    "site_type",
)


class InstallerApiHandler(BaseHTTPRequestHandler):
    """Obsluha lokalniho HTTP API Installeru."""

    server_version = "TNG-IQ-FANDA-Installer/0.1"

    def _odeslat_json(
        self,
        status: HTTPStatus | int,
        data: dict[str, Any],
    ) -> None:
        telo = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(telo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(telo)

    def _nacist_json(self) -> dict[str, Any]:
        delka_text = self.headers.get("Content-Length", "0")

        try:
            delka = int(delka_text)
        except ValueError as chyba:
            raise ValueError("Neplatna delka HTTP pozadavku.") from chyba

        if delka <= 0:
            return {}

        surova_data = self.rfile.read(delka)

        try:
            data = json.loads(surova_data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as chyba:
            raise ValueError("Telo pozadavku neni platny JSON.") from chyba

        if not isinstance(data, dict):
            raise ValueError("JSON pozadavku musi byt objekt.")

        return data

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._odeslat_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "iqfanda-installer",
                },
            )
            return

        if self.path == "/api/installer/status":
            self._odeslat_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "installer": ziskat_stav_instalace(),
                },
            )
            return

        if self.path == "/api/wifi/status":
            vysledek = ziskat_stav_wifi()
            status = (
                HTTPStatus.OK
                if vysledek.get("ok")
                else HTTPStatus.SERVICE_UNAVAILABLE
            )

            self._odeslat_json(
                status,
                vysledek,
            )
            return

        self._odeslat_json(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "error": "Endpoint nebyl nalezen.",
            },
        )

    def do_POST(self) -> None:
        try:
            if self.path == "/api/wifi/scan":
                vysledek = vyhledat_wifi_site()
                status = (
                    HTTPStatus.OK
                    if vysledek.get("ok")
                    else HTTPStatus.SERVICE_UNAVAILABLE
                )

                self._odeslat_json(
                    status,
                    vysledek,
                )
                return

            if self.path == "/api/wifi/connect":
                data = self._nacist_json()

                ssid = data.get("ssid")
                password = data.get("password")
                interface = data.get("interface", "wlan0")

                if not isinstance(ssid, str) or not ssid.strip():
                    self._odeslat_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "ok": False,
                            "error": "Chybi platne SSID.",
                        },
                    )
                    return

                if not isinstance(password, str):
                    self._odeslat_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "ok": False,
                            "error": "Heslo Wi-Fi musi byt text.",
                        },
                    )
                    return

                if not isinstance(interface, str) or not interface.strip():
                    self._odeslat_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "ok": False,
                            "error": "Sitove rozhrani neni platne.",
                        },
                    )
                    return

                vysledek = pripojit_wifi(
                    ssid=ssid,
                    password=password,
                    interface=interface,
                )

                status = (
                    HTTPStatus.OK
                    if vysledek.get("ok")
                    else HTTPStatus.BAD_GATEWAY
                )

                self._odeslat_json(
                    status,
                    vysledek,
                )
                return

            if self.path == "/api/installer/start":
                data = self._nacist_json()

                chybejici_pole = [
                    pole
                    for pole in POVINNA_POLE_INSTALACE
                    if not isinstance(data.get(pole), str)
                    or not data[pole].strip()
                ]

                if chybejici_pole:
                    self._odeslat_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "ok": False,
                            "error": "Chybi povinna pole.",
                            "fields": chybejici_pole,
                        },
                    )
                    return

                vysledek = spustit_instalaci(
                    ssid=data["ssid"],
                    password=data["password"],
                    provisioning_id=data["provisioning_id"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                    email=data["email"],
                    account_password=data["account_password"],
                    site_name=data["site_name"],
                    site_type=data["site_type"],
                    device_serial_number=data.get("device_serial_number"),
                    device_name=data.get(
                        "device_name",
                        "TNG IQ FANDA",
                    ),
                    software_version=data.get("software_version"),
                    interface=data.get("interface", "wlan0"),
                )

                self._odeslat_json(
                    HTTPStatus.ACCEPTED,
                    {
                        "ok": True,
                        "installer": vysledek,
                    },
                )
                return

            if self.path == "/api/installer/reset":
                vysledek = resetovat_instalaci()

                self._odeslat_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "installer": vysledek,
                    },
                )
                return

            self._odeslat_json(
                HTTPStatus.NOT_FOUND,
                {
                    "ok": False,
                    "error": "Endpoint nebyl nalezen.",
                },
            )

        except ValueError as chyba:
            self._odeslat_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error": str(chyba),
                },
            )

        except Exception as chyba:
            self._odeslat_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "error": "Interni chyba Installeru.",
                    "detail": str(chyba),
                },
            )

    def log_message(
        self,
        format: str,
        *args: Any,
    ) -> None:
        print(
            f"[installer-api] {self.address_string()} "
            f"- {format % args}",
            flush=True,
        )


def vytvorit_server(
    host: str = VYCHOZI_HOST,
    port: int = VYCHOZI_PORT,
) -> ThreadingHTTPServer:
    """Vytvori HTTP server bez zahajeni obsluhy."""

    return ThreadingHTTPServer(
        (host, port),
        InstallerApiHandler,
    )


def spustit_api(
    host: str = VYCHOZI_HOST,
    port: int = VYCHOZI_PORT,
) -> None:
    """Spusti blokujici lokalni HTTP API."""

    server = vytvorit_server(host=host, port=port)

    print(
        f"Installer API spusteno na http://{host}:{server.server_port}",
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    spustit_api()



