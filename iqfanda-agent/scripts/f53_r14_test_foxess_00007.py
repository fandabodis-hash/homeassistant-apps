"""Test the user's already installed 0.1.168 on 00007; never update or restart.

The bundled R13 module supplies transport helpers only. Every command passes a
local whitelist and the bound 00007 identity. Protected installations are read
in short transactions; their historical R13 mismatch remains unresolved.
"""
import hashlib
import hmac
import json
import secrets

from sqlalchemy import func, or_
import f53_r13_deploy_foxess as base
from f53_protected_config_audit import make_snapshot, diff_snapshots
from f53_energy_context import capture_energy_context

SERIAL = "F800711-TNG-00007"
SOURCE = "F53_R14_TEST_FOXESS_00007"
PROTECTED = ("F800711-TNG-00012", "F800711-TNG-00013", "F800711-TNG-00018")
LOCK_ID = 53016807
PROFILE = "foxess_h3_10_0_e"
DIGEST_KEY = secrets.token_bytes(32)
CONFIG_FIELDS = ("remote_commands_enabled", "cloud_enabled", "settings", "active_modules",
                 "config_version", "timezone", "language", "heartbeat_interval_seconds",
                 "sync_interval_seconds", "telemetry_interval_seconds")
base.SERIAL, base.SOURCE, base.PROTECTED = SERIAL, SOURCE, PROTECTED
base.RAW_BASE = ("https://raw.githubusercontent.com/fandabodis-hash/homeassistant-apps/"
                 "68281fa63ced8d5093dce6fb452149b0dea18120/iqfanda-agent/rootfs/app/")
_base_selected = base.selected
_binding = None
_new_canary_id = None


def selected(db):
    global _binding
    base.require(base.SERIAL == SERIAL and base.SOURCE == SOURCE and SERIAL not in PROTECTED,
                 "R14_TARGET_CONSTANTS_CHANGED")
    device = _base_selected(db)
    base.require(device.serial_number == SERIAL and device.site_id is not None,
                 "R14_TARGET_IS_NOT_00007")
    identity = (str(device.id), str(device.device_uuid), str(device.site_id))
    if _binding is None:
        _binding = identity
    base.require(identity == _binding, "R14_TARGET_BINDING_CHANGED")
    return device


base.selected = selected


def read_only(db):
    db.execute(base.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))


