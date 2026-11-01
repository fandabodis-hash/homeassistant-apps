"""Finish the audited 00018 upgrade, once, without changing protected settings.

Run from the backend using the bundled PS1. The existing R13 deployment module
and pure snapshot helper are packaged alongside this module, without installing
files or modifying backend/frontend source in the running container.
"""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
import uuid
from datetime import timedelta

from sqlalchemy import func, or_
import f53_r13_deploy_foxess as base
from f53_protected_config_audit import make_snapshot, diff_snapshots

ORIGINAL_COMMAND_ID = "7c5cfc5d-2325-49c6-b23a-c25f8c4f9b63"
ORIGINAL_SOURCE = "F53_R13_LOCAL_FOXESS_00018"
SOURCE = "F53_R13R1_00018_AFTER_7c5cfc5d"
SOURCE_VERSION = "0.1.167"
SOURCE_TAGS = (ORIGINAL_SOURCE, SOURCE)
base.SOURCE = SOURCE
DIGEST_KEY = secrets.token_bytes(32)
CONFIG_FIELDS = ("remote_commands_enabled", "cloud_enabled", "settings", "active_modules",
                 "config_version", "timezone", "language", "heartbeat_interval_seconds",
                 "sync_interval_seconds", "telemetry_interval_seconds")


