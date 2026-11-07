"""F58 R5 – pure safety policy for stuck manual battery sessions.

Design candidate only. No device access, no cloud calls and no state mutation.
The caller must verify current state and must never infer new START permission
from a stale requested cloud session. Physical STOP is an independent safety
operation and never requires cloud approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Outcome = Literal[
    "healthy_idle",
    "stale_manual_replay",
    "unverified_stop",
    "foreign_remote_owner",
    "remote_auto_required",
    "export_risk",
    "safety_lockout",
    "telemetry_unavailable",
    "active_session",
    "investigate",
]


@dataclass(frozen=True)
class BatteryRecoverySnapshot:
    """Read-only, scoped observations for one verified device UUID."""

    device_uuid_verified: bool
    local_state: str | None
    local_verified: bool
    local_reason: str | None
    local_session_id: str | None
    local_status_age_seconds: float | None
    cloud_manual_state: str | None
    cloud_session_id: str | None
    cloud_action: str | None
    remote_enable: int | None
    remote_setpoint_w: int | None
    owner_marker: str | None
    remote_readback_age_seconds: float | None
    export_forbidden: bool | None
    local_grid_import_w: float | None
    meter_age_seconds: float | None
    bms_ok: bool | None
    soc_percent: float | None
    min_soc_percent: float | None


@dataclass(frozen=True)
class BatteryRecoveryDecision:
    outcome: Outcome
    cloud_session_retirement_allowed: bool
    stop_auto_retry_allowed: bool
    automatic_start_allowed: bool
    agent_restart_allowed: bool
    reason: str


def _decision(outcome: Outcome, reason: str, *, retire: bool = False,
              retry_stop: bool = False) -> BatteryRecoveryDecision:
    # NEVER automatically restart a process or reissue a stale manual START.
    return BatteryRecoveryDecision(outcome, retire, retry_stop, False, False, reason)


def decide(snapshot: BatteryRecoverySnapshot) -> BatteryRecoveryDecision:
    """Produce a no-side-effect plan; caller must enforce it independently.

    Automatic cloud retirement of an old request requires matching session IDs,
    a fresh verified local STOP and readback of remote power 0/remote disabled.
    Unknowns fail closed and MUST NOT be converted into assumed AUTO.
    """
    if not snapshot.device_uuid_verified:
        return _decision("telemetry_unavailable", "device_uuid_mismatch")
    if (snapshot.local_status_age_seconds is None or
        snapshot.local_status_age_seconds < 0 or snapshot.local_status_age_seconds > 120 or
        snapshot.remote_readback_age_seconds is None or
        snapshot.remote_readback_age_seconds < 0 or snapshot.remote_readback_age_seconds > 60):
        return _decision("telemetry_unavailable", "missing_or_stale_local_readback")
    if snapshot.bms_ok is not True:
        return _decision("safety_lockout", "bms_not_verified_ok")
    if snapshot.soc_percent is None or snapshot.min_soc_percent is None:
        return _decision("safety_lockout", "battery_soc_or_minimum_unknown")
    if not 0 <= snapshot.soc_percent <= 100 or not 0 <= snapshot.min_soc_percent <= 100:
        return _decision("safety_lockout", "battery_limits_invalid")
    if snapshot.cloud_action == "discharge_grid" and snapshot.soc_percent <= snapshot.min_soc_percent + 2:
        return _decision("safety_lockout", "minimum_soc_reached")
    if snapshot.remote_enable not in (0, 1) or snapshot.remote_setpoint_w is None:
        return _decision("telemetry_unavailable", "remote_register_readback_unknown")
    if snapshot.owner_marker not in ("owned", "missing", "foreign", "invalid"):
        return _decision("telemetry_unavailable", "owner_marker_unknown")
    if snapshot.owner_marker in ("foreign", "invalid"):
        return _decision("foreign_remote_owner", "do_not_take_ambiguous_remote_owner")
    if snapshot.remote_enable == 1 and snapshot.owner_marker != "owned":
        return _decision("foreign_remote_owner", "do_not_take_foreign_remote_control")
    if snapshot.local_state == "stop_pending" or not snapshot.local_verified:
        # Existing local supervised retry is allowed ONLY when a valid marker
        # claims FANDA ownership; this is not a generic reset of the inverter.
        return _decision("unverified_stop", "stop_or_auto_not_verified",
                         retry_stop=snapshot.owner_marker == "owned")
    if snapshot.remote_enable == 1 or snapshot.remote_setpoint_w != 0:
        return _decision("remote_auto_required", "power_remote_still_enabled",
                         retry_stop=snapshot.owner_marker == "owned")
    if snapshot.local_state in ("preparing", "active"):
        return _decision("active_session", "local_owner_active_check_manual_vs_auto")
    if snapshot.local_state != "stopped":
        return _decision("investigate", "unknown_local_runtime_state")
    if snapshot.cloud_manual_state not in ("requested", "active"):
        return _decision("healthy_idle", "local_auto_verified_cloud_not_requesting")
    if snapshot.cloud_action not in ("charge_grid", "discharge_grid"):
        return _decision("investigate", "unsupported_cloud_action")
    if snapshot.export_forbidden is True and snapshot.cloud_action == "discharge_grid":
        # Zero export is mandatory: fail closed rather than forcing grid export.
        if (snapshot.local_grid_import_w is None or snapshot.meter_age_seconds is None
                or snapshot.meter_age_seconds > 30):
            return _decision("export_risk", "export_forbidden_meter_missing_stale")
        return _decision("export_risk", "grid_discharge_not_authorized_for_zero_export")
    if (not snapshot.cloud_session_id or not snapshot.local_session_id or
        snapshot.cloud_session_id != snapshot.local_session_id):
        return _decision("stale_manual_replay", "session_id_unmatched_no_blind_retirement")
    if snapshot.local_reason not in ("process_restart", "authorization_expired", "manual_user_stop",
                                    "manual_stop", "control_apply_failed", "explicit_auto"):
        return _decision("investigate", "stop_reason_not_recognized")
    return _decision("stale_manual_replay", "matched_stale_session_local_auto_verified", retire=True)
