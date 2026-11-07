"""F58/F59 – offline commissioning gate for a changed battery implementation.

This module makes *no* Modbus, network or cloud calls. It does not change the
verified pin. An old physical certificate proves the old runtime only; a newer
agent may run ordinary non-battery modules, but manual battery START remains
locked until the new driver is independently verified.

The factory zero-export limit is never disabled by this planner.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Literal

FOXESS_PROFILE = "foxess_h3_10_0_e"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FULL_CANARY_POWER_W = 500
ZERO_EXPORT_RESERVE_W = 300

PlanState = Literal[
    "device_mismatch", "source_integrity_failed", "missing_proof",
    "invalid_proof", "inverter_not_idle", "local_stop_not_verified",
    "battery_safety_not_verified", "already_verified",
    "new_physical_verification_required",
]


@dataclass(frozen=True)
class PinTransitionSnapshot:
    device_uuid: str
    expected_device_uuid: str
    installed_agent_version: str
    runtime_fingerprint: str
    source_integrity_ok: bool
    pin_status: str | None
    pinned_agent_version: str | None
    pinned_runtime_fingerprint: str | None
    evidence_id: str | None
    report_sha256: str | None
    local_runtime_state: str | None
    local_auto_verified: bool
    remote_enable: int | None
    remote_power_w: int | None
    remote_owner: str | None
    readback_age_seconds: float | None
    battery_soc_percent: float | None
    bms_safe: bool | None
    export_forbidden: bool | None
    grid_import_w: float | None
    grid_meter_age_seconds: float | None


@dataclass(frozen=True)
class PinTransitionPlan:
    state: PlanState
    # These fields describe an engineering preflight, never permission to
    # start a device. A separate supervised hardware canary is mandatory.
    charge_preflight_possible: bool
    discharge_preflight_possible: bool
    requires_physical_revalidation: bool
    preserve_previous_pin: bool
    automatically_arm: bool
    automatically_restart: bool
    automatically_change_export_limit: bool
    reason: str


def _plan(state: PlanState, reason: str, *, charge: bool = False,
          discharge: bool = False, requires_test: bool = True) -> PinTransitionPlan:
    return PinTransitionPlan(
        state=state, charge_preflight_possible=charge,
        discharge_preflight_possible=discharge,
        requires_physical_revalidation=requires_test,
        preserve_previous_pin=True,
        automatically_arm=False, automatically_restart=False,
        automatically_change_export_limit=False,
        reason=reason,
    )


def evaluate_pin_transition(s: PinTransitionSnapshot) -> PinTransitionPlan:
    """Assess candidate for a *separately approved* 500 W physical canary.

    A yes to the charge/discharge preflight means only that the preliminary
    readings were acceptable. It never activates Modbus and never declares
    the new runtime physically verified. Continuous local grid monitoring and
    final AUTO readback are also required for a later physical test.
    """
    if (not s.device_uuid or s.device_uuid != s.expected_device_uuid):
        return _plan("device_mismatch", "device_uuid_does_not_match")
    if (not s.source_integrity_ok or not HEX64.fullmatch(s.runtime_fingerprint)):
        return _plan("source_integrity_failed", "new_runtime_not_integrity_verified")
    if s.pin_status != "physically_verified":
        return _plan("missing_proof", "no_prior_physically_verified_pin")
    if (not s.pinned_agent_version or not s.pinned_runtime_fingerprint or
            not HEX64.fullmatch(s.pinned_runtime_fingerprint) or
            not s.evidence_id or not s.report_sha256 or
            not HEX64.fullmatch(s.report_sha256)):
        return _plan("invalid_proof", "prior_pin_incomplete_do_not_modify")
    same = (s.pinned_runtime_fingerprint == s.runtime_fingerprint and
            s.pinned_agent_version == s.installed_agent_version)
    if (s.remote_enable != 0 or s.remote_power_w != 0 or
            s.remote_owner != "missing" or
            s.readback_age_seconds is None or
            not 0 <= s.readback_age_seconds <= 30):
        return _plan("inverter_not_idle", "physical_remote_idle_not_confirmed",
                     requires_test=not same)
    if s.local_runtime_state != "stopped" or not s.local_auto_verified:
        return _plan("local_stop_not_verified", "safe_local_stop_not_confirmed",
                     requires_test=not same)
    if (s.bms_safe is not True or s.battery_soc_percent is None or
            not 30 <= s.battery_soc_percent <= 90):
        return _plan("battery_safety_not_verified", "bms_or_soc_not_ready",
                     requires_test=not same)
    if same:
        return _plan("already_verified", "pin_matches_this_integrity_checked_agent",
                     requires_test=False)

    # No-export mode may remain enabled. A later discharge test must keep
    # monitoring instantaneous local import and must stop immediately if it
    # falls below the power plus reserve. UNKNOWN export policy fails closed.
    can_discharge = (
        s.export_forbidden is False or
        (s.export_forbidden is True and s.grid_import_w is not None and
         s.grid_meter_age_seconds is not None and
         0 <= s.grid_meter_age_seconds <= 5 and
         s.grid_import_w >= FULL_CANARY_POWER_W + ZERO_EXPORT_RESERVE_W)
    )
    return _plan(
        "new_physical_verification_required",
        "old_pin_must_be_archived_and_new_500w_physical_canary_approved",
        charge=True, discharge=can_discharge,
    )


def archive_previous_verified_pin(*, pin_path: Path, archive_directory: Path,
                                  expected_file_sha256: str, approved: bool) -> Path:
    """Append-only disk archive of the exact old registry bytes, never deletion.

    This helper is NOT called from startup or automatic battery recovery.
    It must only run after explicit commissioning approval and while the local
    battery owner is verified idle. The caller separately enforces identity.
    """
    if approved is not True:
        raise PermissionError("Explicit commissioning approval is required.")
    if not HEX64.fullmatch(expected_file_sha256):
        raise ValueError("Expected SHA256 is invalid.")
    if pin_path.is_symlink() or not pin_path.is_file():
        raise RuntimeError("Original pin must be a regular existing file.")
    original = pin_path.read_bytes()
    actual = hashlib.sha256(original).hexdigest()
    if actual != expected_file_sha256:
        raise RuntimeError("Original pin changed; no archive was created.")
    try:
        registry = json.loads(original.decode("utf-8-sig"))
        pin = registry["profiles"][FOXESS_PROFILE]
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise RuntimeError("Original pin registry is malformed.") from exc
    if (registry.get("schema_version") != 1 or
            not isinstance(pin, dict) or pin.get("status") != "physically_verified"):
        raise RuntimeError("Original physical proof is missing.")
    archive_directory.mkdir(parents=True, exist_ok=True)
    if archive_directory.is_symlink():
        raise RuntimeError("Archive directory cannot be a symlink.")
    path = archive_directory / ("pin-registry-" + actual + ".json")
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != original:
            raise RuntimeError("Existing archive differs; will never overwrite it.")
        return path
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o400)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # Do not remove a partly written archive silently.
        raise
    dir_fd = os.open(archive_directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return path