def digest(value):
    return hmac.new(DIGEST_KEY, json.dumps(value, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()


def read_only(db):
    db.execute(base.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))


def verify_original_attempt():
    with base.SessionLocal() as db:
        read_only(db)
        device = base.selected(db)
        command = db.get(base.DeviceCommand, uuid.UUID(ORIGINAL_COMMAND_ID))
        base.require(command is not None and command.device_id == device.id,
                     "ORIGINAL_UPDATE_IDENTITY_NOT_CONFIRMED")
        payload = base.obj(command.payload)
        result = base.obj(command.result)
        base.require(command.command_type == "agent_update" and payload.get("source") == ORIGINAL_SOURCE
                     and payload.get("target_version") == base.VERSION and payload.get("backup") is True,
                     "ORIGINAL_UPDATE_PAYLOAD_CHANGED")
        base.require(command.status == "failed" and result.get("phase") == "update_timeout"
                     and result.get("executor") == "home_assistant_core_agent_update"
                     and result.get("installed_version") == SOURCE_VERSION
                     and result.get("target_version") == base.VERSION,
                     "ORIGINAL_UPDATE_IS_NOT_THE_REVIEWED_TERMINAL_ATTEMPT")
        window_start = base.aware(command.created_at) - timedelta(seconds=60)
        window_end = base.aware(command.completed_at or command.updated_at) + timedelta(seconds=60)
        base.out("ORIGINAL_ATTEMPT", {"id": ORIGINAL_COMMAND_ID, "status": command.status,
            "phase": result["phase"], "installed_version": result["installed_version"],
            "created_at": command.created_at, "completed_at": command.completed_at,
            "historical_audit_window_is_approximate": True})
        return window_start, window_end


def capture_protected():
    result = {"capture_started_at": None, "capture_finished_at": None, "devices": {}}
    with base.SessionLocal() as db:
        read_only(db)
        result["capture_started_at"] = db.execute(base.text("SELECT clock_timestamp()" )).scalar().isoformat()
        for serial in base.PROTECTED:
            device = db.scalar(base.select(base.Device).where(base.Device.serial_number == serial))
            if device is None:
                result["devices"][serial] = {"present": False}
                continue
            config = db.scalar(base.select(base.DeviceConfig).where(base.DeviceConfig.device_id == device.id))
            modules = db.scalars(base.select(base.InstallationModule).where(
                base.InstallationModule.site_id == device.site_id).order_by(base.InstallationModule.module_key)).all()
            config_value = {key: getattr(config, key, None) for key in CONFIG_FIELDS}
            module_values = [{"module": m.module_key, "status": m.status, "configuration": m.configuration,
                              "manufacturer": m.manufacturer, "model": m.model} for m in modules]
            snapshot = make_snapshot(config_value, module_values, digest_key=DIGEST_KEY)
            result["devices"][serial] = {"present": True, "device_id": str(device.id),
                "site_id": str(device.site_id), "agent_version": device.software_version,
                "config_present": config is not None,
                "config_version": getattr(config, "config_version", None),
                "config_updated_at": str(getattr(config, "updated_at", None)),
                "modules_updated_at": {m.module_key: str(m.updated_at) for m in modules},
                **snapshot}
        result["capture_finished_at"] = db.execute(base.text("SELECT clock_timestamp()" )).scalar().isoformat()
    return result


def public_snapshot(snapshot):
    return {**snapshot, "devices": {serial: {k: v for k, v in item.items() if not k.startswith("_")}
                                    for serial, item in snapshot["devices"].items()}}


def command_audit(start, end):
    report = {"from_utc": start, "to_utc": end, "scope": "protected_sites_read_only",
              "actor_attribution": "not_inferred_from_command_counts", "devices": {}}
    with base.SessionLocal() as db:
        read_only(db)
        protected = db.scalars(base.select(base.Device).where(base.Device.serial_number.in_(base.PROTECTED))).all()
        site_ids = [d.site_id for d in protected if d.site_id is not None]
        scope = db.scalars(base.select(base.Device).where(base.Device.site_id.in_(site_ids))).all() if site_ids else []
        for device in scope:
            in_window = or_(
                base.DeviceCommand.created_at.between(start, end),
                base.DeviceCommand.claimed_at.between(start, end),
                base.DeviceCommand.completed_at.between(start, end),
                base.DeviceCommand.updated_at.between(start, end))
            where = (base.DeviceCommand.device_id == device.id, in_window)
            count = db.scalar(base.select(func.count()).select_from(base.DeviceCommand).where(*where))
            tagged = db.scalar(base.select(func.count()).select_from(base.DeviceCommand).where(
                *where, base.DeviceCommand.payload["source"].as_string().in_(SOURCE_TAGS)))
            rows = db.scalars(base.select(base.DeviceCommand).where(*where)
                .order_by(base.DeviceCommand.updated_at.desc()).limit(80)).all()
            items = []
            for c in rows:
                source = base.obj(c.payload).get("source")
                safe_source = source if isinstance(source, str) and (source in SOURCE_TAGS or source in {
                    "manual_immediate_charge", "spot", "spot_prices", "photovoltaic"}) else None
                items.append({"id": str(c.id), "type": c.command_type, "status": c.status,
                    "source": safe_source, "other_source_hmac": digest(source) if source and safe_source is None else None,
                    "actor": "system_or_unspecified" if c.created_by_user_id is None else "user:" + digest(str(c.created_by_user_id))[:16],
                    "created_at": c.created_at, "claimed_at": c.claimed_at,
                    "completed_at": c.completed_at, "updated_at": c.updated_at,
                    "payload_hmac": digest(c.payload), "result_hmac": digest(c.result)})
            report["devices"][device.serial_number] = {"device_id": str(device.id),
                "commands_created_or_updated_in_window": int(count or 0),
                "r13_or_r13r1_tagged_commands_in_window": int(tagged or 0),
                "details_truncated": count > len(items), "commands": items}
    return report


def backend_source_hashes():
    paths = ("app/routers/device.py", "app/services/phase34_00013_heating_permission_service.py",
             "app/services/phase34_heating_setpoint_service.py")
    result = {}
    for relative in paths:
        path = Path("/app") / relative
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return result


def recovery_update_payload():
    return {"target_version": base.VERSION, "backup": True, "recovery_of": ORIGINAL_COMMAND_ID}


def find_recovery_update():
    with base.SessionLocal() as db:
        device = base.selected(db)
        rows = db.scalars(base.select(base.DeviceCommand).where(base.DeviceCommand.device_id == device.id,
            base.DeviceCommand.command_type == "agent_update",
            base.DeviceCommand.payload["source"].as_string() == SOURCE)).all()
        base.require(len(rows) <= 1, "MULTIPLE_RECOVERY_UPDATES_REQUIRE_REVIEW")
        if not rows:
            return None
        command = rows[0]
        base.require(base.obj(command.payload) == {**recovery_update_payload(), "source": SOURCE},
                     "RECOVERY_UPDATE_PAYLOAD_CHANGED")
        return {"id": command.id, "status": command.status, "result": base.obj(command.result),
                "error": str(command.error_message or "")[:1200]}


def current_version():
    with base.SessionLocal() as db:
        device = base.selected(db)
        base.require(base.fresh(device.last_seen_at), "TARGET_HEARTBEAT_STALE")
        base.require(device.software_version in {SOURCE_VERSION, base.VERSION},
                     "RECOVERY_REQUIRES_EXACT_0167_OR_ALREADY_INSTALLED_0168")
        return device.software_version


def find_recovery_canary():
    with base.SessionLocal() as db:
        device = base.selected(db)
        for c in base.live_commands(db, device.id):
            if c.command_type == "manual_battery_canary_v1":
                base.require(base.obj(c.payload).get("source") == SOURCE, "ANOTHER_CANARY_IS_INFLIGHT")
        rows = db.scalars(base.select(base.DeviceCommand).where(base.DeviceCommand.device_id == device.id,
            base.DeviceCommand.command_type == "manual_battery_canary_v1",
            base.DeviceCommand.payload["source"].as_string() == SOURCE)).all()
        base.require(len(rows) <= 1, "MULTIPLE_CANARY_OPERATIONS_REQUIRE_REVIEW")
        if not rows:
            return None
        command = rows[0]
        base.require(base.obj(command.payload) == {**base.canary_payload(), "source": SOURCE},
                     "ANOTHER_CANARY_IS_INFLIGHT")
        return {"id": command.id, "status": command.status, "result": base.obj(command.result),
                "error": str(command.error_message or "")[:1200]}


def wait_update(command_id):
    deadline = time.monotonic() + base.UPDATE_TIMEOUT
    last = None
    last_log = 0.0
    while time.monotonic() < deadline:
        with base.SessionLocal() as db:
            command = db.get(base.DeviceCommand, command_id)
            device = base.selected(db)
            base.require(command is not None and command.device_id == device.id, "RECOVERY_UPDATE_SCOPE_CHANGED")
            result = base.obj(command.result)
            phase = result.get("phase")
            current = (command.status, phase, device.software_version)
            if current != last or time.monotonic() - last_log >= 25:
                stage = "metadata_refresh_or_validation" if phase is None else phase
                base.out("UPDATE_WAIT", {"id": str(command_id), "status": command.status,
                    "stage": stage, "agent_version": device.software_version})
                last, last_log = current, time.monotonic()
            if command.status in {"failed", "cancelled", "expired", "succeeded"}:
                base.out("UPDATE_RESULT", {"id": str(command_id), "status": command.status,
                    "result": result, "error": str(command.error_message or "")[:1600]})
                base.require(command.status == "succeeded", "RECOVERY_UPDATE_FAILED_NO_AUTOMATIC_RETRY: " + str(command.error_message or phase))
                base.require(result.get("installed_version") == base.VERSION and result.get("target_version") == base.VERSION,
                             "RECOVERY_UPDATE_RESULT_VERSION_MISMATCH")
                return result
            if base.fresh(device.last_seen_at) and device.software_version not in {SOURCE_VERSION, base.VERSION}:
                raise RuntimeError("UNEXPECTED_AGENT_VERSION: operation state preserved; no second update or canary")
        time.sleep(base.POLL_SECONDS)
    raise RuntimeError("RECOVERY_UPDATE_WAIT_TIMEOUT: existing operation preserved; no duplicate request")


def probe(stage):
    command_id = base.queue("foxess_remote_control_probe", {"read_only": True, "recovery_stage": stage}, 240)
    result = base.wait_command(command_id, timeout=240)
    validate_probe(result)
    base.out("FOXESS_PROBE", {"stage": stage, **result})
    return result


def validate_probe(result):
    base.require(result.get("read_only") is True and result.get("write_performed") is False
                 and result.get("profile_id") == "foxess_h3_10_0_e"
                 and result.get("protocol_version") == 115 and result.get("device_id") == 247,
                 "READ_ONLY_FOXESS_IDENTITY_NOT_CONFIRMED")


def require_update_power_idle(result):
    registers = base.obj(result.get("registers"))
    base.require(registers.get("remote_enable_44000") in {0, 1}
                 and registers.get("remote_active_power_w_44002") == 0,
                 "OLD_AGENT_REMOTE_SETPOINT_IS_NOT_ZERO: update and canary were not started")


def require_auto_owner(result):
    owner = base.obj(result.get("owner_marker"))
    registers = base.obj(result.get("registers"))
    state = owner.get("status")
    base.require(state in {"owned", "missing"}, "FOXESS_OWNER_UNKNOWN_FOREIGN_OR_INVALID")
    base.require(registers.get("remote_enable_44000") in {0, 1}, "FOXESS_REMOTE_STATE_INVALID")
    if state == "missing":
        base.require(registers.get("remote_enable_44000") == 0,
                     "FOXESS_FOREIGN_REMOTE_OWNER_DO_NOT_TAKE_OVER")


def require_auto_readback(result):
    registers = base.obj(result.get("registers"))
    base.require(registers.get("remote_enable_44000") == 0
                 and registers.get("remote_active_power_w_44002") == 0
                 and base.obj(result.get("owner_marker")).get("status") == "missing",
                 "AUTO_FINAL_READBACK_OR_OWNER_RELEASE_UNVERIFIED")


def canary_pending_reason(result):
    registers = base.obj(result.get("registers"))
    soc = registers.get("battery_soc_percent_31038")
    max_soc = registers.get("max_soc_41010")
    base.require(type(soc) is int and type(max_soc) is int and 0 <= soc <= 100 and 0 <= max_soc <= 100,
                 "FRESH_LOCAL_SOC_INVALID")
    if not 30 <= soc <= 90:
        return "local_soc_outside_30_90_window"
    if soc >= min(95, max_soc):
        return "local_soc_leaves_no_charging_room"
    return None


def finish_installed_agent():
    state = base.preflight(require_version=True)
    baseline = probe("installed_0168_before_auto")
    require_auto_owner(baseline)
    auto_id = base.queue("spot_battery_intent", base.auto_payload(state), 180)
    auto_result = base.wait_command(auto_id, timeout=180)
    base.require(auto_result.get("verified") is True and auto_result.get("action") == "auto",
                 "BASELINE_AUTO_UNVERIFIED")
    confirmed = probe("installed_0168_after_auto")
    require_auto_readback(confirmed)
    pending = canary_pending_reason(confirmed)
    if pending:
        base.out("RESULT", {"status": "INSTALLED_AUTO_VERIFIED_CANARY_PENDING_SOC", "agent_version": base.VERSION,
            "serial": base.SERIAL, "physical_canary_performed": False, "new_verification_pin_written": False,
            "reason": pending, "local_soc_percent": confirmed["registers"]["battery_soc_percent_31038"],
            "local_max_soc_percent": confirmed["registers"]["max_soc_41010"]})
        return 3
    base.preflight(require_version=True)
    canary_id = base.queue("manual_battery_canary_v1", base.canary_payload(), 420)
    result = base.wait_command(canary_id, timeout=420)
    base.report_canary_success(result, canary_id)
    return 0


def main():
    base.out("SCOPE", {"revision": SOURCE, "target": base.SERIAL, "source_version": SOURCE_VERSION,
        "target_version": base.VERSION, "recovery_of": ORIGINAL_COMMAND_ID,
        "maximum_new_update_requests": 1, "module_configuration_writes": False,
        "r13_original_protected_mismatch": "unresolved", "hardware_fingerprint_changed": False})
    before = None
    lock_db = None
    lock_acquired = False
    run_started_at = base.now()
    try:
        base.release_preflight()
        lock_db = base.SessionLocal()
        lock_acquired = lock_db.execute(base.text("SELECT pg_try_advisory_lock(53016818)")).scalar() is True
        base.require(lock_acquired, "ANOTHER_F53_INSTALLER_IS_RUNNING")
        prior_start, prior_end = verify_original_attempt()
        before = capture_protected()
        base.out("PROTECTED_BEFORE", public_snapshot(before))
        base.out("BACKEND_SOURCE_HASHES", backend_source_hashes())
        base.out("R13_HISTORICAL_COMMAND_AUDIT", command_audit(prior_start, prior_end))
        recovery = find_recovery_update()
        version = current_version()
        if recovery and recovery["status"] in base.ACTIVE:
            base.out("RESUME_UPDATE", {"id": str(recovery["id"]), "status": recovery["status"]})
            wait_update(recovery["id"])
            base.wait_version()
        # A previous terminal failed recovery is evidence to review, not a
        # reason to silently create another update on every invocation.
        elif recovery and recovery["status"] != "succeeded" and version != base.VERSION:
            base.out("PREVIOUS_RECOVERY_RESULT", recovery)
            raise RuntimeError("RECOVERY_UPDATE_ALREADY_ATTEMPTED: no new update was created")
        canary = find_recovery_canary()
        if canary is not None:
            base.out("RESUME_CANARY", {"id": str(canary["id"]), "status": canary["status"],
                "completed_evidence_reused": canary["status"] == "succeeded", "new_physical_test_queued": False})
            if canary["status"] in base.ACTIVE:
                result = base.wait_command(canary["id"], timeout=420)
            elif canary["status"] == "succeeded":
                result = canary["result"]
            else:
                base.out("PREVIOUS_CANARY_RESULT", canary)
                raise RuntimeError("RECOVERY_CANARY_ALREADY_ATTEMPTED: no automatic second physical test")
            base.wait_version()
            base.report_canary_success(result, canary["id"])
            return 0
        state = base.preflight()
        base.require(state["agent_version"] in {SOURCE_VERSION, base.VERSION},
                     "RECOVERY_REQUIRES_EXACT_0167_OR_ALREADY_INSTALLED_0168")
        if state["agent_version"] == SOURCE_VERSION:
            base.require(recovery is None, "RECOVERY_RESULT_AND_CURRENT_VERSION_DISAGREE")
            old_probe = probe("old_0167_worker_ready_read_only")
            require_update_power_idle(old_probe)
            state = base.preflight()
            base.require(state["agent_version"] == SOURCE_VERSION, "SOURCE_VERSION_CHANGED_BEFORE_UPDATE")
            update_id = base.queue("agent_update", recovery_update_payload(), base.UPDATE_TIMEOUT + 120)
            wait_update(update_id)
            base.wait_version()
        base.out("ADDON_INSTALLED", {"serial": base.SERIAL, "version": base.VERSION})
        return finish_installed_agent()
    except Exception as exc:
        base.out("RESULT", {"status": "RECOVERY_NOT_CONFIRMED", "serial": base.SERIAL,
            "error_type": type(exc).__name__, "error": str(exc)[:1800],
            "automatic_second_update": False, "instruction": "Review this report; preserve pending operations and owner markers."})
        return 2
    finally:
        try:
            if before is not None:
                after = capture_protected()
                base.out("PROTECTED_AFTER", public_snapshot(after))
                differences = {}
                for serial in base.PROTECTED:
                    a, b = before["devices"].get(serial, {}), after["devices"].get(serial, {})
                    differences[serial] = diff_snapshots(a, b) if a.get("present") and b.get("present") else {
                        "presence_changed": a.get("present") != b.get("present"), "comparison": "unavailable"}
                base.out("PROTECTED_FIELD_DIFF", {"r13_original_mismatch": "unresolved",
                    "new_window": differences, "automatic_configuration_restore": False})
                base.out("RECOVERY_COMMAND_AUDIT", command_audit(run_started_at, base.now()))
        except Exception as exc:
            base.out("PROTECTED_AUDIT_ERROR", {"type": type(exc).__name__, "error": str(exc)[:500]})
        finally:
            if lock_db is not None:
                try:
                    if lock_acquired:
                        base.require(lock_db.execute(base.text("SELECT pg_advisory_unlock(53016818)")).scalar() is True,
                                     "INSTALLER_LOCK_RELEASE_UNCONFIRMED")
                except Exception:
                    lock_db.invalidate()
                finally:
                    lock_db.close()


if __name__ == "__main__":
    raise SystemExit(main())
