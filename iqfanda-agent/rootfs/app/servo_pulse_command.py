"""F47 R111: guarded local HA Agent entrypoint for servo pulses on model 00013.

This is a DRAFT feature, not enabled in production. It cannot energize either
relay until a trusted HA-local device auto-off and interlock attestation exists.
No cloud-supplied flags can override this protection.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from servo_pulse_guard import (
    SERIAL, DEVICE_UUID, IEEE, OPEN, CLOSE, Snapshot,
    LocalSafetyEvidence, UnsafeServo, run_pulse,
)

FLOOR_SENSOR = "sensor.owon_ths317_et_4"
SUPPORTED_COMMAND = "zigbee_servo_pulse_v1"


def _state_of(get_entity_state: Callable[[str], dict], entity: str) -> str:
    obj = get_entity_state(entity)
    if not isinstance(obj, dict):
        raise UnsafeServo("ha_state_missing:" + entity)
    state = str(obj.get("state") or "").lower()
    if state in ("unavailable", "unknown", ""):
        raise UnsafeServo("ha_state_unavailable:" + entity)
    return state


def _make_snapshot(
    *,
    identity: dict,
    payload: dict,
    get_entity_state: Callable[[str], dict],
    get_entity_device_id: Callable[[str], str],
    freshness_seconds: Callable[[str], float],
) -> Snapshot:
    """Read *actual* HA entities; never take measured temperatures from payload."""
    if str(identity.get("device_uuid") or "") != DEVICE_UUID:
        raise UnsafeServo("wrong_device_identity")
    if str(payload.get("serial") or "") != SERIAL:
        raise UnsafeServo("wrong_serial")
    if str(payload.get("device_uuid") or "") != DEVICE_UUID:
        raise UnsafeServo("wrong_uuid")
    if str(payload.get("ieee") or "").lower() != IEEE:
        raise UnsafeServo("wrong_ieee")
    if not isinstance(payload.get("target_c"), (int, float)):
        raise UnsafeServo("invalid_target")
    target = float(payload["target_c"])
    if not math.isfinite(target):
        raise UnsafeServo("nonfinite_target")
    observed_id_open = get_entity_device_id(OPEN)
    observed_id_close = get_entity_device_id(CLOSE)
    if not observed_id_open or observed_id_open != observed_id_close:
        raise UnsafeServo("direction_outputs_not_same_device")
    if str(payload.get("device_reg_id") or "") != observed_id_open:
        raise UnsafeServo("device_registry_mismatch")
    op = _state_of(get_entity_state, OPEN)
    cl = _state_of(get_entity_state, CLOSE)
    temp = float(_state_of(get_entity_state, FLOOR_SENSOR))
    age = float(freshness_seconds(FLOOR_SENSOR))
    if not math.isfinite(temp) or not math.isfinite(age):
        raise UnsafeServo("invalid_sensor")
    return Snapshot(
        serial=SERIAL, uuid=DEVICE_UUID, ieee=IEEE, open_state=op,
        close_state=cl, floor_c=temp, target_c=target,
        telemetry_age_seconds=age,
    )


def no_attestation(_ieee: str) -> LocalSafetyEvidence:
    """DO NOT bypass: deployment has no proven device-native auto-off/interlock."""
    return LocalSafetyEvidence(
        device_auto_off_confirmed=False,
        mutual_exclusion_confirmed=False,
        expected_ieee=IEEE,
        maximum_device_on_seconds=0.0,
    )


def execute(
    *,
    identity: dict[str, Any],
    payload: dict[str, Any],
    get_entity_state: Callable[[str], dict],
    get_entity_device_id: Callable[[str], str],
    freshness_seconds: Callable[[str], float],
    set_output: Callable[[str, bool], None],
    device_safety_attestor: Callable[[str], LocalSafetyEvidence] = no_attestation,
    arm_device_auto_off: Callable[[str, float], bool] | None = None,
    wait: Callable[[float], None] | None = None,
) -> dict:
    """Complete one strictly bounded pulse. NEVER trust payload's safety booleans.

    This safe-by-default integration will REFUSE to actuate because attestation
    is intentionally not implemented in installed ZHA devices yet.
    """
    if payload.get("command_type") != SUPPORTED_COMMAND:
        raise UnsafeServo("wrong_command_type")
    requested_duration = payload.get("seconds")
    if type(requested_duration) not in (int, float) or float(requested_duration) != 1.0:
        raise UnsafeServo("one_second_only")
    if arm_device_auto_off is None:
        # A remote ON without a locally configured physical OFF is unsafe.
        raise UnsafeServo("device_local_auto_off_adapter_not_installed")
    def snap() -> Snapshot:
        return _make_snapshot(
            identity=identity,
            payload=payload,
            get_entity_state=get_entity_state,
            get_entity_device_id=get_entity_device_id,
            freshness_seconds=freshness_seconds,
        )
    def attestor() -> LocalSafetyEvidence:
        return device_safety_attestor(IEEE)
    kwargs = {}
    if wait is not None:
        kwargs["wait"] = wait
    return run_pulse(
        snapshot=snap,
        local_safety_probe=attestor,
        arm_device_auto_off=arm_device_auto_off,
        set_output=set_output,
        duration_s=1.0,
        **kwargs,
    )
