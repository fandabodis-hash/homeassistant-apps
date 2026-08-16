"""Factory Service pro výrobu zařízení TNG IQ FANDA."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from manufacturing import create_manufacturing_identity


def create_identity(
    serial_number: str,
    model: str = "IQ FANDA PI5",
    hardware_revision: str = "Raspberry Pi 5",
    software_version: str = "0.1.62",
    identity_path: Path | None = None,
) -> dict[str, Any]:
    """Vytvoří výrobní identitu zařízení."""

    kwargs: dict[str, Any] = {
        "serial_number": serial_number,
        "model": model,
        "hardware_revision": hardware_revision,
        "software_version": software_version,
    }

    if identity_path is not None:
        kwargs["identity_path"] = identity_path

    return create_manufacturing_identity(**kwargs)