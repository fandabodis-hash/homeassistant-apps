"""Bezpecny navrat zarizeni do stavu READY_FOR_INSTALL."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from host.identity import identity_service
from installer.access_point_service import request_access_point
from installer.setup_manager import setup_manager


DEVICE_CONFIG_PATH = Path(
    os.getenv(
        "IQF_DEVICE_CONFIG_PATH",
        "/config/device.json",
    )
)

INSTALL_CONFIG_PATH = Path(
    os.getenv(
        "IQF_INSTALL_CONFIG_PATH",
        "/config/install.json",
    )
)

CLOUD_CONFIG_PATH = Path(
    os.getenv(
        "IQF_CLOUD_CONFIG_PATH",
        "/config/cloud-config.json",
    )
)


def _ulozit_vyrobni_identitu(data: dict[str, Any]) -> None:
    """Atomicky ulozi zachovanou vyrobni identitu."""

    identity_path = identity_service.identity_path
    identity_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=identity_path.parent,
            prefix=f".{identity_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(
                data,
                temporary_file,
                ensure_ascii=False,
                indent=2,
            )
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)

        temporary_path.replace(identity_path)

    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def provest_factory_reset() -> dict[str, Any]:
    """
    Odstrani zakaznickou a cloudovou registraci.

    Trvala vyrobni identita a seriove cislo zustanou zachovany.
    """

    manufacturing_identity = identity_service.load()

    if not manufacturing_identity.get("identity_exists"):
        raise RuntimeError(
            "Factory Reset nelze provest bez vyrobni identity zarizeni."
        )

    serial_number = str(
        manufacturing_identity.get("serial_number") or ""
    ).strip()

    if not serial_number:
        raise RuntimeError(
            "Vyrobni identita neobsahuje seriove cislo."
        )

    preserved_identity = {
        key: value
        for key, value in manufacturing_identity.items()
        if key != "identity_exists"
    }
    preserved_identity["serial_number"] = serial_number
    preserved_identity["state"] = "READY_FOR_INSTALL"

    _ulozit_vyrobni_identitu(preserved_identity)

    removed_files: list[str] = []

    for path in (
        DEVICE_CONFIG_PATH,
        INSTALL_CONFIG_PATH,
        CLOUD_CONFIG_PATH,
    ):
        if path.exists():
            path.unlink(missing_ok=True)
            removed_files.append(str(path))

    installer_status = setup_manager.reset()

    access_point_result = request_access_point(
        reason="factory_reset",
    )

    return {
        "ok": True,
        "state": "READY_FOR_INSTALL",
        "serial_number": serial_number,
        "removed_files": removed_files,
        "manufacturing_identity_path": str(
            identity_service.identity_path
        ),
        "installer": installer_status,
        "access_point": access_point_result,
        "restart_required": True,
        "message": (
            "Factory Reset byl pripraven. "
            "Pro ukonceni bezicich cloudovych sluzeb "
            "restartujte TNG IQ FANDA Agent."
        ),
    }

