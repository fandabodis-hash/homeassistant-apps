"""Vyrobni identita zarizeni TNG IQ FANDA."""

from __future__ import annotations

import json
import os
import platform
import re
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from host.identity import (
    DEFAULT_IDENTITY_PATH,
    DeviceIdentityService,
)


SERIAL_PATTERN = re.compile(r"^F800711-TNG-\d{5}$")


def normalize_serial_number(serial_number: str) -> str:
    """Normalizuje a overi vyrobni seriove cislo."""

    normalized = str(serial_number or "").strip().upper()

    if not SERIAL_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Seriove cislo musi mit format "
            "F800711-TNG-NNNNN, například "
            "F800711-TNG-00004."
        )

    return normalized


def read_raspberry_pi_serial() -> str | None:
    """Nacte hardwarove seriove cislo Raspberry Pi."""

    cpuinfo_path = Path("/proc/cpuinfo")

    if not cpuinfo_path.is_file():
        return None

    try:
        lines = cpuinfo_path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()

        for line in lines:
            if line.lower().startswith("serial"):
                _, value = line.split(":", 1)
                serial = value.strip()
                return serial or None

    except OSError:
        return None

    return None


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    """Atomicky ulozi JSON a omezi pristupova prava."""

    path.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary_path, path)
        os.chmod(path, 0o600)

    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def build_manufacturing_identity(
    serial_number: str,
    model: str = "IQ FANDA PI5",
    hardware_revision: str = "Raspberry Pi 5",
    software_version: str = "0.1.14",
) -> dict[str, Any]:
    """Sestavi novou trvalou vyrobni identitu."""

    normalized_serial = normalize_serial_number(serial_number)
    serial_suffix = normalized_serial.rsplit("-", 1)[-1]

    return {
        "manufacturer": "TNG-Air",
        "product": "TNG IQ FANDA",
        "serial_number": normalized_serial,
        "provisioning_id": str(uuid.uuid4()),
        "model": str(model).strip(),
        "hardware_revision": str(
            hardware_revision
        ).strip(),
        "software_version": str(
            software_version
        ).strip(),
        "production_date": date.today().isoformat(),
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "factory_version": "1.0",
        "hostname": f"iqf-{serial_suffix}",
        "state": "UNPROVISIONED",
        "hardware_id": read_raspberry_pi_serial(),
        "platform": platform.platform(),
    }


def create_manufacturing_identity(
    serial_number: str,
    model: str = "IQ FANDA PI5",
    hardware_revision: str = "Raspberry Pi 5",
    software_version: str = "0.1.14",
    identity_path: Path = DEFAULT_IDENTITY_PATH,
) -> dict[str, Any]:
    """
    Vytvori trvalou vyrobni identitu.

    Existujici identita se nikdy automaticky neprepise.
    """

    identity_service = DeviceIdentityService(
        identity_path=identity_path,
    )

    if identity_service.exists():
        existing_identity = identity_service.load()

        raise FileExistsError(
            "Vyrobni identita jiz existuje. "
            "Seriove cislo: "
            f"{existing_identity.get('serial_number')}. "
            f"Cesta: {identity_path}"
        )

    identity = build_manufacturing_identity(
        serial_number=serial_number,
        model=model,
        hardware_revision=hardware_revision,
        software_version=software_version,
    )

    atomic_write_json(
        path=Path(identity_path),
        payload=identity,
    )

    return identity
