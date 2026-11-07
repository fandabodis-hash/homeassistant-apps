"""Bounded, local FoxESS 500 W physical canary; never contacts the cloud.

The caller must reserve the local battery runtime and persist its recovery
route before calling this runner. The runner holds the shared serial-bus lock
through both directions and verified AUTO cleanup. It never accepts register
addresses, power levels, or timing overrides from a command payload.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import re
import time
from typing import Any

from agent_updater import read_installed_agent_version
from communication import foxess_ems_controller as driver
from communication.inverter_adapter import select_inverter_profile
from local_modules.foxess_commissioning_grid_guard import LocalGridImportGuard, GridGuardUnavailable


PROFILE_ID = "foxess_h3_10_0_e"
VERIFICATION_SCOPE = "bidirectional_500w_and_stop"
MINIMUM_AGENT_VERSION = (0, 1, 168)
POWER_W = 500
MINIMUM_MEASURED_POWER_W = 250
MAXIMUM_MEASURED_POWER_W = 750
MINIMUM_SOC_PERCENT = 30
MAXIMUM_SOC_PERCENT = 90
CHARGE_TARGET_SOC_PERCENT = 95
DISCHARGE_FLOOR_SOC_PERCENT = 20
SAMPLE_INTERVAL_SECONDS = 5.0
PHASE_TIMEOUT_SECONDS = 60.0
REQUIRED_CONSECUTIVE_SAMPLES = 3
MAXIMUM_SAMPLES_PER_PHASE = 12
FIRMWARE_REGISTERS = (30016, 30017, 30018)

_SNAPSHOT_REGISTERS = {
    "protocol_version": 30028,
    "work_mode": 41000,
    "max_soc_percent": 41010,
    "remote_enable": 44000,
    "remote_timeout_seconds": 44001,
    "remote_active_power_w": 44002,
    "battery_power_w": 31036,
    "battery_soc_percent": 31038,
}


class CanaryFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(exc: Exception, phase: str) -> dict[str, Any]:
    # Raw serial/transport exceptions can contain a USB path or other local
    # identifiers. Export only our fixed messages and numeric readback facts.
    if isinstance(exc, CanaryFailure):
        return {"phase": phase, "code": exc.code, "message": str(exc)}
    result: dict[str, Any] = {"phase": phase, "code": "driver_or_transport_error"}
    if type(exc).__name__ in {
        "RuntimeError", "ValueError", "TypeError", "TimeoutError", "OSError",
        "ConnectionError", "PermissionError", "BatteryControlSafetyStop",
    }:
        result["exception_type"] = type(exc).__name__
    match = re.search(
        r"FoxESS (?:AUTO final readback|registr) (\d+): "
        r"ocekavano (-?\d+), nacteno (-?\d+)", str(exc),
    )
    if match:
        result.update(register=int(match[1]), expected=int(match[2]), actual=int(match[3]))
    return result


def _snapshot(client: Any) -> dict[str, int]:
    result = {
        key: driver._read_one(client=client, device_id=247, address=address)
        for key, address in _SNAPSHOT_REGISTERS.items()
    }
    for key in ("remote_active_power_w", "battery_power_w"):
        result[key] = driver._decode_s16(result[key])
    return result


def _apply(client: Any, action: str) -> dict[str, Any]:
    result = driver.apply_foxess_h3_action(
        client=client, device_id=247, action=action,
        allowed_charge_power_w=POWER_W if action == "charge_grid" else 0,
        allowed_discharge_power_w=POWER_W if action == "discharge_grid" else 0,
        target_soc_percent=CHARGE_TARGET_SOC_PERCENT,
        minimum_discharge_soc_percent=DISCHARGE_FLOOR_SOC_PERCENT,
        marker_path=driver.OWNER_MARKER_PATH,
    )
    expected_power = 0 if action == "auto" else POWER_W
    if result.verified is not True or result.action != action or result.applied_power_w != expected_power:
        raise CanaryFailure("driver_unverified", "The driver did not verify the fixed canary request.")
    return asdict(result)


def _auto(client: Any, original: dict[str, int]) -> dict[str, Any]:
    evidence: dict[str, Any] = {"attempted": True, "verified": False}
    try:
        evidence["driver_result"] = _apply(client, "auto")
        evidence["registers"] = current = _snapshot(client)
        expected = {"protocol_version": 115, "remote_enable": 0,
                    "remote_active_power_w": 0, **original}
        if any(current[key] != value for key, value in expected.items()):
            raise CanaryFailure("auto_readback_mismatch", "Final AUTO registers do not match the saved original state.")
        if driver._read_marker(driver.OWNER_MARKER_PATH) is not None:
            raise CanaryFailure("owner_not_released", "AUTO did not release the local driver owner marker.")
        evidence["verified"] = True
    except Exception as exc:
        evidence["error"] = _error(exc, "auto")
    return evidence


def _require_soc(current: dict[str, int]) -> None:
    if not MINIMUM_SOC_PERCENT <= current["battery_soc_percent"] <= MAXIMUM_SOC_PERCENT:
        raise CanaryFailure("soc_outside_canary_window", "Canary requires a measured SOC between 30 and 90 percent.")
    if not 0 <= current["max_soc_percent"] <= 100:
        raise CanaryFailure("invalid_maximum_soc", "The inverter maximum SOC is outside its valid range.")


def _phase(client: Any, action: str, evidence: dict[str, Any], *, grid_guard: Any) -> None:
    evidence.update(action=action, status="failed", samples=[], consecutive_matching_samples=0)
    setpoint = POWER_W if action == "discharge_grid" else -POWER_W
    evidence["requested_setpoint_w"] = setpoint
    if action == "discharge_grid":
        # Independently verified local meter + zero-export hardware limiter.
        # No cloud telemetry, estimated consumption or stale smart-meter data.
        try:
            evidence["grid_import_before_w"] = grid_guard.check_before_discharge()
        except GridGuardUnavailable as exc:
            raise CanaryFailure("grid_import_preflight_failed", str(exc)) from exc
    evidence["driver_result"] = _apply(client, action)
    started = time.monotonic()
    deadline = started + PHASE_TIMEOUT_SECONDS
    consecutive = 0
    expected = {"protocol_version": 115, "work_mode": 2, "remote_enable": 1,
                "remote_timeout_seconds": driver.REMOTE_TIMEOUT_SECONDS,
                "remote_active_power_w": setpoint}
    for _ in range(MAXIMUM_SAMPLES_PER_PHASE):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(SAMPLE_INTERVAL_SECONDS, remaining))
        if action == "discharge_grid":
            try:
                grid_import = grid_guard.check_during_discharge()
            except GridGuardUnavailable as exc:
                raise CanaryFailure("grid_import_during_discharge_failed", str(exc)) from exc
        current = _snapshot(client)
        elapsed = time.monotonic() - started
        sample = {"elapsed_seconds": round(elapsed, 3), "registers": current}
        if action == "discharge_grid":
            sample["grid_import_w"] = grid_import
        evidence["samples"].append(sample)
        _require_soc(current)
        if action == "charge_grid" and current["battery_soc_percent"] >= min(
            CHARGE_TARGET_SOC_PERCENT, current["max_soc_percent"]
        ):
            raise CanaryFailure("charge_soc_limit_reached", "The inverter charge SOC limit was reached during the canary.")
        sample["readback_verified"] = all(current[key] == value for key, value in expected.items())
        if not sample["readback_verified"]:
            raise CanaryFailure("active_readback_mismatch", "The active mode, remote enable, timeout or signed setpoint changed.")
        measured = current["battery_power_w"] * (1 if setpoint > 0 else -1)
        sample["physical_power_within_tolerance"] = MINIMUM_MEASURED_POWER_W <= measured <= MAXIMUM_MEASURED_POWER_W
        consecutive = consecutive + 1 if sample["physical_power_within_tolerance"] else 0
        evidence["consecutive_matching_samples"] = consecutive
        if elapsed <= PHASE_TIMEOUT_SECONDS and consecutive >= REQUIRED_CONSECUTIVE_SAMPLES:
            evidence["status"] = "passed"
            return
    raise CanaryFailure("physical_power_not_confirmed", "Three consecutive independent battery-power samples did not confirm the requested direction and tolerance within 60 seconds.")


def run_foxess_battery_canary(*, cloud_config: dict, command_id: str,
                              export_guard: Any | None = None) -> dict[str, Any]:
    """Return complete evidence on success or failure, including AUTO cleanup.

    Invoke through the local runtime's exclusive, persistent canary wrapper.
    PASS covers this fixed 500 W bidirectional test and restored AUTO only.
    It does not certify other powers, models, BMS limits, offline or reboot tests.
    """
    result: dict[str, Any] = {
        "schema_version": 1, "status": "failed", "verified": False,
        "verification_scope": VERIFICATION_SCOPE, "profile_id": PROFILE_ID,
        "device_id": 247, "agent_version": None, "started_at": _utc_now(),
        "firmware_versions_raw": {}, "preflight": {}, "phases": [],
        "final_auto": {"attempted": False, "verified": False}, "error": None,
        "criteria": {"setpoint_magnitude_w": POWER_W,
                     "measured_magnitude_range_w": [MINIMUM_MEASURED_POWER_W, MAXIMUM_MEASURED_POWER_W],
                     "consecutive_samples": REQUIRED_CONSECUTIVE_SAMPLES,
                     "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
                     "phase_timeout_seconds": PHASE_TIMEOUT_SECONDS,
                     "soc_range_percent": [MINIMUM_SOC_PERCENT, MAXIMUM_SOC_PERCENT]},
    }
    phase = "preflight"
    try:
        if not isinstance(command_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", command_id):
            raise CanaryFailure("invalid_command_id", "Canary requires a bounded command identifier.")
        result["command_id"] = command_id
        version = read_installed_agent_version()
        if not re.fullmatch(r"\d+\.\d+\.\d+", version) or tuple(map(int, version.split("."))) < MINIMUM_AGENT_VERSION:
            raise CanaryFailure("agent_upgrade_required", "Install agent 0.1.168 or later before running the canary.")
        result["agent_version"] = version
        runtime = driver._select_runtime(cloud_config)
        declared_profiles = [runtime[key] for key in ("profile_id", "inverter_profile_id")
                             if runtime.get(key) not in (None, "")]
        manufacturer = str(runtime.get("manufacturer") or "").strip().lower()
        model = str(runtime.get("model") or runtime.get("identified_model") or "").strip()
        if any(profile != PROFILE_ID for profile in declared_profiles) or manufacturer != "foxess" or model != "H3-10.0-E":
            raise CanaryFailure("unsupported_profile", "This canary is limited to the FoxESS H3-10.0-E profile.")
        # The existing cloud runtime supplies manufacturer/model but need not
        # flatten profile_id from module configuration. Resolve that exact
        # model through the protected local catalog; never infer another family.
        profile = select_inverter_profile(manufacturer=manufacturer, model=model)
        if profile.get("profile_id") != PROFILE_ID:
            raise CanaryFailure("unsupported_profile", "The local profile does not match the FoxESS H3-10.0-E canary.")
        # Installation-specific source is local HA, never a cloud request or
        # a writable parameter from DeviceCommand. Missing proof denies the test.
        if export_guard is None:
            try:
                export_guard = LocalGridImportGuard()
            except GridGuardUnavailable as exc:
                raise CanaryFailure("independent_grid_meter_missing", str(exc)) from exc
        _communicator, serial_path = driver._find_communicator(str(runtime["communicator_id"]).strip())
        with driver.ziskej_zamek_modbus_sbernice(serial_path):
            client = driver.ModbusSerialClient(port=serial_path, baudrate=9600, bytesize=8,
                                               parity="N", stopbits=1, timeout=1.5, retries=0)
            restore_allowed = False
            original: dict[str, int] = {}
            try:
                if not client.connect():
                    raise CanaryFailure("serial_unavailable", "The configured inverter serial port could not be opened.")
                current = _snapshot(client)
                result["preflight"]["registers"] = current
                if current["protocol_version"] != 115 or current["remote_enable"] not in {0, 1}:
                    raise CanaryFailure("unsupported_register_state", "Canary requires protocol 115 and a valid remote-enable register.")
                try:
                    marker = driver._read_marker(driver.OWNER_MARKER_PATH)
                except Exception:
                    result["preflight"]["owner"] = "foreign_or_invalid"
                    raise CanaryFailure("foreign_or_invalid_owner", "An incompatible or unreadable owner marker prevents the canary.") from None
                result["preflight"]["owner"] = "tng_iq_fanda" if marker is not None else None
                if marker is None and current["remote_enable"] != 0:
                    raise CanaryFailure("foreign_remote_owner", "Remote control is active without the local driver owner marker.")
                original = {
                    "work_mode": marker.get("original_work_mode") if marker is not None else current["work_mode"],
                    "remote_timeout_seconds": marker.get("original_timeout") if marker is not None else current["remote_timeout_seconds"],
                }
                if type(original["work_mode"]) is not int or original["work_mode"] not in {0, 1, 2} or type(original["remote_timeout_seconds"]) is not int or not 0 <= original["remote_timeout_seconds"] <= 65535:
                    raise CanaryFailure("invalid_original_state", "The original AUTO settings cannot be safely restored.")
                result["preflight"]["original_auto"] = original
                restore_allowed = True
                _require_soc(current)
                if current["battery_soc_percent"] >= min(CHARGE_TARGET_SOC_PERCENT, current["max_soc_percent"]):
                    raise CanaryFailure("charge_soc_limit_reached", "Current SOC leaves no room below the inverter charge SOC limit.")
                for address in FIRMWARE_REGISTERS:
                    result["firmware_versions_raw"][str(address)] = driver._read_one(client=client, device_id=247, address=address)
                result["preflight"]["auto"] = _auto(client, original)
                if not result["preflight"]["auto"]["verified"]:
                    raise CanaryFailure("baseline_auto_unverified", "AUTO must be verified before applying either canary direction.")
                for action in ("discharge_grid", "charge_grid"):
                    phase = action
                    evidence: dict[str, Any] = {}
                    result["phases"].append(evidence)
                    _phase(client, action, evidence, grid_guard=export_guard)
                    if action == "discharge_grid":
                        phase = "between_phases_auto"
                        result["between_phases_auto"] = _auto(client, original)
                        if not result["between_phases_auto"]["verified"]:
                            raise CanaryFailure("intermediate_auto_unverified", "AUTO must be verified before reversing direction.")
            except Exception as exc:
                result["error"] = _error(exc, phase)
            finally:
                if restore_allowed:
                    result["final_auto"] = _auto(client, original)
                    if not result["final_auto"]["verified"] and result["error"] is None:
                        result["error"] = result["final_auto"].get("error")
                try:
                    client.close()
                except Exception as exc:
                    result["close_error"] = _error(exc, "serial_close")
                    if result["error"] is None:
                        result["error"] = result["close_error"]
    except Exception as exc:
        result["error"] = _error(exc, phase)
    passed = result["error"] is None and len(result["phases"]) == 2 and all(
        item.get("status") == "passed" for item in result["phases"]
    ) and result["final_auto"]["verified"]
    result.update(status="passed" if passed else "failed", verified=passed, finished_at=_utc_now())
    return result
