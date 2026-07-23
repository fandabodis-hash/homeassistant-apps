"""Rizeni instalacniho Access Pointu TNG IQ FANDA."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from installer.network_manager import run_nmcli


DEFAULT_REQUEST_PATH = Path(
    os.environ.get(
        "IQF_ACCESS_POINT_REQUEST_PATH",
        "/homeassistant/iqf/runtime/access_point_request.yaml",
    )
)

DEFAULT_INTERFACE = "wlan0"
DEFAULT_PROFILE_NAME = "IQF Installer AP"
DEFAULT_CHECK_INTERVAL_SECONDS = 2.0


CommandRunner = Callable[..., Any]


class AccessPointManager:
    """Zpracovava pozadavky na instalacni Access Point."""

    def __init__(
        self,
        request_path: Path = DEFAULT_REQUEST_PATH,
        interface: str = DEFAULT_INTERFACE,
        profile_name: str = DEFAULT_PROFILE_NAME,
        command_runner: CommandRunner = run_nmcli,
        check_interval_seconds: float = (
            DEFAULT_CHECK_INTERVAL_SECONDS
        ),
    ) -> None:
        self.request_path = Path(request_path)
        self.interface = str(interface).strip() or DEFAULT_INTERFACE
        self.profile_name = (
            str(profile_name).strip() or DEFAULT_PROFILE_NAME
        )
        self.command_runner = command_runner
        self.check_interval_seconds = max(
            0.2,
            float(check_interval_seconds),
        )

        self._lock = threading.Lock()
        self._active = False
        self._last_request: dict[str, Any] | None = None
        self._last_error: str | None = None

    def get_status(self) -> dict[str, Any]:
        """Vrati aktualni stav spravce Access Pointu."""

        with self._lock:
            return {
                "ok": self._last_error is None,
                "active": self._active,
                "interface": self.interface,
                "profile_name": self.profile_name,
                "request_path": str(self.request_path),
                "last_request": self._last_request,
                "error": self._last_error,
            }

    def load_request(self) -> dict[str, Any] | None:
        """Nacte pozadavek ulozeny jako JSON kompatibilni s YAML."""

        if not self.request_path.is_file():
            return None

        raw_text = self.request_path.read_text(
            encoding="utf-8-sig",
        )

        payload = json.loads(raw_text)

        if not isinstance(payload, dict):
            raise ValueError(
                "Pozadavek na Access Point musi byt JSON objekt."
            )

        required_fields = (
            "requested",
            "reason",
            "ssid",
            "address",
            "portal_url",
        )

        missing_fields = [
            field
            for field in required_fields
            if field not in payload
        ]

        if missing_fields:
            raise ValueError(
                "V pozadavku na Access Point chybi pole: "
                + ", ".join(missing_fields)
            )

        return {
            "requested": bool(payload["requested"]),
            "reason": str(payload["reason"] or "").strip(),
            "ssid": str(payload["ssid"] or "").strip(),
            "address": str(payload["address"] or "").strip(),
            "portal_url": str(
                payload["portal_url"] or ""
            ).strip(),
        }

    @staticmethod
    def _command_error(
        result: Any,
        fallback: str,
    ) -> str:
        stderr = str(
            getattr(result, "stderr", "") or ""
        ).strip()

        stdout = str(
            getattr(result, "stdout", "") or ""
        ).strip()

        return stderr or stdout or fallback

    def is_access_point_active(self) -> bool:
        """Overi skutecny stav AP primo v NetworkManageru."""

        result = self.command_runner(
            "-t",
            "-f",
            "NAME",
            "connection",
            "show",
            "--active",
        )

        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError(
                self._command_error(
                    result,
                    "Nepodarilo se overit stav Access Pointu.",
                )
            )

        active_profiles = {
            line.strip()
            for line in str(
                getattr(result, "stdout", "") or ""
            ).splitlines()
            if line.strip()
        }

        return self.profile_name in active_profiles

    def start_access_point(
        self,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Vytvori a aktivuje instalacni Access Point."""

        ssid = str(
            request_payload.get("ssid") or ""
        ).strip()

        address = str(
            request_payload.get("address") or ""
        ).strip()

        if not ssid:
            raise ValueError(
                "SSID instalacniho Access Pointu nesmi byt prazdne."
            )

        if not address:
            raise ValueError(
                "IP adresa instalacniho Access Pointu nesmi byt prazdna."
            )

        self.command_runner(
            "connection",
            "down",
            self.profile_name,
        )

        self.command_runner(
            "connection",
            "delete",
            self.profile_name,
        )

        add_result = self.command_runner(
            "connection",
            "add",
            "type",
            "wifi",
            "ifname",
            self.interface,
            "con-name",
            self.profile_name,
            "ssid",
            ssid,
        )

        if getattr(add_result, "returncode", 1) != 0:
            raise RuntimeError(
                self._command_error(
                    add_result,
                    "Nepodarilo se vytvorit profil Access Pointu.",
                )
            )

        modify_result = self.command_runner(
            "connection",
            "modify",
            self.profile_name,
            "802-11-wireless.mode",
            "ap",
            "802-11-wireless.band",
            "bg",
            "ipv4.method",
            "manual",
            "ipv4.addresses",
            address,
            "ipv6.method",
            "disabled",
            "connection.autoconnect",
            "no",
        )

        if getattr(modify_result, "returncode", 1) != 0:
            self.command_runner(
                "connection",
                "delete",
                self.profile_name,
            )

            raise RuntimeError(
                self._command_error(
                    modify_result,
                    "Nepodarilo se nastavit profil Access Pointu.",
                )
            )

        up_result = self.command_runner(
            "connection",
            "up",
            self.profile_name,
        )

        if getattr(up_result, "returncode", 1) != 0:
            raise RuntimeError(
                self._command_error(
                    up_result,
                    "Nepodarilo se aktivovat Access Point.",
                )
            )

        with self._lock:
            self._active = True
            self._last_error = None

        return {
            "ok": True,
            "active": True,
            "ssid": ssid,
            "address": address,
            "interface": self.interface,
            "profile_name": self.profile_name,
        }

    def stop_access_point(self) -> dict[str, Any]:
        """Deaktivuje a odstrani instalacni Access Point."""

        down_result = self.command_runner(
            "connection",
            "down",
            self.profile_name,
        )

        delete_result = self.command_runner(
            "connection",
            "delete",
            self.profile_name,
        )

        down_code = getattr(
            down_result,
            "returncode",
            1,
        )
        delete_code = getattr(
            delete_result,
            "returncode",
            1,
        )

        if down_code != 0 and delete_code != 0:
            raise RuntimeError(
                self._command_error(
                    delete_result,
                    self._command_error(
                        down_result,
                        "Nepodarilo se ukoncit Access Point.",
                    ),
                )
            )

        with self._lock:
            self._active = False
            self._last_error = None

        return {
            "ok": True,
            "active": False,
            "interface": self.interface,
            "profile_name": self.profile_name,
        }

    def reconcile(self) -> dict[str, Any]:
        """Sjednoti skutecny stav AP s ulozenym pozadavkem."""

        request_payload = self.load_request()

        if request_payload is None:
            return {
                "ok": True,
                "changed": False,
                "message": (
                    "Pozadavek na Access Point zatim neexistuje."
                ),
                "status": self.get_status(),
            }

        actual_active = self.is_access_point_active()

        with self._lock:
            previous_request = self._last_request
            self._active = actual_active

        is_active = actual_active
        requested = request_payload["requested"]

        request_changed = request_payload != previous_request

        if requested and (not is_active or request_changed):
            result = self.start_access_point(
                request_payload=request_payload,
            )
            changed = True

        elif not requested and is_active:
            result = self.stop_access_point()
            changed = True

        else:
            result = {
                "ok": True,
                "active": is_active,
            }
            changed = False

        with self._lock:
            self._last_request = request_payload
            self._last_error = None

        return {
            "ok": True,
            "changed": changed,
            "result": result,
            "status": self.get_status(),
        }

    def run_forever(self) -> None:
        """Prubezne zpracovava pozadavky na Access Point."""

        logging.info(
            "Access Point Manager sleduje pozadavky v %s.",
            self.request_path,
        )

        while True:
            try:
                self.reconcile()

            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)

                logging.exception(
                    "Zpracovani pozadavku na Access Point selhalo."
                )

            time.sleep(self.check_interval_seconds)


access_point_manager = AccessPointManager()
