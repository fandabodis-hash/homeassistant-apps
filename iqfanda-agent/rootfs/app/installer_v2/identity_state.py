"""Vyhodnoceni cloudove identity TNG IQ FANDA."""

from __future__ import annotations

from typing import Any


REQUIRED_CLOUD_IDENTITY_FIELDS = (
    "device_uuid",
    "device_token",
)


def evaluate_cloud_identity(
    *,
    identity_exists: bool,
    identity_payload: dict[str, Any] | None,
    identity_read_error: bool = False,
) -> dict[str, Any]:
    """
    Rozlisi prvni instalaci, platnou instalaci a recovery stav.

    Samotna vyrobni identita ani seriove cislo neznamena,
    ze byla cloudova instalace dokoncena.
    """

    if not identity_exists:
        return {
            "valid": False,
            "missing": True,
            "recovery": False,
            "reason": "cloud_identity_missing",
            "missing_fields": [],
        }

    if identity_read_error:
        return {
            "valid": False,
            "missing": False,
            "recovery": True,
            "reason": "cloud_identity_unreadable",
            "missing_fields": [],
        }

    if not isinstance(identity_payload, dict):
        return {
            "valid": False,
            "missing": False,
            "recovery": True,
            "reason": "cloud_identity_invalid_format",
            "missing_fields": [],
        }

    missing_fields = [
        field
        for field in REQUIRED_CLOUD_IDENTITY_FIELDS
        if not str(
            identity_payload.get(field) or ""
        ).strip()
    ]

    if missing_fields:
        return {
            "valid": False,
            "missing": False,
            "recovery": True,
            "reason": "cloud_identity_incomplete",
            "missing_fields": missing_fields,
        }

    return {
        "valid": True,
        "missing": False,
        "recovery": False,
        "reason": "cloud_identity_valid",
        "missing_fields": [],
    }