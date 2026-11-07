"""F58 FoxESS-only 0.1.175 – exact 00007 commissioning authorization.

This local helper is a pure input check. It never contacts HA, the cloud,
Modbus, hardware, or changes any other model's configuration. Generic
command_worker remains serial/UUID-independent.
"""
from __future__ import annotations

PROTECTED_FOXESS_PILOT_UUID = "1c25a8d4-b986-43ee-b2fc-eaa152e9bdf8"
ORIGINAL_00007_PIN_SHA256 = "7717125241c7b4903f9f7f183d3929e49481a4b2ead77e3f5ae33e07fc7a6cdc"


def require_foxess_175_pilot(*, identity: dict, payload: dict,
                             installed_version: str) -> None:
    """An explicit one-device authorization before attempting a physical test.

    Existing other-agent versions are unchanged. On 0.1.175 only 00007 can
    commission; no historical PIN may be silently migrated or erased.
    """
    if installed_version != "0.1.175":
        return
    if not isinstance(identity, dict) or not isinstance(payload, dict):
        raise ValueError("FoxESS 0.1.175 commissioning requires valid device identity.")
    if (identity.get("device_uuid") != PROTECTED_FOXESS_PILOT_UUID
            or payload.get("expected_device_uuid") != PROTECTED_FOXESS_PILOT_UUID):
        raise ValueError("FoxESS 0.1.175 commissioning is restricted to 00007.")
    if payload.get("explicit_technician_approval") is not True:
        raise ValueError("FoxESS 0.1.175 commissioning requires technician approval.")
    if payload.get("expected_previous_pin_sha256") != ORIGINAL_00007_PIN_SHA256:
        raise ValueError("FoxESS 0.1.175 original physical pin SHA256 is not verified.")
