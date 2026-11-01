"""One explicitly authorized release of an already-zero remote setpoint on 00007.

This is a fixed service operation, not ownership inference or a general Modbus
writer. It never creates an owner marker. One durable attempt consumes the right
to write: a later remote-enable=1 requires a new operator-reviewed procedure.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace
import uuid


COMMAND_TYPE = "foxess_zero_remote_release_v1"
SERIAL = "F800711-TNG-00007"
SOURCE = "F53_R16_00007_RELEASE_AND_TEST"
CONFIRMATION = "RELEASE_ZERO_REMOTE_ON_00007"
RECOVERY_ID = "F53_R16_00007_ZERO_REMOTE_20261101"
EXPECTED_VERSION = "0.1.169"
EXPECTED_SESSION = "a096ee25-1bb0-49ee-84e5-2fdab6232529"
PROFILE_ID = "foxess_h3_10_0_e"
AUDIT_PATH = Path("/config/f53_r16_00007_zero_remote_release.json")
FIRMWARE = {"30016": 223, "30017": 103, "30018": 403}
REGISTERS = {"work_mode_41000": 41000, "max_soc_41010": 41010,
    "remote_timeout_44001": 44001, "remote_active_power_raw_44002": 44002,
    "remote_enable_44000": 44000}
FIXED_REGISTERS = {"work_mode_41000": 0, "max_soc_41010": 100,
    "remote_timeout_44001": 500, "remote_active_power_raw_44002": 0,
    "remote_active_power_w_44002": 0}
PAYLOAD_KEYS = {"serial_number", "device_uuid", "source", "confirmation", "recovery_id",
                "expected_agent_version", "expected_runtime_fingerprint", "issued_at", "expires_at"}


class ReleaseRejected(RuntimeError):
    """The exact service preconditions are not met; do not broaden the scope."""


def _require(condition, code):
    if not condition:
        raise ReleaseRejected(code)


def _now():
    return datetime.now(timezone.utc)


def _date(value):
    _require(isinstance(value, str), "RELEASE_TIMESTAMP_MISSING")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseRejected("RELEASE_TIMESTAMP_INVALID") from exc
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "RELEASE_TIMESTAMP_REQUIRES_TIMEZONE")
    return parsed.astimezone(timezone.utc)


def _valid_now(checked):
    _require(_date(checked["issued_at"]) <= _now() < _date(checked["expires_at"]), "RELEASE_AUTHORIZATION_NOT_CURRENT")


def check_request(*, identity, manufacturing_serial, command_id, payload):
    _require(isinstance(payload, dict) and set(payload) == PAYLOAD_KEYS, "RELEASE_PAYLOAD_SCOPE_INVALID")
    _require(manufacturing_serial == SERIAL and payload["serial_number"] == SERIAL, "RELEASE_TARGET_IS_NOT_00007")
    _require(payload["source"] == SOURCE and payload["confirmation"] == CONFIRMATION
             and payload["recovery_id"] == RECOVERY_ID, "RELEASE_EXPLICIT_CONFIRMATION_MISSING")
    try:
        requested = uuid.UUID(str(payload["device_uuid"]))
        actual = uuid.UUID(str(identity.get("device_uuid") or ""))
        command = uuid.UUID(str(command_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReleaseRejected("RELEASE_UUID_INVALID") from exc
    _require(requested == actual, "RELEASE_DEVICE_UUID_MISMATCH")
    _require(payload["expected_agent_version"] == EXPECTED_VERSION, "RELEASE_EXPECTED_VERSION_INVALID")
    fingerprint = payload["expected_runtime_fingerprint"]
    _require(isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint) is not None,
             "RELEASE_EXPECTED_FINGERPRINT_INVALID")
    issued, expiry = _date(payload["issued_at"]), _date(payload["expires_at"])
    _require(0 < (expiry - issued).total_seconds() <= 180, "RELEASE_AUTHORIZATION_EXCEEDS_180_SECONDS")
    checked = {**payload, "command_id": str(command), "device_uuid": str(actual)}
    _valid_now(checked)
    return checked


def _services():
    from agent_updater import read_installed_agent_version
    from device_config import load_cached_cloud_config
    from host.identity import DeviceIdentityService
    import manual_battery_runtime
    from communication import foxess_ems_controller
    from communication.battery_feature_guard import verify_battery_feature
    from communication.foxess_remote_control_probe import probe_foxess_remote_control_from_cloud_config
    return SimpleNamespace(serial=lambda: DeviceIdentityService().get_serial_number(),
        version=read_installed_agent_version, config=load_cached_cloud_config,
        runtime=manual_battery_runtime, driver=foxess_ems_controller, guard=verify_battery_feature,
        probe=probe_foxess_remote_control_from_cloud_config)


def _marker_missing(driver):
    _require(not driver.OWNER_MARKER_PATH.is_symlink() and not driver.OWNER_MARKER_PATH.exists(),
             "RELEASE_OWNER_MARKER_NOT_MISSING")


def _read_audit(checked, route_hash):
    _require(not AUDIT_PATH.is_symlink(), "RELEASE_AUDIT_IS_SYMLINK")
    try:
        record = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError) as exc:
        raise ReleaseRejected("RELEASE_AUDIT_UNREADABLE") from exc
    _require(isinstance(record, dict) and record.get("schema_version") == 1
             and record.get("recovery_id") == RECOVERY_ID and record.get("serial_number") == SERIAL
             and record.get("device_uuid") == checked["device_uuid"] and record.get("route_sha256") == route_hash
             and record.get("status") in {"write_intent", "verification_intent", "hardware_verified", "completed"},
             "RELEASE_AUDIT_SCOPE_INVALID")
    return record


def _persist(record):
    _require(not AUDIT_PATH.is_symlink(), "RELEASE_AUDIT_IS_SYMLINK")
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".foxess-release-", dir=AUDIT_PATH.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, AUDIT_PATH)
        directory = os.open(AUDIT_PATH.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _snapshot(driver, client):
    read = lambda address: driver._read_one(client=client, device_id=247, address=address)
    registers = {name: read(address) for name, address in REGISTERS.items()}
    registers["remote_active_power_w_44002"] = driver._decode_s16(registers["remote_active_power_raw_44002"])
    return {"protocol_version": read(30028), "device_id": 247,
            "firmware_versions_raw": {key: read(int(key)) for key in FIRMWARE}, "registers": registers}


def _validate_snapshot(snapshot, *, remote):
    _require(snapshot["protocol_version"] == 115 and snapshot["device_id"] == 247
             and snapshot["firmware_versions_raw"] == FIRMWARE, "RELEASE_INVERTER_IDENTITY_CHANGED")
    registers = snapshot["registers"]
    _require(all(type(registers.get(key)) is int and registers[key] == value for key, value in FIXED_REGISTERS.items()),
             "RELEASE_FIXED_REGISTER_PRECONDITION_FAILED")
    _require(type(registers.get("remote_enable_44000")) is int and registers["remote_enable_44000"] in remote,
             "RELEASE_REMOTE_ENABLE_UNEXPECTED")


def _stable(driver, client, *, remote):
    _marker_missing(driver)
    first = _snapshot(driver, client)
    _validate_snapshot(first, remote=remote)
    time.sleep(driver.MODBUS_POST_VERIFY_SETTLE_SECONDS)
    second = _snapshot(driver, client)
    _validate_snapshot(second, remote=remote)
    _marker_missing(driver)
    _require(first == second, "RELEASE_PRECONDITIONS_NOT_STABLE")
    return second


def _runtime_state(services, config):
    runtime = services.runtime
    state = runtime._RUNTIME._load()
    _require(isinstance(state, dict) and isinstance(state.get("control_config"), dict), "RELEASE_PERSISTED_ROUTE_MISSING")
    route = runtime._route(config)
    _require(route == runtime._route(state["control_config"]), "RELEASE_CACHED_AND_PERSISTED_ROUTE_DIFFER")
    _require(state.get("state") in {"stop_pending", "stopped"}, "RELEASE_RUNTIME_IS_NOT_STOPPED_OR_PENDING")
    if state["state"] == "stop_pending":
        _require(state.get("session_id") == EXPECTED_SESSION and _date(state.get("expires_at")) <= _now(),
                 "RELEASE_PENDING_SESSION_IS_NOT_THE_EXPIRED_R15_SESSION")
    intent = state.get("intent")
    _require(isinstance(intent, dict), "RELEASE_PERSISTED_INTENT_MISSING")
    for key, low, high in (("current_soc_percent", 0, 100), ("target_soc_percent", 0, 100),
                           ("minimum_discharge_soc_percent", 10, 100), ("battery_capacity_kwh", 0.000001, float("inf"))):
        value = intent.get(key)
        _require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
                 "RELEASE_PERSISTED_INTENT_INVALID")
    return state, route


def _stop_payload(state):
    intent = state["intent"]
    return {"action": "auto", "reason": "f53_r16_zero_remote_release", "control_source": "manual_immediate_charge",
        "manual_immediate_charge": True, "requested_charge_power_w": 0, "allowed_charge_power_w": 0,
        "requested_discharge_power_w": 0, "allowed_discharge_power_w": 0,
        **{key: intent[key] for key in ("current_soc_percent", "target_soc_percent", "minimum_discharge_soc_percent", "battery_capacity_kwh")},
        "valid_until": (_now() + timedelta(seconds=30)).isoformat()}


def _release(services, checked, evidence):
    runtime, driver = services.runtime, services.driver
    # The runtime loop, normal battery commands and commissioning share this
    # same in-process lock. Never substitute a separate docker-exec process.
    with runtime._LOCK:
        _valid_now(checked)
        _require(services.version() == EXPECTED_VERSION, "RELEASE_INSTALLED_VERSION_MISMATCH")
        fingerprint = services.guard()
        _require(fingerprint == checked["expected_runtime_fingerprint"], "RELEASE_RUNTIME_FINGERPRINT_MISMATCH")
        evidence["runtime_fingerprint"] = fingerprint
        config = services.config()
        state, route = _runtime_state(services, config)
        selected = driver._select_runtime(config)
        _require(str(selected.get("manufacturer") or "").lower() == "foxess"
                 and (selected.get("model") or selected.get("identified_model")) == "H3-10.0-E"
                 and all(selected.get(key) in (None, "", PROFILE_ID) for key in ("profile_id", "inverter_profile_id")),
                 "RELEASE_RUNTIME_PROFILE_MISMATCH")
        route_hash = hashlib.sha256(route.encode()).hexdigest()
        previous = _read_audit(checked, route_hash)
        evidence["previous_recovery_recorded"] = previous is not None
        _communicator, serial_path = driver._find_communicator(str(selected["communicator_id"]).strip())
        _require(serial_path.startswith("/dev/serial/by-id/"), "RELEASE_STABLE_SERIAL_PATH_REQUIRED")
        with driver.ziskej_zamek_modbus_sbernice(serial_path):
            client = driver.ModbusSerialClient(port=serial_path, baudrate=9600, bytesize=8, parity="N",
                                               stopbits=1, timeout=1.5, retries=0)
            try:
                _require(client.connect(), "RELEASE_SERIAL_CONNECT_FAILED")
                before = _stable(driver, client, remote={0, 1})
                evidence["before"] = before
                enabled = before["registers"]["remote_enable_44000"] == 1
                _require(not enabled or (previous is None and state["state"] == "stop_pending"),
                         "RELEASE_REMOTE_REENABLED_OR_NOT_AUTHORIZED_FOR_ONE_WRITE")
                _require(runtime._route(services.config()) == route, "RELEASE_CACHED_ROUTE_CHANGED")
                record = previous or {"schema_version": 1, "recovery_id": RECOVERY_ID, "serial_number": SERIAL,
                    "device_uuid": checked["device_uuid"], "route_sha256": route_hash,
                    "initial_command_id": checked["command_id"], "initial_before": before,
                    "runtime_fingerprint": fingerprint, "created_at": _now().isoformat()}
                record = {**record, "last_command_id": checked["command_id"],
                    "status": "write_intent" if enabled else "verification_intent", "before": before,
                    "write_intent_recorded": bool(enabled or record.get("write_intent_recorded"))}
                _persist(record)
                if enabled:
                    _marker_missing(driver)
                    _valid_now(checked)  # Locks, reads and fsync can consume the lease.
                    evidence["hardware_write_calls_this_command"] = 1
                    try:
                        # Exactly one wire-level request. Never retry a value
                        # mismatch: another controller may have re-enabled it.
                        response = client.write_register(address=44000, value=0, device_id=247)
                        evidence["write_acknowledged"] = response is not None and not response.isError()
                    except Exception as exc:
                        evidence["write_acknowledged"] = False
                        evidence["write_ack_error_type"] = type(exc).__name__
                    time.sleep(driver.MODBUS_WRITE_SETTLE_SECONDS)
                after = _stable(driver, client, remote={0})
                expected = {**before, "registers": {**before["registers"], "remote_enable_44000": 0}}
                _require(after == expected, "RELEASE_UNEXPECTED_REGISTER_CHANGE")
                evidence.update(after=after, remote_enable_changed_observed=enabled)
                record.update(status="hardware_verified", after=after, last_verified_at=_now().isoformat())
                _persist(record)
            finally:
                client.close()
        # Release the bus lock before normal AUTO; its driver takes that lock.
        # Keep the runtime lock, and reject a newly appeared marker before AUTO
        # could enter the owned restore branch and write other registers.
        _marker_missing(driver)
        outcome = runtime._RUNTIME.submit(payload=_stop_payload(state), command_id=checked["command_id"], cloud_config=config)
        stopped, applied = outcome.get("local_runtime", {}), outcome.get("applied", {})
        _require(stopped.get("state") == "stopped" and stopped.get("action") == "auto"
                 and stopped.get("verified") is True and stopped.get("applied_power_w") == 0
                 and applied.get("verified") is True and applied.get("action") == "auto"
                 and applied.get("write_performed") is False, "RELEASE_NORMAL_STOP_NOT_VERIFIED_WITHOUT_WRITES")
        evidence["normal_stop"] = {key: stopped.get(key) for key in ("state", "action", "verified", "applied_power_w")}
        evidence["normal_stop"]["write_performed"] = False
        _require(runtime._route(services.config()) == route, "RELEASE_CACHED_ROUTE_CHANGED_AFTER_STOP")
        probe = services.probe(cloud_config=config)
        _require(probe.get("read_only") is True and probe.get("write_performed") is False
                 and probe.get("profile_id") == PROFILE_ID and probe.get("serial_path_verified") is True
                 and probe.get("owner_marker", {}).get("status") == "missing"
                 and probe.get("owner_marker", {}).get("present") is False, "RELEASE_FINAL_PROBE_INVALID")
        _validate_snapshot(probe, remote={0})
        _require(services.guard() == fingerprint, "RELEASE_IMPLEMENTATION_CHANGED")
        evidence.update(final_probe=probe, verified=True,
                        phase="zero_remote_released" if enabled else "already_zero_verified")
        record.update(status="completed", completed_at=_now().isoformat(), normal_stop=evidence["normal_stop"],
                      final_probe=probe, last_result=evidence)
        _persist(record)


def execute_foxess_zero_remote_release(*, identity, command_id, command_payload, submit_result):
    """Execute only the fixed, short-lived, manufacturing-identity-bound service request."""
    evidence = {"executor": COMMAND_TYPE, "phase": "rejected_or_uncertain", "recovery_id": RECOVERY_ID,
        "serial_number": SERIAL, "agent_version": EXPECTED_VERSION, "verified": False,
        "hardware_write_calls_this_command": 0, "write_acknowledged": None,
        "automatic_repeat": False, "physical_acceptance_test": False, "verification_pin_written": False}
    try:
        services = _services()
        checked = check_request(identity=identity, manufacturing_serial=services.serial(),
                                command_id=command_id, payload=command_payload)
        _release(services, checked, evidence)
    except Exception as exc:
        evidence.update(verified=False, phase="rejected_or_uncertain", error_type=type(exc).__name__)
        submit_result(identity=identity, command_id=command_id, status="failed", result=evidence,
                      error_message=str(exc)[:500])
        return
    submit_result(identity=identity, command_id=command_id, status="succeeded", result=evidence, error_message=None)
