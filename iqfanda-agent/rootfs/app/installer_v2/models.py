"""Datove modely TNG IQ FANDA Installer V2."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InstallerMode(str, Enum):
    """Provozni rezimy Installer V2."""

    FIRST_INSTALL = "first_install"
    INSTALLED_BOOT_WINDOW = "installed_boot_window"
    INSTALLED_RUN = "installed_run"
    WIFI_MAINTENANCE = "wifi_maintenance"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class InstallerState:
    """Vysledek stavoveho automatu."""

    mode: InstallerMode
    installed: bool
    access_point_required: bool
    service_window_seconds: int | None
    reason: str