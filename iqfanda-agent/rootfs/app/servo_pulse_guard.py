"""F47 R110 -- fail-closed local servo pulse contract for F800711-TNG-00013.

Not connected to the live HA Agent command dispatcher. This module deliberately
cannot issue a pulse unless local device-side auto-off and mutual interlocking
have been independently verified by an HA-local adapter. A cloud boolean is NOT
proof. The caller must supply trusted callbacks with current on-device evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal
import math
import time

SERIAL = "F800711-TNG-00013"
DEVICE_UUID = "014a0d23-d440-4131-abb4-28a860595043"
OPEN = "switch.sonoff_mini_zb2gs_spinac_3"
CLOSE = "switch.sonoff_mini_zb2gs_spinac_4"
IEEE = "08:fd:52:ff:fe:07:87:d9"
MAX_PULSE_S = 1.0
MAX_TEMP_C = 45.0


class UnsafeServo(RuntimeError):
    """Operation rejected before energizing or failed after a verified off attempt."""


@dataclass(frozen=True)
class Snapshot:
    serial: str
    uuid: str
    ieee: str
    open_state: str
    close_state: str
    floor_c: float
    target_c: float
    telemetry_age_seconds: float


@dataclass(frozen=True)
class LocalSafetyEvidence:
    """Trusted HA-local probe result, never materialized from cloud payload."""
    device_auto_off_confirmed: bool
    mutual_exclusion_confirmed: bool
    expected_ieee: str
    maximum_device_on_seconds: float


def decide(s: Snapshot, tolerance_c: float = 0.2) -> Literal["close", "open", "hold"]:
    """Temperature is the control variable; position is informational only."""
    if (s.serial != SERIAL or s.uuid != DEVICE_UUID or s.ieee.lower() != IEEE):
        raise UnsafeServo("wrong_model_or_device")
    if not (math.isfinite(s.floor_c) and math.isfinite(s.target_c)):
        raise UnsafeServo("invalid_temperature")
    if not (0.0 <= s.telemetry_age_seconds <= 30.0):
        raise UnsafeServo("stale_telemetry")
    if not (15.0 <= s.target_c <= 45.0):
        raise UnsafeServo("invalid_target")
    if s.floor_c >= MAX_TEMP_C:
        return "close"
    if s.floor_c - s.target_c > tolerance_c:
        return "close"
    if s.target_c - s.floor_c > tolerance_c:
        return "open"
    return "hold"


def validate_pulse(s: Snapshot, evidence: LocalSafetyEvidence, direction: str, duration_s: float) -> str:
    requested = decide(s)
    if requested == "hold":
        raise UnsafeServo("within_tolerance_no_pulse")
    if direction not in ("close", "open") or direction != requested:
        raise UnsafeServo("wrong_direction")
    if direction == "open" and s.floor_c >= MAX_TEMP_C:
        raise UnsafeServo("floor_high_open_denied")
    if s.open_state != "off" or s.close_state != "off":
        raise UnsafeServo("directions_not_both_off")
    if not math.isfinite(duration_s) or duration_s <= 0 or duration_s > MAX_PULSE_S:
        raise UnsafeServo("pulse_length_invalid")
    if (not evidence.device_auto_off_confirmed or not evidence.mutual_exclusion_confirmed or
        evidence.expected_ieee.lower() != IEEE or
        not 0 < evidence.maximum_device_on_seconds <= 2.0):
        raise UnsafeServo("local_device_safety_not_verified")
    return CLOSE if direction == "close" else OPEN


def run_pulse(
    *,
    snapshot: Callable[[], Snapshot],
    local_safety_probe: Callable[[], LocalSafetyEvidence],
    arm_device_auto_off: Callable[[str, float], bool],
    set_output: Callable[[str, bool], None],
    wait: Callable[[float], None] = time.sleep,
    duration_s: float = 1.0,
) -> dict:
    """Perform one bounded pulse ONLY when the hardware safety evidence is real.

    Critical: Python finally and its OFF requests cannot guarantee motor shutdown
    if the Agent or Zigbee connection fails. A separately verified local/device
    hardware auto-off and directional interlock remain mandatory.
    """
    s = snapshot()
    direction = decide(s)
    if direction == "hold":
        return {"action": "hold", "floor_c": s.floor_c, "target_c": s.target_c}
    evidence = local_safety_probe()
    entity = validate_pulse(s, evidence, direction, duration_s)
    if arm_device_auto_off(entity, min(2.0, evidence.maximum_device_on_seconds)) is not True:
        raise UnsafeServo("cannot_arm_device_auto_off")
    failure = None
    try:
        # A fresh readback after arming is required to prevent direction races.
        s2 = snapshot()
        validate_pulse(s2, local_safety_probe(), direction, duration_s)
        set_output(entity, True)
        wait(duration_s)
    except BaseException as exc:
        failure = exc
    finally:
        errors = []
        # Both directions OFF regardless of which one was enabled.
        for output in (OPEN, CLOSE):
            try:
                set_output(output, False)
            except BaseException as exc:
                errors.append(f"{output}: {type(exc).__name__}")
        if errors:
            raise UnsafeServo("off_command_failed:" + ",".join(errors)) from failure
    if failure is not None:
        raise failure
    after = snapshot()
    if after.open_state != "off" or after.close_state != "off":
        raise UnsafeServo("off_readback_unconfirmed")
    return {"action": "pulse", "direction": direction, "seconds": duration_s,
            "floor_c": s.floor_c, "target_c": s.target_c}
