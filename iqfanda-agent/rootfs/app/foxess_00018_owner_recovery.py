"""F99: one-use, identity-bound FoxESS 00018 recovery from unowned ZERO remote.

A SERVICE action, not a replacement battery driver.  It never grants ownership,
never creates/deletes the FoxESS owner marker and never sends charge/discharge.
The sole possible Modbus write is 44000=0 after two complete stable snapshots,
with 44002 already ZERO and the historical 00018 firmware explicitly matched.
The one-use durable audit is committed BEFORE the write; uncertain writes are
never retried without a new reviewed procedure.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

SERIAL = "F800711-TNG-00018"
VERSION = "0.1.176"
SOURCE = "F99_R4_00018_OWNER_RECOVERY_20261119"
CONFIRMATION = "RELEASE_ZERO_REMOTE_ONLY_00018"
RECOVERY_ID = "F99_R4_00018_20261119"
PROFILE = "foxess_h3_10_0_e"
EXPECTED_FIRMWARE = {"30016": 223, "30017": 103, "30018": 407}
FIXED = {"work_mode_41000": 0, "max_soc_41010": 100,
         "remote_timeout_44001": 20804, "remote_active_power_44002": 0}
AUDIT_PATH = Path("/config/f99_r4_00018_zero_remote_recovery.json")
PAYLOAD_KEYS = {"serial_number", "device_uuid", "source", "confirmation",
                "recovery_id", "expected_agent_version",
                "expected_runtime_fingerprint", "issued_at", "expires_at"}


class RecoveryRejected(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise RecoveryRejected(reason)


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    require(isinstance(value, str), "RECOVERY_DATE_MISSING")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryRejected("RECOVERY_DATE_INVALID") from exc
    require(dt.tzinfo is not None and dt.utcoffset() is not None,
            "RECOVERY_DATE_TIMEZONE_MISSING")
    return dt.astimezone(timezone.utc)


def check_request(*, identity, serial, command_id, payload):
    require(type(payload) is dict and set(payload) == PAYLOAD_KEYS,
            "RECOVERY_PAYLOAD_SCOPE_INVALID")
    require(serial == SERIAL and payload["serial_number"] == SERIAL,
            "RECOVERY_WRONG_DEVICE")
    require(payload["source"] == SOURCE
            and payload["confirmation"] == CONFIRMATION
            and payload["recovery_id"] == RECOVERY_ID,
            "RECOVERY_OPERATOR_CONFIRMATION_MISSING")
    require(payload["expected_agent_version"] == VERSION,
            "RECOVERY_VERSION_MISMATCH")
    try:
        actual = uuid.UUID(str(identity["device_uuid"]))
        expected = uuid.UUID(str(payload["device_uuid"]))
        command = uuid.UUID(str(command_id))
    except (KeyError, ValueError, TypeError) as exc:
        raise RecoveryRejected("RECOVERY_DEVICE_UUID_INVALID") from exc
    require(actual == expected, "RECOVERY_DEVICE_UUID_MISMATCH")
    sha = payload["expected_runtime_fingerprint"]
    require(isinstance(sha, str) and re.fullmatch("[a-f0-9]{64}", sha) is not None,
            "RECOVERY_FINGERPRINT_FORMAT_INVALID")
    issued, expires = timestamp(payload["issued_at"]), timestamp(payload["expires_at"])
    require(0 < (expires-issued).total_seconds() <= 180,
            "RECOVERY_LEASE_EXCEEDS_180_SECONDS")
    checked = {**payload, "command_id": str(command)}
    valid_now(checked)
    return checked


def valid_now(checked):
    require(timestamp(checked["issued_at"]) <= now() < timestamp(checked["expires_at"]),
            "RECOVERY_AUTHORIZATION_EXPIRED")


def marker_missing(driver):
    path = driver.OWNER_MARKER_PATH
    require(not path.is_symlink() and not path.exists(),
            "RECOVERY_OWNERSHIP_MARKER_NOT_MISSING")


def _audit(record):
    require(not AUDIT_PATH.is_symlink(), "RECOVERY_AUDIT_SYMLINK")
    require(not AUDIT_PATH.exists(), "RECOVERY_ALREADY_ATTEMPTED")
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(AUDIT_PATH, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(record, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    sync_parent(AUDIT_PATH)


def sync_parent(path):
    d = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(d)
    finally:
        os.close(d)


def _update_audit(record):
    require(AUDIT_PATH.is_file() and not AUDIT_PATH.is_symlink(),
            "RECOVERY_AUDIT_MISSING")
    fd, path = tempfile.mkstemp(prefix=".f99audit-", dir=AUDIT_PATH.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, AUDIT_PATH)
        sync_parent(AUDIT_PATH)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def snapshot(driver, client):
    read = lambda address: driver._read_one(client=client, device_id=247,
                                           address=address)
    regs = {name: read(address) for name, address in (
        ("work_mode_41000", 41000),
        ("max_soc_41010", 41010),
        ("remote_enable_44000", 44000),
        ("remote_timeout_44001", 44001),
        ("remote_active_power_44002", 44002),
    )}
    return {"protocol_version": read(30028),
            "firmware": {str(reg): read(reg) for reg in (30016, 30017, 30018)},
            "registers": regs}


def check_snapshot(value, *, remote):
    require(value["protocol_version"] == 115, "RECOVERY_PROTOCOL_CHANGED")
    require(value["firmware"] == EXPECTED_FIRMWARE, "RECOVERY_FIRMWARE_CHANGED")
    regs = value["registers"]
    require(all(type(regs.get(k)) is int and regs[k] == v for k, v in FIXED.items()),
            "RECOVERY_NONZERO_POWER_OR_SETTING_CHANGED")
    require(type(regs.get("remote_enable_44000")) is int
            and regs["remote_enable_44000"] in remote,
            "RECOVERY_REMOTE_ENABLE_UNEXPECTED")


def stable(driver, client, *, remote):
    marker_missing(driver)
    first = snapshot(driver, client)
    check_snapshot(first, remote=remote)
    time.sleep(0.3)
    second = snapshot(driver, client)
    check_snapshot(second, remote=remote)
    marker_missing(driver)
    require(first == second, "RECOVERY_HARDWARE_SNAPSHOT_UNSTABLE")
    return second


def local_state(runtime, config):
    state = runtime._RUNTIME._load()
    require(type(state) is dict and state.get("state") == "stop_pending",
            "RECOVERY_LOCAL_STOP_NOT_PENDING")
    require(state.get("profile_id") == PROFILE and state.get("manual") is True,
            "RECOVERY_WRONG_LOCAL_OWNER")
    require(isinstance(state.get("control_config"), dict)
            and runtime._route(config) == runtime._route(state["control_config"]),
            "RECOVERY_DEVICE_ROUTE_CHANGED")
    require(timestamp(state["expires_at"]) < now(), "RECOVERY_SESSION_NOT_EXPIRED")
    intent = state.get("intent")
    require(type(intent) is dict, "RECOVERY_SAVED_INTENT_MISSING")
    for key in ("current_soc_percent", "target_soc_percent",
                "minimum_discharge_soc_percent", "battery_capacity_kwh"):
        v = intent.get(key)
        require(type(v) in (int, float) and 0 <= v <= 100
                if key != "battery_capacity_kwh" else
                type(v) in (int, float) and 0 < v <= 1000,
                "RECOVERY_INVALID_SAVED_INTENT")
    return state


def auto_payload(state):
    intent = state["intent"]
    return {"action": "auto", "reason": "f99_r4_00018_zero_remote_release",
            "control_source": "manual_immediate_charge",
            "manual_immediate_charge": True,
            "requested_charge_power_w": 0, "allowed_charge_power_w": 0,
            "requested_discharge_power_w": 0, "allowed_discharge_power_w": 0,
            **{key: intent[key] for key in (
                "current_soc_percent", "target_soc_percent",
                "minimum_discharge_soc_percent", "battery_capacity_kwh")},
            "valid_until": (now()+timedelta(seconds=30)).isoformat()}


def release(services, checked, evidence):
    runtime, driver = services.runtime, services.driver
    # Same in-process lock as STOP, renewal and cloud commands.
    with runtime._LOCK:
        valid_now(checked)
        require(services.serial() == SERIAL and services.version() == VERSION,
                "RECOVERY_IDENTITY_OR_VERSION_CHANGED")
        # A safety STOP must not require a new physical START pin after update.
        # The complete source manifest is still enforced (no profile pin bypass for START).
        certified = services.guard()
        require(certified == checked["expected_runtime_fingerprint"],
                "RECOVERY_RELEASE_FINGERPRINT_MISMATCH")
        config = services.config()
        state = local_state(runtime, config)
        selected = driver._select_runtime(config)
        require(str(selected.get("manufacturer") or "").lower() == "foxess"
                and (selected.get("model") or selected.get("identified_model")) == "H3-10.0-E"
                and all(selected.get(key) in (None, "", PROFILE)
                        for key in ("profile_id", "inverter_profile_id")),
                "RECOVERY_INVERTER_PROFILE_CHANGED")
        marker_missing(driver)
        require(not AUDIT_PATH.exists() and not AUDIT_PATH.is_symlink(),
                "RECOVERY_ALREADY_ATTEMPTED")
        _comm, serial_path = driver._find_communicator(str(selected["communicator_id"]).strip())
        require(serial_path.startswith("/dev/serial/by-id/"),
                "RECOVERY_SERIAL_ROUTE_NOT_STABLE")
        with driver.ziskej_zamek_modbus_sbernice(serial_path):
            client = driver.ModbusSerialClient(
                port=serial_path, baudrate=9600, bytesize=8,
                parity="N", stopbits=1, timeout=1.5, retries=0)
            try:
                require(client.connect(), "RECOVERY_MODBUS_CONNECT_FAILED")
                before = stable(driver, client, remote={0, 1})
                valid_now(checked)
                marker_missing(driver)
                enabled = before["registers"]["remote_enable_44000"] == 1
                record = {"schema_version": 1, "serial_number": SERIAL,
                          "recovery_id": RECOVERY_ID,
                          "device_uuid": checked["device_uuid"],
                          "command_id": checked["command_id"],
                          "created_at": now().isoformat(),
                          "state": "write_intent" if enabled else "verification_intent",
                          "before": before, "write_attempted": enabled,
                          "route_sha256": hashlib.sha256(
                              runtime._route(config).encode()).hexdigest()}
                _audit(record)  # One-shot right consumed *before* possible hardware write.
                if enabled:
                    marker_missing(driver)
                    valid_now(checked)
                    evidence["write_calls"] = 1
                    try:
                        response = client.write_register(
                            address=44000, value=0, device_id=247)
                        evidence["write_ack"] = (response is not None and not response.isError())
                    except Exception as exc:
                        evidence["write_ack"] = False
                        evidence["write_error_type"] = type(exc).__name__
                    time.sleep(driver.MODBUS_WRITE_SETTLE_SECONDS)
                after = stable(driver, client, remote={0})
                expected = {**before,
                            "registers": {**before["registers"],
                                          "remote_enable_44000": 0}}
                require(after == expected, "RECOVERY_UNEXPECTED_HARDWARE_CHANGE")
                record.update(state="hardware_verified", after=after,
                              verified_at=now().isoformat())
                _update_audit(record)
                evidence.update(before=before, after=after,
                                released=enabled)
            finally:
                client.close()
        marker_missing(driver)
        outcome = runtime._RUNTIME.submit(
            payload=auto_payload(state),
            command_id=checked["command_id"], cloud_config=config)
        rt, applied = outcome.get("local_runtime", {}), outcome.get("applied", {})
        require(rt.get("state") == "stopped" and rt.get("action") == "auto"
                and rt.get("verified") is True and rt.get("applied_power_w") == 0
                and applied.get("action") == "auto"
                and applied.get("verified") is True
                and applied.get("write_performed") is False,
                "RECOVERY_NORMAL_STOP_FAILED")
        final = services.probe(cloud_config=config)
        require(final.get("read_only") is True
                and final.get("write_performed") is False
                and final.get("owner_marker", {}).get("status") == "missing",
                "RECOVERY_FINAL_PROBE_INVALID")
        regs = final.get("registers", {})
        require(all(regs.get(k) == v for k, v in (
            ("work_mode_41000", 0),
            ("max_soc_41010", 100),
            ("remote_enable_44000", 0),
            ("remote_timeout_44001", 20804),
            ("remote_active_power_w_44002", 0))),
            "RECOVERY_FINAL_REGISTERS_CHANGED")
        require(final.get("firmware_versions_raw") == EXPECTED_FIRMWARE,
                "RECOVERY_FINAL_FIRMWARE_CHANGED")
        record.update(state="completed", completed_at=now().isoformat(),
                      normal_stop={"state": rt["state"], "verified": True},
                      final_registers=regs)
        _update_audit(record)
        evidence.update(phase="zero_remote_released" if enabled
                        else "already_zero_verified", verified=True,
                        normal_stop=record["normal_stop"])


def execute_foxess_00018_owner_recovery(*, identity, command_id,
                                       command_payload, submit_result):
    """One scoped action via the already existing FoxESS release dispatcher."""
    evidence = {"executor": "f99_00018_owner_recovery",
                "recovery_id": RECOVERY_ID, "serial_number": SERIAL,
                "phase": "rejected_or_uncertain", "verified": False,
                "write_calls": 0, "automatic_retry": False}
    try:
        from foxess_zero_remote_release import _services
        services = _services()
        checked = check_request(identity=identity, serial=services.serial(),
                                command_id=command_id, payload=command_payload)
        release(services, checked, evidence)
    except Exception as exc:
        evidence.update(error_type=type(exc).__name__, verified=False)
        submit_result(identity=identity, command_id=command_id, status="failed",
                      result=evidence, error_message=str(exc)[:500])
        return
    submit_result(identity=identity, command_id=command_id, status="succeeded",
                  result=evidence, error_message=None)
