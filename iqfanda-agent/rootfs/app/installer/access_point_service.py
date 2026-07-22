"""Integracni rozhrani pro instalacni Access Point TNG IQ FANDA.

Tento modul neprovadi zadne sitove operace.
Pouze atomicky zapisuje pozadavek pro iqfanda-host-agent.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_REQUEST_PATH = Path(
    os.environ.get(
        "IQF_ACCESS_POINT_REQUEST_PATH",
        "/homeassistant/iqf/runtime/access_point_request.yaml",
    )
)

DEFAULT_SSID = "TNG_IQ_FANDA"
DEFAULT_ADDRESS = "192.168.4.1/24"
DEFAULT_PORTAL_URL = "http://192.168.4.1:8099"


def _atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    """Atomicky ulozi JSON, ktery je zaroven platnym YAML dokumentem."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )

    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as temporary_file:
            json.dump(
                payload,
                temporary_file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(
            temporary_path,
            path,
        )

    except Exception:
        temporary_path.unlink(
            missing_ok=True,
        )
        raise


def write_access_point_request(
    requested: bool,
    reason: str,
    *,
    ssid: str = DEFAULT_SSID,
    address: str = DEFAULT_ADDRESS,
    portal_url: str = DEFAULT_PORTAL_URL,
    request_path: Path | None = None,
) -> dict[str, Any]:
    """Zapise pozadovany stav instalacniho Access Pointu."""

    target_path = request_path or DEFAULT_REQUEST_PATH

    reason = str(reason or "").strip()
    ssid = str(ssid or "").strip()
    address = str(address or "").strip()
    portal_url = str(portal_url or "").strip()

    if not reason:
        raise ValueError("Duvod pozadavku na Access Point nesmi byt prazdny.")

    if not ssid:
        raise ValueError("SSID instalacniho Access Pointu nesmi byt prazdne.")

    if not address:
        raise ValueError("Adresa instalacniho Access Pointu nesmi byt prazdna.")

    if not portal_url:
        raise ValueError("URL instalacniho portalu nesmi byt prazdna.")

    payload: dict[str, Any] = {
        "requested": bool(requested),
        "reason": reason,
        "ssid": ssid,
        "address": address,
        "portal_url": portal_url,
    }

    _atomic_write_json(
        target_path,
        payload,
    )

    return {
        "ok": True,
        "path": str(target_path),
        "request": payload,
    }


def request_access_point(
    reason: str,
    *,
    request_path: Path | None = None,
) -> dict[str, Any]:
    """Pozada Host Agent o spusteni instalacniho AP."""

    return write_access_point_request(
        requested=True,
        reason=reason,
        request_path=request_path,
    )


def release_access_point(
    reason: str,
    *,
    request_path: Path | None = None,
) -> dict[str, Any]:
    """Pozada Host Agent o ukonceni instalacniho AP."""

    return write_access_point_request(
        requested=False,
        reason=reason,
        request_path=request_path,
    )
