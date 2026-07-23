"""Funkce pro cteni vyrobni identity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from host.identity import (
    DEFAULT_IDENTITY_PATH,
    DeviceIdentityService,
)


def read_identity(
    identity_path: Path = DEFAULT_IDENTITY_PATH,
) -> dict[str, Any]:
    """Vrati vyrobni identitu."""

    service = DeviceIdentityService(
        identity_path=identity_path,
    )

    return service.load()