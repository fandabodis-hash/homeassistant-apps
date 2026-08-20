"""Cisty stavovy automat TNG IQ FANDA Installer V2."""

from __future__ import annotations

from installer_v2.models import (
    InstallerMode,
    InstallerState,
)


DEFAULT_SERVICE_WINDOW_SECONDS = 60


def determine_installer_state(
    *,
    cloud_identity_valid: bool,
    cloud_identity_missing: bool,
    cloud_identity_recovery: bool,
    service_window_active: bool,
    wifi_maintenance_requested: bool = False,
    service_window_seconds: int = DEFAULT_SERVICE_WINDOW_SECONDS,
) -> InstallerState:
    """
    Urci provozni rezim Installer V2.

    Funkce nema zadne vedlejsi ucinky.
    Neovlada Wi-Fi, AP, NetworkManager ani soubory zarizeni.
    """

    if cloud_identity_recovery:
        return InstallerState(
            mode=InstallerMode.RECOVERY,
            installed=False,
            access_point_required=True,
            service_window_seconds=None,
            reason="cloud_identity_requires_recovery",
        )

    if cloud_identity_missing:
        return InstallerState(
            mode=InstallerMode.FIRST_INSTALL,
            installed=False,
            access_point_required=True,
            service_window_seconds=None,
            reason="first_install",
        )

    if not cloud_identity_valid:
        return InstallerState(
            mode=InstallerMode.RECOVERY,
            installed=False,
            access_point_required=True,
            service_window_seconds=None,
            reason="unknown_identity_state",
        )

    if wifi_maintenance_requested:
        return InstallerState(
            mode=InstallerMode.WIFI_MAINTENANCE,
            installed=True,
            access_point_required=True,
            service_window_seconds=None,
            reason="wifi_maintenance_requested",
        )

    if service_window_active:
        return InstallerState(
            mode=InstallerMode.INSTALLED_BOOT_WINDOW,
            installed=True,
            access_point_required=True,
            service_window_seconds=max(
                1,
                int(service_window_seconds),
            ),
            reason="installed_service_window",
        )

    return InstallerState(
        mode=InstallerMode.INSTALLED_RUN,
        installed=True,
        access_point_required=False,
        service_window_seconds=None,
        reason="normal_operation",
    )