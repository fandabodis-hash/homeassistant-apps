"""Zavazny AP workflow TNG IQ FANDA Installer V2.

Tento modul je cista logika.

Neovlada:
- Access Point,
- NetworkManager,
- cloud,
- vyrobni identitu,
- soubory zarizeni.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from installer_v2.models import InstallerMode


SERVICE_WINDOW_SECONDS = 60


class TypPripojeni(str, Enum):
    """Typ internetoveho pripojeni pri prvni instalaci."""

    ETHERNET = "ethernet"
    WIFI = "wifi"


class AkceInstalateru(str, Enum):
    """Akce povolene v jednotlivych rezimech."""

    DOKONCIT_PRVNI_INSTALACI = "complete_first_install"
    ZMENIT_WIFI = "change_wifi"
    POTVRDIT_NOVOU_WIFI = "confirm_new_wifi"
    ZRUSIT_ZMENU_WIFI = "cancel_wifi_change"


def konfigurace_ap_pro_rezim(
    mode: InstallerMode,
) -> dict[str, Any]:
    """Vrati zavaznou konfiguraci AP UI pro dany rezim."""

    if mode == InstallerMode.FIRST_INSTALL:
        return {
            "page": "first_install",
            "access_point_enabled": True,
            "timeout_seconds": None,
            "countdown_visible": False,
            "allowed_actions": [
                AkceInstalateru.DOKONCIT_PRVNI_INSTALACI.value,
            ],
            "fields": [
                "customer_name",
                "email",
                "connection_type",
            ],
            "technical_fields_visible": False,
            "serial_number_editable": False,
            "cloud_password_visible": False,
        }

    if mode == InstallerMode.INSTALLED_BOOT_WINDOW:
        return {
            "page": "installed_service",
            "access_point_enabled": True,
            "timeout_seconds": SERVICE_WINDOW_SECONDS,
            "countdown_visible": True,
            "allowed_actions": [
                AkceInstalateru.ZMENIT_WIFI.value,
            ],
            "fields": [],
            "technical_fields_visible": False,
            "serial_number_editable": False,
            "cloud_password_visible": False,
        }

    if mode == InstallerMode.WIFI_MAINTENANCE:
        return {
            "page": "wifi_maintenance",
            "access_point_enabled": True,
            "timeout_seconds": None,
            "countdown_visible": False,
            "allowed_actions": [
                AkceInstalateru.POTVRDIT_NOVOU_WIFI.value,
                AkceInstalateru.ZRUSIT_ZMENU_WIFI.value,
            ],
            "fields": [
                "ssid",
                "wifi_password",
            ],
            "technical_fields_visible": False,
            "serial_number_editable": False,
            "cloud_password_visible": False,
        }

    if mode == InstallerMode.RECOVERY:
        return {
            "page": "recovery",
            "access_point_enabled": True,
            "timeout_seconds": None,
            "countdown_visible": False,
            "allowed_actions": [],
            "fields": [],
            "technical_fields_visible": False,
            "serial_number_editable": False,
            "cloud_password_visible": False,
            "automatic_serial_allocation": False,
        }

    return {
        "page": "normal_operation",
        "access_point_enabled": False,
        "timeout_seconds": None,
        "countdown_visible": False,
        "allowed_actions": [],
        "fields": [],
        "technical_fields_visible": False,
        "serial_number_editable": False,
        "cloud_password_visible": False,
    }


def pole_prvni_instalace(
    connection_type: TypPripojeni,
) -> list[str]:
    """Vrati pole zobrazena v jednoduchem AP formulari."""

    fields = [
        "customer_name",
        "email",
        "connection_type",
    ]

    if connection_type == TypPripojeni.WIFI:
        fields.extend([
            "ssid",
            "wifi_password",
        ])

    return fields


def validuj_prvni_instalaci(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Validuje pouze udaje, ktere smi instalater zadat."""

    allowed_fields = {
        "customer_name",
        "email",
        "connection_type",
        "ssid",
        "wifi_password",
    }

    forbidden_fields = {
        key
        for key in payload
        if key not in allowed_fields
    }

    if forbidden_fields:
        return {
            "ok": False,
            "error": "request_contains_forbidden_fields",
            "fields": sorted(forbidden_fields),
        }

    customer_name = str(
        payload.get("customer_name") or ""
    ).strip()

    email = str(
        payload.get("email") or ""
    ).strip().lower()

    connection_value = str(
        payload.get("connection_type") or ""
    ).strip().lower()

    if not customer_name:
        return {
            "ok": False,
            "error": "customer_name_required",
        }

    if not email or "@" not in email:
        return {
            "ok": False,
            "error": "valid_email_required",
        }

    try:
        connection_type = TypPripojeni(
            connection_value
        )
    except ValueError:
        return {
            "ok": False,
            "error": "connection_type_invalid",
        }

    if connection_type == TypPripojeni.WIFI:
        ssid = str(
            payload.get("ssid") or ""
        ).strip()

        wifi_password = str(
            payload.get("wifi_password") or ""
        )

        if not ssid:
            return {
                "ok": False,
                "error": "ssid_required",
            }

        if len(wifi_password) < 8:
            return {
                "ok": False,
                "error": "wifi_password_too_short",
            }

    return {
        "ok": True,
        "customer_name": customer_name,
        "email": email,
        "connection_type": connection_type.value,
    }


def muze_vytvorit_nove_seriove_cislo(
    mode: InstallerMode,
) -> bool:
    """
    Nove SN smi vzniknout pouze pri skutecne prvni instalaci.

    RECOVERY ani servis jiz instalovaneho zarizeni
    nove seriove cislo nikdy nevytvari.
    """

    return mode == InstallerMode.FIRST_INSTALL