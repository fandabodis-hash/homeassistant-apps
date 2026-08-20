"""Bezpecny vyrobni bootstrap noveho TNG IQ FANDA."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from factory.factory_service import create_identity
from host.identity import (
    DEFAULT_IDENTITY_PATH,
    DeviceIdentityService,
)


DEFAULT_API_BASE_URL = "https://api.tngiqfanda.cz"
NEXT_SERIAL_ENDPOINT = "/api/v1/factory/next-serial"

_BOOTSTRAP_LOCK = threading.Lock()


def _api_base_url() -> str:
    value = str(
        os.getenv(
            "IQF_API_BASE_URL",
            DEFAULT_API_BASE_URL,
        )
        or DEFAULT_API_BASE_URL
    ).strip().rstrip("/")

    return value or DEFAULT_API_BASE_URL


def request_next_serial(
    admin_token: str,
    opener: Callable[..., Any] = request.urlopen,
) -> str:
    token = str(admin_token or "").strip()

    if not token:
        raise ValueError(
            "Chybi administratorsky bearer token."
        )

    http_request = request.Request(
        f"{_api_base_url()}{NEXT_SERIAL_ENDPOINT}",
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "TNG-IQ-FANDA-Factory-Bootstrap",
        },
    )

    try:
        with opener(
            http_request,
            timeout=20,
        ) as response:
            body = response.read().decode(
                "utf-8",
                errors="strict",
            )

    except error.HTTPError as exc:
        body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        if exc.code in (401, 403):
            raise PermissionError(
                "Cloud odmitl vyrobni autorizaci."
            ) from exc

        raise RuntimeError(
            f"Cloud factory API vratil HTTP {exc.code}: {body}"
        ) from exc

    except error.URLError as exc:
        raise RuntimeError(
            f"Cloud factory API neni dostupne: {exc.reason}"
        ) from exc

    try:
        payload = json.loads(body)

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Cloud factory API vratilo neplatny JSON."
        ) from exc

    serial_number = str(
        payload.get("serial_number") or ""
    ).strip().upper()

    if not serial_number:
        raise RuntimeError(
            "Cloud factory API nevratilo seriove cislo."
        )

    return serial_number


def _safe_result(
    identity: dict[str, Any],
    created: bool,
) -> dict[str, Any]:
    return {
        "created": created,
        "serial_number": identity.get("serial_number"),
        "hostname": identity.get("hostname"),
        "model": identity.get("model"),
        "hardware_revision": identity.get(
            "hardware_revision"
        ),
        "software_version": identity.get(
            "software_version"
        ),
        "state": identity.get("state"),
    }


def bootstrap_manufacturing_identity(
    admin_token: str,
    opener: Callable[..., Any] = request.urlopen,
    identity_path: Path | None = None,
) -> dict[str, Any]:

    path = (
        Path(identity_path)
        if identity_path is not None
        else DEFAULT_IDENTITY_PATH
    )

    with _BOOTSTRAP_LOCK:

        identity_service = DeviceIdentityService(
            identity_path=path,
        )

        if identity_service.exists():
            return _safe_result(
                identity_service.load(),
                created=False,
            )

        serial_number = request_next_serial(
            admin_token=admin_token,
            opener=opener,
        )

        identity = create_identity(
            serial_number=serial_number,
            model=str(
                os.getenv(
                    "IQF_DEVICE_MODEL",
                    "IQ FANDA PI5",
                )
                or "IQ FANDA PI5"
            ).strip(),
            hardware_revision=str(
                os.getenv(
                    "IQF_HARDWARE_REVISION",
                    "Raspberry Pi 5",
                )
                or "Raspberry Pi 5"
            ).strip(),
            software_version=str(
                os.getenv(
                    "IQF_SOFTWARE_VERSION",
                    "0.1.72",
                )
                or "0.1.72"
            ).strip(),
            identity_path=path,
        )

        return _safe_result(
            identity,
            created=True,
        )