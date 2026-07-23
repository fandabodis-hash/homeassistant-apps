from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_CLOUD_API_URL = "https://api.iqfanda.cz"
DEFAULT_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class CloudClientConfig:
    """
    Konfigurace komunikace s IQ FANDA Cloud.
    """

    base_url: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "CloudClientConfig":
        base_url = os.getenv(
            "IQF_CLOUD_API_URL",
            DEFAULT_CLOUD_API_URL,
        ).strip()

        if not base_url:
            base_url = DEFAULT_CLOUD_API_URL

        timeout_raw = os.getenv(
            "IQF_CLOUD_TIMEOUT_SECONDS",
            str(DEFAULT_TIMEOUT_SECONDS),
        )

        try:
            timeout_seconds = int(timeout_raw)
        except (TypeError, ValueError):
            timeout_seconds = DEFAULT_TIMEOUT_SECONDS

        timeout_seconds = max(3, min(timeout_seconds, 120))

        return cls(
            base_url=base_url.rstrip("/"),
            timeout_seconds=timeout_seconds,
        )


class IQFCloudClient:
    """
    HTTP klient pro komunikaci Host Agentu s IQ FANDA Cloud.

    Tento modul neřídí onboarding. Pouze provádí jednotlivé
    cloudové požadavky a vrací sjednocený výsledek.
    """

    def __init__(
        self,
        config: CloudClientConfig | None = None,
    ) -> None:
        self.config = (
            config
            if config is not None
            else CloudClientConfig.from_environment()
        )

    def _build_url(self, path: str) -> str:
        normalized_path = "/" + str(path or "").lstrip("/")
        return f"{self.config.base_url}{normalized_path}"

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Odešle JSON požadavek a vrátí sjednocený slovník.

        Úspěch:

        {
            "ok": True,
            "status_code": 200,
            "data": {...}
        }

        Chyba:

        {
            "ok": False,
            "status_code": 400,
            "error": "...",
            "data": {...}
        }
        """
        url = self._build_url(path)

        request_headers = {
            "Accept": "application/json",
            "User-Agent": "TNG-IQ-FANDA-Host-Agent",
        }

        if headers:
            request_headers.update(headers)

        body: bytes | None = None

        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
            ).encode("utf-8")

            request_headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            url=url,
            data=body,
            headers=request_headers,
            method=method.upper(),
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.config.timeout_seconds,
            ) as response:
                status_code = int(response.status)
                response_body = response.read().decode(
                    "utf-8",
                    errors="replace",
                )

                data = self._decode_json(response_body)

                return {
                    "ok": 200 <= status_code < 300,
                    "status_code": status_code,
                    "data": data,
                    "error": None,
                }

        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode(
                "utf-8",
                errors="replace",
            )

            data = self._decode_json(response_body)
            error = self._extract_error(
                data=data,
                fallback=f"Cloud vrátil HTTP {exc.code}.",
            )

            return {
                "ok": False,
                "status_code": int(exc.code),
                "data": data,
                "error": error,
            }

        except urllib.error.URLError as exc:
            return {
                "ok": False,
                "status_code": None,
                "data": None,
                "error": (
                    "Cloud není dostupný: "
                    f"{getattr(exc, 'reason', exc)}"
                ),
            }

        except TimeoutError:
            return {
                "ok": False,
                "status_code": None,
                "data": None,
                "error": "Vypršel časový limit komunikace s cloudem.",
            }

        except Exception as exc:
            return {
                "ok": False,
                "status_code": None,
                "data": None,
                "error": f"Neočekávaná chyba cloudového klienta: {exc}",
            }

    @staticmethod
    def _decode_json(response_body: str) -> Any:
        if not response_body:
            return None

        try:
            return json.loads(response_body)
        except json.JSONDecodeError:
            return {
                "raw_response": response_body,
            }

    @staticmethod
    def _extract_error(
        data: Any,
        fallback: str,
    ) -> str:
        if isinstance(data, dict):
            for key in (
                "detail",
                "error",
                "message",
            ):
                value = data.get(key)

                if isinstance(value, str) and value.strip():
                    return value.strip()

                if value is not None:
                    return str(value)

        return fallback

    def register_device(
        self,
        *,
        provisioning_id: str,
        first_name: str,
        last_name: str,
        email: str,
        password: str,
        site_name: str,
        site_type: str,
        device_serial_number: str,
        device_name: str = "TNG IQ FANDA",
        software_version: str | None = None,
    ) -> dict[str, Any]:
        """
        Zaregistruje uživatele, objekt a zařízení v IQ FANDA Cloud.

        Citlivé údaje se neukládají do instance klienta.
        Heslo se použije pouze pro jeden registrační požadavek.
        """
        payload: dict[str, Any] = {
            "provisioning_id": str(provisioning_id).strip(),
            "first_name": str(first_name).strip(),
            "last_name": str(last_name).strip(),
            "email": str(email).strip().lower(),
            "password": str(password),
            "site_name": str(site_name).strip(),
            "site_type": (
                str(site_type or "house").strip()
                or "house"
            ),
            "device_serial_number": (
                str(device_serial_number).strip().upper()
            ),
            "device_name": (
                str(device_name or "TNG IQ FANDA").strip()
                or "TNG IQ FANDA"
            ),
            "software_version": (
                str(software_version).strip()
                if software_version is not None
                else None
            ),
        }

        required_fields = (
            "provisioning_id",
            "first_name",
            "last_name",
            "email",
            "password",
            "site_name",
            "device_serial_number",
            "device_name",
        )

        missing_fields = [
            field_name
            for field_name in required_fields
            if not payload.get(field_name)
        ]

        if missing_fields:
            return {
                "ok": False,
                "status_code": None,
                "data": None,
                "error": (
                    "Chybí povinné registrační údaje: "
                    + ", ".join(missing_fields)
                ),
            }

        if len(payload["password"]) < 10:
            return {
                "ok": False,
                "status_code": None,
                "data": None,
                "error": (
                    "Heslo cloudového účtu musí mít "
                    "alespoň 10 znaků."
                ),
            }

        if len(payload["device_serial_number"]) < 3:
            return {
                "ok": False,
                "status_code": None,
                "data": None,
                "error": (
                    "Sériové číslo zařízení musí mít "
                    "alespoň 3 znaky."
                ),
            }

        return self._request_json(
            method="POST",
            path="/api/v1/provisioning/register",
            payload=payload,
        )

    @staticmethod
    def _device_authorization_headers(
        device_token: str,
    ) -> dict[str, str]:
        """
        Vytvoří autorizační hlavičku zařízení.
        """
        normalized_token = str(device_token or "").strip()

        if not normalized_token:
            raise ValueError("Device token nesmí být prázdný.")

        return {
            "Authorization": f"Bearer {normalized_token}",
        }

    def heartbeat(
        self,
        *,
        device_uuid: str,
        device_token: str,
        software_version: str | None = None,
        uptime_seconds: int | None = None,
        cpu_usage_percent: float | None = None,
        cpu_temperature_celsius: float | None = None,
        memory_usage_percent: float | None = None,
        memory_total_bytes: int | None = None,
        memory_used_bytes: int | None = None,
        disk_usage_percent: float | None = None,
        disk_total_bytes: int | None = None,
        disk_used_bytes: int | None = None,
        internet_connected: bool | None = None,
        home_assistant_running: bool | None = None,
        home_assistant_version: str | None = None,
        ip_address: str | None = None,
        mac_address: str | None = None,
        active_modules_count: int | None = None,
        last_sync_at: str | None = None,
    ) -> dict[str, Any]:
        """
        Odešle stav zařízení do IQ FANDA Cloud.

        Device token se používá pouze v HTTP hlavičce
        a neukládá se do instance klienta.
        """
        normalized_uuid = str(device_uuid or "").strip()

        if not normalized_uuid:
            return {
                "ok": False,
                "status_code": None,
                "data": None,
                "error": "Device UUID nesmí být prázdné.",
            }

        try:
            headers = self._device_authorization_headers(
                device_token=device_token,
            )
        except ValueError as exc:
            return {
                "ok": False,
                "status_code": None,
                "data": None,
                "error": str(exc),
            }

        payload: dict[str, Any] = {
            "device_uuid": normalized_uuid,
        }

        optional_fields: dict[str, Any] = {
            "software_version": software_version,
            "uptime_seconds": uptime_seconds,
            "cpu_usage_percent": cpu_usage_percent,
            "cpu_temperature_celsius": cpu_temperature_celsius,
            "memory_usage_percent": memory_usage_percent,
            "memory_total_bytes": memory_total_bytes,
            "memory_used_bytes": memory_used_bytes,
            "disk_usage_percent": disk_usage_percent,
            "disk_total_bytes": disk_total_bytes,
            "disk_used_bytes": disk_used_bytes,
            "internet_connected": internet_connected,
            "home_assistant_running": home_assistant_running,
            "home_assistant_version": home_assistant_version,
            "ip_address": ip_address,
            "mac_address": mac_address,
            "active_modules_count": active_modules_count,
            "last_sync_at": last_sync_at,
        }

        for field_name, value in optional_fields.items():
            if value is not None:
                payload[field_name] = value

        return self._request_json(
            method="POST",
            path="/api/v1/device/heartbeat",
            payload=payload,
            headers=headers,
        )

    def get_device_configuration(
        self,
        *,
        device_token: str,
    ) -> dict[str, Any]:
        """
        Stáhne aktuální konfiguraci zařízení z IQ FANDA Cloud.
        """
        try:
            headers = self._device_authorization_headers(
                device_token=device_token,
            )
        except ValueError as exc:
            return {
                "ok": False,
                "status_code": None,
                "data": None,
                "error": str(exc),
            }

        return self._request_json(
            method="GET",
            path="/api/v1/device/config",
            headers=headers,
        )

    def health(self) -> dict[str, Any]:
        """
        Ověří dostupnost veřejného health endpointu cloudu.
        """
        return self._request_json(
            method="GET",
            path="/health",
        )


cloud_client = IQFCloudClient()
