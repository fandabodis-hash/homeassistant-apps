"""F107: 00019-only cloud command bridge for a single Modbus read-only probe.

This file does not register listeners, schedules, outputs or startup hooks.
"""
from __future__ import annotations

from typing import Any, Callable

from communication.midea_00019_readonly import (
    FTDI_PORT, MideaProbeError, SERIAL, probe_diagnostic_once,
)

DEVICE_UUID = "648d4229-7c17-400f-bbed-ffe5ab04b844"
SITE_ID = "980e5e5e-76b4-4cac-9b6c-1f31fa5fd6e1"
EXPECTED = {
    "serial_number": SERIAL,
    "device_uuid": DEVICE_UUID,
    "site_id": SITE_ID,
    "port_path": FTDI_PORT,
    "unit_id": 16,
    "function_code": 4,
    "read_only": True,
    "scope": "single_probe_no_writes",
}


def validate_command(identity: dict[str, Any], payload: dict[str, Any]) -> None:
    if not isinstance(identity, dict) or str(identity.get("device_uuid", "")).lower() != DEVICE_UUID:
        raise MideaProbeError("MIDEA_AGENT_IDENTITY_MISMATCH")
    if str(identity.get("site_id", "")).lower() != SITE_ID:
        raise MideaProbeError("MIDEA_AGENT_SITE_MISMATCH")
    if not isinstance(payload, dict) or set(payload) != set(EXPECTED):
        raise MideaProbeError("MIDEA_EXACT_READ_ONLY_FIELDS_REQUIRED")
    for key, expected in EXPECTED.items():
        value = payload.get(key)
        if key in ("unit_id", "function_code"):
            if type(value) is not int or value != expected:
                raise MideaProbeError("MIDEA_MODBUS_PARAMS_NOT_APPROVED")
        elif key == "read_only":
            if value is not True:
                raise MideaProbeError("MIDEA_READ_ONLY_FALSE_REJECTED")
        elif value != expected:
            raise MideaProbeError("MIDEA_TARGET_MISMATCH_" + key.upper())


def execute_readonly_command(
    *, identity: dict[str, Any], command_id: str,
    command_payload: dict[str, Any], submit_result: Callable[..., Any],
    probe: Callable[..., dict[str, Any]] = probe_diagnostic_once,
) -> None:
    try:
        validate_command(identity, command_payload)
        result = probe(
            serial_number=SERIAL, port_path=FTDI_PORT,
            unit_id=16, function_code=4,
        )
        if not isinstance(result, dict) or result.get("read_only") is not True:
            raise MideaProbeError("MIDEA_DRIVER_INVALID_RESULT")
        if result.get("writes_performed") is not False:
            raise MideaProbeError("MIDEA_DRIVER_WRITE_GUARD")
        if result.get("serial_number") != SERIAL:
            raise MideaProbeError("MIDEA_RESULT_TARGET_DRIFT")
        # F144 R2: never replace failure or partial reads with success.
        success = result.get("probe_success")
        physical = result.get("physical_modbus_read")
        if type(success) is not bool or type(physical) is not bool:
            raise MideaProbeError("MIDEA_DRIVER_MISSING_READ_EVIDENCE")
        if success and not physical:
            raise MideaProbeError("MIDEA_DRIVER_INVALID_READ_EVIDENCE")
        result = dict(result)
        reason = None if success else "NO_WATER_TEMPERATURE_RESPONSE_NO_PROFILE_VERIFICATION"
        result.update({
            "executor": "midea_00019_cloud_probe",
            "probe_success": success, "physical_modbus_read": physical,
            "profile_verified": False,
            "read_only": True, "writes_performed": False,
            "control_enabled": False,
            "phase": ("candidate_registers_read_only_no_actuation" if success
                      else "read_only_probe_failed_with_block_evidence"),
            "reason": reason,
        })
        submit_result(
            identity=identity, command_id=command_id,
            status="succeeded" if success else "failed",
            result=result, error_message=reason,
        )
    except Exception as exc:
        code = str(exc) if isinstance(exc, MideaProbeError) else type(exc).__name__
        submit_result(
            identity=identity, command_id=command_id, status="failed",
            result={
                "executor": "midea_00019_cloud_probe",
                "phase": "read_only_probe_failed",
                "reason": code, "read_only": True,
                "writes_performed": False, "control_enabled": False,
            },
            error_message=code,
        )