def digest(value):
    return hmac.new(DIGEST_KEY, json.dumps(value, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()


def target_identity(*, log=True):
    with base.SessionLocal() as db:
        read_only(db)
        device = selected(db)
        module = db.scalar(base.select(base.InstallationModule).where(
            base.InstallationModule.site_id == device.site_id, base.InstallationModule.module_key == "photovoltaic"))
        cfg = base.obj(module.configuration) if module else {}
        identity = {"serial": SERIAL, "device_id": str(device.id), "site_id": str(device.site_id),
            "agent_version": device.software_version, "last_seen_at": device.last_seen_at,
            "manufacturer": str((module.manufacturer if module else None) or cfg.get("manufacturer") or "")[:120],
            "model": str((module.model if module else None) or cfg.get("model") or "")[:120]}
        if log:
            base.out("TARGET_IDENTITY", identity)
        base.require(device.software_version == "0.1.168" and base.VERSION == "0.1.168",
                     "R14_REQUIRES_USER_INSTALLED_0168: no update or restart was requested")
        base.require(base.fresh(device.last_seen_at), "R14_FRESH_0168_HEARTBEAT_REQUIRED")
        return identity


def queue(command_type, payload, seconds):
    allowed = False
    if command_type == "foxess_remote_control_probe":
        allowed = (payload.get("read_only") is True and set(payload) == {"read_only", "r14_stage"}
                   and payload.get("r14_stage") in {"before_auto", "after_auto"})
    elif command_type == "spot_battery_intent":
        allowed = (payload.get("action") == "auto" and payload.get("manual_immediate_charge") is True
                   and payload.get("control_source") == "manual_immediate_charge"
                   and payload.get("reason") == "f53_r14_commissioning_stop"
                   and all(type(payload.get(k)) is int and payload[k] == 0 for k in (
                       "requested_charge_power_w", "allowed_charge_power_w",
                       "requested_discharge_power_w", "allowed_discharge_power_w")))
    elif command_type == "manual_battery_canary_v1":
        allowed = (payload == base.canary_payload() and payload.get("commissioning") is True
                   and type(payload.get("power_w")) is int and payload["power_w"] == 500
                   and payload.get("expected_agent_version") == "0.1.168"
                   and payload.get("expected_runtime_fingerprint") == base.EXPECTED_RUNTIME_FINGERPRINT)
    base.require(allowed, "R14_COMMAND_NOT_ALLOWED")
    target_identity(log=False)
    return base.queue(command_type, payload, seconds)


def capture_protected():
    result = {"devices": {}}
    with base.SessionLocal() as db:
        read_only(db)
        result["capture_started_at"] = db.execute(base.text("SELECT clock_timestamp()")).scalar().isoformat()
        for serial in PROTECTED:
            device = db.scalar(base.select(base.Device).where(base.Device.serial_number == serial))
            if device is None:
                result["devices"][serial] = {"present": False}
                continue
            config = db.scalar(base.select(base.DeviceConfig).where(base.DeviceConfig.device_id == device.id))
            modules = db.scalars(base.select(base.InstallationModule).where(
                base.InstallationModule.site_id == device.site_id).order_by(base.InstallationModule.module_key)).all()
            snapshot = make_snapshot({k: getattr(config, k, None) for k in CONFIG_FIELDS},
                [{"module": m.module_key, "status": m.status, "configuration": m.configuration,
                  "manufacturer": m.manufacturer, "model": m.model} for m in modules], digest_key=DIGEST_KEY)
            result["devices"][serial] = {"present": True, "device_id": str(device.id), "site_id": str(device.site_id),
                "agent_version": device.software_version, "config_present": config is not None,
                "config_version": getattr(config, "config_version", None),
                "config_updated_at": str(getattr(config, "updated_at", None)),
                "module_rows": [{"id": str(m.id), "module_key_hmac": digest(m.module_key),
                                 "updated_at": str(m.updated_at)} for m in modules], **snapshot}
        result["capture_finished_at"] = db.execute(base.text("SELECT clock_timestamp()")).scalar().isoformat()
    return result


def public_snapshot(snapshot):
    return {**snapshot, "devices": {s: {k: v for k, v in item.items() if not k.startswith("_")}
                                    for s, item in snapshot["devices"].items()}}


def protected_differences(before, after):
    result = {}
    for serial in PROTECTED:
        a, b = before["devices"].get(serial, {}), after["devices"].get(serial, {})
        difference = diff_snapshots(a, b) if a.get("present") and b.get("present") else {"comparison": "unavailable"}
        difference["identity_or_presence_changed"] = any(a.get(k) != b.get(k) for k in (
            "present", "device_id", "site_id", "config_present"))
        difference["module_identity_changed"] = {
            (m["id"], m["module_key_hmac"]) for m in a.get("module_rows", [])} != {
            (m["id"], m["module_key_hmac"]) for m in b.get("module_rows", [])}
        result[serial] = difference
    return result


def command_audit(start, end):
    report = {"from_utc": start, "to_utc": end, "scope": "protected_sites_read_only",
              "actor_attribution": "not_inferred_from_command_counts", "devices": {}}
    with base.SessionLocal() as db:
        read_only(db)
        protected = db.scalars(base.select(base.Device).where(base.Device.serial_number.in_(PROTECTED))).all()
        sites = [d.site_id for d in protected if d.site_id is not None]
        scope = db.scalars(base.select(base.Device).where(base.Device.site_id.in_(sites))).all() if sites else []
        for device in scope:
            window = or_(base.DeviceCommand.created_at.between(start, end), base.DeviceCommand.claimed_at.between(start, end),
                         base.DeviceCommand.completed_at.between(start, end), base.DeviceCommand.updated_at.between(start, end))
            where = (base.DeviceCommand.device_id == device.id, window)
            count = db.scalar(base.select(func.count()).select_from(base.DeviceCommand).where(*where))
            own_count = db.scalar(base.select(func.count()).select_from(base.DeviceCommand).where(
                *where, base.DeviceCommand.payload["source"].as_string() == SOURCE))
            rows = db.scalars(base.select(base.DeviceCommand).where(*where)
                .order_by(base.DeviceCommand.updated_at.desc()).limit(80)).all()
            items = []
            for command in rows:
                source = base.obj(command.payload).get("source")
                items.append({"id": str(command.id), "type": command.command_type, "status": command.status,
                    "r14_source": source == SOURCE, "source_hmac": digest(source),
                    "actor": "system_or_unspecified" if command.created_by_user_id is None
                             else "user:" + digest(str(command.created_by_user_id))[:16],
                    "created_at": command.created_at, "claimed_at": command.claimed_at,
                    "completed_at": command.completed_at, "updated_at": command.updated_at,
                    "payload_hmac": digest(command.payload), "result_hmac": digest(command.result)})
            report["devices"][device.serial_number] = {"device_id": str(device.id),
                "commands_created_or_updated_in_window": int(count or 0),
                "r14_tagged_commands_in_window": int(own_count or 0),
                "details_truncated": int(count or 0) > len(items), "commands": items}
    return report


def find_canary():
    with base.SessionLocal() as db:
        read_only(db)
        device = selected(db)
        for command in base.live_commands(db, device.id):
            if command.command_type == "manual_battery_canary_v1":
                base.require(base.obj(command.payload).get("source") == SOURCE, "ANOTHER_CANARY_IS_INFLIGHT")
        rows = db.scalars(base.select(base.DeviceCommand).where(base.DeviceCommand.device_id == device.id,
            base.DeviceCommand.command_type == "manual_battery_canary_v1",
            base.DeviceCommand.payload["source"].as_string() == SOURCE)).all()
        base.require(len(rows) <= 1, "MULTIPLE_R14_CANARIES_REQUIRE_REVIEW")
        if not rows:
            return None
        command = rows[0]
        base.require(command.device_id == device.id and base.obj(command.payload) == {**base.canary_payload(), "source": SOURCE},
                     "R14_CANARY_IDENTITY_OR_PAYLOAD_CHANGED")
        return {"id": command.id, "status": command.status, "result": base.obj(command.result)}


def probe(stage):
    command_id = queue("foxess_remote_control_probe", {"read_only": True, "r14_stage": stage}, 240)
    result = base.wait_command(command_id, timeout=240)
    base.require(result.get("read_only") is True and result.get("write_performed") is False
                 and result.get("profile_id") == PROFILE and result.get("protocol_version") == 115
                 and result.get("device_id") == 247 and result.get("serial_path_verified") is True,
                 "R14_READ_ONLY_FOXESS_IDENTITY_NOT_CONFIRMED")
    base.out("FOXESS_PROBE", {"stage": stage, **result})
    return result


def require_auto_owner(result):
    owner, registers = base.obj(result.get("owner_marker")), base.obj(result.get("registers"))
    base.require(owner.get("status") in {"owned", "missing"}, "FOXESS_OWNER_UNKNOWN_FOREIGN_OR_INVALID")
    base.require(registers.get("remote_enable_44000") in {0, 1}, "FOXESS_REMOTE_STATE_INVALID")
    if owner.get("status") == "missing":
        base.require(registers.get("remote_enable_44000") == 0, "FOXESS_FOREIGN_REMOTE_OWNER_DO_NOT_TAKE_OVER")


def require_auto_readback(result):
    registers = base.obj(result.get("registers"))
    base.require(registers.get("remote_enable_44000") == 0 and registers.get("remote_active_power_w_44002") == 0
                 and base.obj(result.get("owner_marker")).get("status") == "missing",
                 "AUTO_FINAL_READBACK_OR_OWNER_RELEASE_UNVERIFIED")


def canary_pending_reason(result):
    registers = base.obj(result.get("registers"))
    soc, maximum = registers.get("battery_soc_percent_31038"), registers.get("max_soc_41010")
    base.require(type(soc) is int and type(maximum) is int and 0 <= soc <= 100 and 0 <= maximum <= 100,
                 "FRESH_LOCAL_SOC_INVALID")
    if not 30 <= soc <= 90:
        return "local_soc_outside_30_90_window"
    if soc >= min(95, maximum):
        return "local_soc_leaves_no_charging_room"
    return None


def energy_snapshot(stage):
    """Optional read-only context hook; not evidence of concurrent canary power."""
    try:
        result = capture_energy_context(base, stage)
    except Exception:
        # Ancillary context must not mask a device's failed phase or cleanup.
        # Database exceptions can include connection details; use a fixed code.
        result = {"stage": stage, "status": "energy_context_unavailable",
                  "context_only": True, "concurrent_measurement_confirmed": False}
    base.out("ENERGY_CONTEXT", result)
    return result


def report_canary(result, command_id, *, reused=False, resumed=False):
    protection = base.obj(result.get("protection"))
    base.require(result.get("status") == "passed" and result.get("verified") is True
                 and result.get("local_final_auto_verified") is True and result.get("profile_id") == PROFILE
                 and result.get("agent_version") == "0.1.168" and result.get("command_id") == str(command_id)
                 and result.get("runtime_fingerprint") == base.EXPECTED_RUNTIME_FINGERPRINT
                 and result.get("verification_scope") == "bidirectional_500w_and_stop"
                 and protection.get("status") == "locked" and protection.get("profile_id") == PROFILE
                 and protection.get("runtime_fingerprint") == base.EXPECTED_RUNTIME_FINGERPRINT
                 and protection.get("verification_scope") == "bidirectional_500w_and_stop",
                 "R14_PHYSICAL_TEST_AND_PROTECTION_NOT_CONFIRMED")
    base.out("RESULT", {"status": "PREVIOUS_FOXESS_00007_CANARY_PASSED" if reused else "PASS_FOXESS_00007_LOCAL_AND_LOCKED",
        "serial": SERIAL, "agent_version": "0.1.168", "physical_test_command_id": str(command_id),
        "completed_evidence_reused": reused, "new_physical_test_queued": not (reused or resumed),
        "physical_canary_performed_in_this_run": not (reused or resumed),
        "existing_canary_completion_observed": resumed,
        "current_auto_reverified_by_this_invocation": not reused,
        "evidence_finished_at": result.get("finished_at"), "protection": protection,
        "verification_scope": result["verification_scope"], "other_installations_tested": False})


def run_test():
    global _new_canary_id
    existing = find_canary()
    if existing is not None:
        base.out("RESUME_CANARY", {"id": str(existing["id"]), "status": existing["status"], "new_physical_test_queued": False})
        if existing["status"] in base.ACTIVE:
            try:
                result = base.wait_command(existing["id"], timeout=420)
            finally:
                energy_snapshot("after_canary")
        elif existing["status"] == "succeeded":
            result = existing["result"]
        else:
            raise RuntimeError("R14_CANARY_ALREADY_ATTEMPTED: no automatic second physical test")
        target_identity(log=False)
        report_canary(result, existing["id"], reused=existing["status"] == "succeeded",
                      resumed=existing["status"] in base.ACTIVE)
        return 0
    state = base.preflight(require_version=True)
    energy_snapshot("before_auto")
    require_auto_owner(probe("before_auto"))
    payload = {**base.auto_payload(state), "reason": "f53_r14_commissioning_stop"}
    auto_id = queue("spot_battery_intent", payload, 180)
    result = base.wait_command(auto_id, timeout=180)
    base.require(result.get("verified") is True and result.get("action") == "auto", "BASELINE_AUTO_UNVERIFIED")
    confirmed = probe("after_auto")
    require_auto_readback(confirmed)
    energy_snapshot("after_auto")
    pending = canary_pending_reason(confirmed)
    if pending:
        base.out("RESULT", {"status": "AUTO_VERIFIED_CANARY_PENDING_SOC", "serial": SERIAL,
            "agent_version": "0.1.168", "physical_canary_performed": False, "new_verification_pin_written": False,
            "physical_canary_performed_in_this_run": False, "completed_evidence_reused": False,
            "reason": pending, "local_soc_percent": confirmed["registers"]["battery_soc_percent_31038"]})
        return 3
    base.preflight(require_version=True)
    energy_snapshot("before_canary")
    command_id = queue("manual_battery_canary_v1", base.canary_payload(), 420)
    _new_canary_id = command_id
    try:
        result = base.wait_command(command_id, timeout=420)
    finally:
        energy_snapshot("after_canary")
    target_identity(log=False)
    report_canary(result, command_id)
    return 0


def main():
    global _new_canary_id
    _new_canary_id = None
    base.out("SCOPE", {"revision": SOURCE, "target": SERIAL, "protected": PROTECTED,
        "required_user_installed_version": "0.1.168", "addon_update": False, "restart": False,
        "module_configuration_writes": False, "physical_canary_w": 500,
        "r13_original_mismatch": "unresolved"})
    before, lock_db, lock_acquired = None, None, False
    started, exit_code = base.now(), 2
    try:
        base.release_preflight()
        lock_db = base.SessionLocal()
        lock_acquired = lock_db.execute(base.text("SELECT pg_try_advisory_lock(53016807)")).scalar() is True
        base.require(lock_acquired, "ANOTHER_F53_00007_TEST_IS_RUNNING")
        target_identity()
        before = capture_protected()
        base.out("PROTECTED_BEFORE", public_snapshot(before))
        base.require(not any(item.get("present") and item.get("site_id") == _binding[2]
                             for item in before["devices"].values()), "R14_TARGET_SHARES_A_PROTECTED_SITE")
        exit_code = run_test()
    except Exception as exc:
        base.out("RESULT", {"status": "R14_TEST_NOT_CONFIRMED", "serial": SERIAL,
            "error_type": type(exc).__name__, "error": str(exc)[:1600],
            "automatic_update_or_restart": False, "automatic_second_canary": False,
            "physical_canary_performed_in_this_run": None if _new_canary_id is not None else False,
            "new_physical_test_queued": _new_canary_id is not None,
            "physical_test_command_id": str(_new_canary_id) if _new_canary_id is not None else None,
            "completed_evidence_reused": False,
            "instruction": "Preserve pending operations and owner markers; review this report."})
    finally:
        try:
            if before is not None:
                after = capture_protected()
                base.out("PROTECTED_AFTER", public_snapshot(after))
                base.out("PROTECTED_FIELD_DIFF", {"r13_original_mismatch": "unresolved",
                    "new_window": protected_differences(before, after), "automatic_configuration_restore": False})
                audit = command_audit(started, base.now())
                base.out("R14_COMMAND_AUDIT", audit)
                if any(d["r14_tagged_commands_in_window"] for d in audit["devices"].values()):
                    exit_code = 2
                    base.out("PROTECTED_SCOPE_VIOLATION", {"r14_commands_outside_target": True})
        except Exception as exc:
            exit_code = 2
            base.out("PROTECTED_AUDIT_ERROR", {"type": type(exc).__name__})
        finally:
            if lock_db is not None:
                try:
                    if lock_acquired:
                        base.require(lock_db.execute(base.text("SELECT pg_advisory_unlock(53016807)")).scalar() is True,
                                     "R14_LOCK_RELEASE_UNCONFIRMED")
                except Exception:
                    exit_code = 2
                    lock_db.invalidate()
                finally:
                    lock_db.close()
    base.out("RUN_RESULT", {"serial": SERIAL, "exit_code": exit_code,
        "status": {0: "PASS", 3: "CANARY_PENDING_SOC"}.get(exit_code, "NOT_CONFIRMED"),
        "r13_original_mismatch": "unresolved"})
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
