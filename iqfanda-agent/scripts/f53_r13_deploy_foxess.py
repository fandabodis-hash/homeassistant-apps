"""Run inside the existing backend; queue only the explicitly selected 00018.

No backend/frontend deployment or module configuration change. The new add-on
performs the bounded physical canary and records its own verification pin.
"""
from __future__ import annotations
import hashlib
import json
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen

from sqlalchemy import select, text
from app.database import SessionLocal
from app.models.device import Device
from app.models.device_config import DeviceConfig
from app.models.device_status import DeviceStatus
from app.models.device_command import DeviceCommand
from app.models.installation_module import InstallationModule
from app.models.module_entity_state import ModuleEntityState
from app.models.spot_resource_runtime_state import SpotResourceRuntimeState

SERIAL = "F800711-TNG-00018"
PROTECTED = ("F800711-TNG-00012", "F800711-TNG-00013")
VERSION = "0.1.168"
EXPECTED_RUNTIME_FINGERPRINT = "e0faee6ae3e367f75c40590410c4b2d71cc021541cee0a20939aa6313f7e1c95"
SOURCE = "F53_R13_LOCAL_FOXESS_00018"
RAW_BASE = "https://raw.githubusercontent.com/fandabodis-hash/homeassistant-apps/main/iqfanda-agent/rootfs/app/"
UPDATE_TIMEOUT = 1500
POLL_SECONDS = 5
ACTIVE = ("pending", "claimed", "running")


def now():
    return datetime.now(timezone.utc)


def out(tag, value):
    print(tag + "=" + json.dumps(value, ensure_ascii=True, sort_keys=True, default=str), flush=True)


def obj(value):
    return value if isinstance(value, dict) else {}


def aware(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def fresh(value, seconds=180):
    return value is not None and -30 <= (now() - aware(value)).total_seconds() <= seconds


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def release_preflight():
    with urlopen(RAW_BASE + "VERSION", timeout=20) as response:
        published = response.read(100).decode().strip()
    with urlopen(RAW_BASE + "protection/manual_battery_manifest.json", timeout=20) as response:
        manifest = json.loads(response.read(200000))
    require(published == VERSION, "PUBLISHED_VERSION_CHANGED: refresh the deployment script")
    require(manifest.get("agent_version") == VERSION and manifest.get("runtime_fingerprint") == EXPECTED_RUNTIME_FINGERPRINT,
            "PUBLISHED_BATTERY_FINGERPRINT_CHANGED: no update was requested")
    out("RELEASE", {"version": VERSION, "runtime_fingerprint": EXPECTED_RUNTIME_FINGERPRINT,
        "public_source_verified": True, "ha_latest_observed": False,
        "actual_installed_version_required_before_canary": VERSION,
        "automatic_retry_after_intermediate_version": False})


def selected(db):
    device = db.scalar(select(Device).where(Device.serial_number == SERIAL))
    require(device is not None and device.site_id is not None, "TARGET_DEVICE_OR_SITE_MISSING")
    require(device.serial_number not in PROTECTED, "PROTECTED_TARGET_REJECTED")
    return device


def live_commands(db, device_id):
    # An expired transport lease does not prove that a claimed/running
    # operation stopped on the device. Never duplicate an uncertain owner.
    return db.scalars(select(DeviceCommand).where(DeviceCommand.device_id == device_id,
        (DeviceCommand.status.in_(("claimed", "running"))) |
        ((DeviceCommand.status == "pending") &
         ((DeviceCommand.expires_at.is_(None)) | (DeviceCommand.expires_at > now())))
        ).order_by(DeviceCommand.created_at)).all()


def canary_payload():
    return {"commissioning": True, "profile_id": "foxess_h3_10_0_e",
            "power_w": 500, "expected_agent_version": VERSION,
            "expected_runtime_fingerprint": EXPECTED_RUNTIME_FINGERPRINT}


def own_inflight_operation():
    with SessionLocal() as db:
        device = selected(db)
        operations = [command for command in live_commands(db, device.id)
                      if command.command_type in {"agent_update", "manual_battery_canary_v1"}]
        if not operations:
            return None
        require(len(operations) == 1, "MULTIPLE_UPDATE_OR_CANARY_OPERATIONS_REQUIRE_REVIEW")
        command = operations[0]
        expected = {"target_version": VERSION, "backup": True} if command.command_type == "agent_update" else canary_payload()
        require(obj(command.payload) == {**expected, "source": SOURCE},
                "ANOTHER_UPDATE_OR_CANARY_IS_INFLIGHT")
        out("RESUME_COMMAND", {"id": str(command.id), "type": command.command_type})
        return command.command_type, command.id


def protected_snapshot():
    snapshots = {}
    with SessionLocal() as db:
        for serial in PROTECTED:
            device = db.scalar(select(Device).where(Device.serial_number == serial))
            if device is None:
                snapshots[serial] = {"present": False}
                continue
            config = db.scalar(select(DeviceConfig).where(DeviceConfig.device_id == device.id))
            modules = db.scalars(select(InstallationModule).where(InstallationModule.site_id == device.site_id).order_by(InstallationModule.module_key)).all()
            value = {"config": {key: getattr(config, key, None) for key in
                ("remote_commands_enabled", "cloud_enabled", "settings", "active_modules", "config_version")},
                "modules": [{"module": m.module_key, "status": m.status, "configuration": m.configuration} for m in modules]}
            snapshots[serial] = {"present": True, "configuration_sha256": hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()}
    return snapshots


def preflight(*, require_version=False):
    with SessionLocal() as db:
        device = selected(db)
        config = db.scalar(select(DeviceConfig).where(DeviceConfig.device_id == device.id))
        status = db.scalar(select(DeviceStatus).where(DeviceStatus.device_id == device.id))
        require(config is not None and config.remote_commands_enabled is True and config.cloud_enabled is True,
                "CLOUD_OR_REMOTE_COMMANDS_NOT_ENABLED")
        require(fresh(device.last_seen_at), "TARGET_HEARTBEAT_STALE")
        if require_version:
            require(device.software_version == VERSION, "TARGET_AGENT_VERSION_NOT_CONFIRMED")
        photovoltaic = db.scalar(select(InstallationModule).where(
            InstallationModule.site_id == device.site_id, InstallationModule.module_key == "photovoltaic"))
        require(photovoltaic is not None, "PHOTOVOLTAIC_MODULE_MISSING")
        cfg = obj(photovoltaic.configuration)
        manufacturer = str(photovoltaic.manufacturer or cfg.get("manufacturer") or "").strip().lower()
        model = str(photovoltaic.model or cfg.get("model") or "").strip()
        require(manufacturer == "foxess" and model == "H3-10.0-E", "TARGET_IS_NOT_EXPECTED_FOXESS_H3")
        runtime = db.scalar(select(SpotResourceRuntimeState).where(
            SpotResourceRuntimeState.site_id == device.site_id,
            SpotResourceRuntimeState.resource_key == "battery:manual_immediate_charge"))
        plan = obj(runtime.active_plan) if runtime else {}
        require(not (runtime and runtime.demand_state == "requested" and plan.get("active") is True),
                "ACTIVE_USER_MANUAL_SESSION: let the user finish or stop it first")
        spot = db.scalar(select(InstallationModule).where(
            InstallationModule.site_id == device.site_id, InstallationModule.module_key == "spot_prices"))
        require(spot is not None and obj(spot.configuration).get("control_enabled") is not True,
                "AUTOMATIC_SPOT_CONTROL_MUST_BE_OFF_FOR_COMMISSIONING")
        for command in live_commands(db, device.id):
            payload = obj(command.payload)
            if command.command_type == "spot_battery_intent" and payload.get("action") != "auto":
                raise RuntimeError("ANOTHER_BATTERY_POWER_COMMAND_IS_INFLIGHT")
            if command.command_type in {"manual_battery_canary_v1", "agent_update"}:
                raise RuntimeError("ANOTHER_UPDATE_OR_CANARY_IS_INFLIGHT")
        local = obj(obj(status.communication_state).get("local_battery_runtime")) if status else {}
        require(local.get("state") not in {"active", "preparing", "stop_pending", "storage_fault"},
                "LOCAL_BATTERY_SESSION_OR_UNVERIFIED_STOP_ACTIVE")
        capacity = float(cfg.get("battery_capacity_kwh") or 0)
        require(math.isfinite(capacity) and capacity > 0, "BATTERY_CAPACITY_INVALID")
        soc_row = db.scalar(select(ModuleEntityState).where(
            ModuleEntityState.installation_module_id == photovoltaic.id,
            ModuleEntityState.entity_key == "baterie.soc"))
        require(soc_row is not None and soc_row.quality == "good" and fresh(soc_row.updated_at), "SOC_TELEMETRY_STALE")
        soc = float(soc_row.value)
        require(math.isfinite(soc) and 0 <= soc <= 100, "SOC_TELEMETRY_INVALID")
        result = {"serial": SERIAL, "device_id": str(device.id), "agent_version": device.software_version,
            "manufacturer": manufacturer, "model": model, "soc_percent": soc,
            "battery_capacity_kwh": capacity, "local_runtime": local}
        out("PREFLIGHT", result)
        return result


def queue(command_type, payload, seconds):
    with SessionLocal() as db:
        device = selected(db)
        require(device.serial_number == SERIAL, "TARGET_SCOPE_CHANGED")
        payload = {**payload, "source": SOURCE}
        existing = [command for command in live_commands(db, device.id) if command.command_type == command_type]
        for command in existing:
            if obj(command.payload).get("source") == SOURCE:
                previous = obj(command.payload)
                if command_type == "spot_battery_intent" and payload.get("action") == "auto":
                    volatile = {"valid_until", "current_soc_percent", "battery_capacity_kwh"}
                    comparable = lambda value: {k: v for k, v in value.items() if k not in volatile}
                    require(comparable(previous) == comparable(payload), "ANOTHER_F53_OPERATION_IS_INFLIGHT")
                else:
                    require(previous == payload, "ANOTHER_F53_OPERATION_IS_INFLIGHT")
                out("RESUME_COMMAND", {"id": str(command.id), "type": command_type})
                return command.id
        stamp = now()
        command = DeviceCommand(id=uuid.uuid4(), device_id=device.id, created_by_user_id=None,
            command_type=command_type, status="pending", payload=payload,
            available_at=stamp, expires_at=stamp + timedelta(seconds=seconds))
        db.add(command)
        db.commit()
        out("QUEUED", {"serial": SERIAL, "id": str(command.id), "type": command_type})
        return command.id


def wait_command(command_id, *, timeout):
    deadline = time.monotonic() + timeout
    reported = None
    last_log = 0.0
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            command = db.get(DeviceCommand, command_id)
            require(command is not None, "QUEUED_COMMAND_DISAPPEARED")
            device = selected(db)
            require(command.device_id == device.id, "COMMAND_SCOPE_MISMATCH")
            state = (command.status, obj(command.result).get("phase"), device.software_version)
            if state != reported or time.monotonic() - last_log > 25:
                out("WAIT", {"command_id": str(command_id), "status": state[0], "phase": state[1], "agent_version": state[2]})
                reported, last_log = state, time.monotonic()
            if command.status in {"succeeded", "failed", "cancelled", "expired"}:
                result = obj(command.result)
                out("COMMAND_RESULT", {"id": str(command_id), "status": command.status,
                    "result": result, "error": str(command.error_message or "")[:1500]})
                require(command.status == "succeeded", "COMMAND_FAILED: " + str(command.error_message or command.status)[:500])
                return result
        time.sleep(POLL_SECONDS)
    raise RuntimeError("COMMAND_WAIT_TIMEOUT: request state was preserved; do not force a second update")


def wait_version():
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            device = selected(db)
            if device.software_version == VERSION and fresh(device.last_seen_at):
                return
        time.sleep(POLL_SECONDS)
    raise RuntimeError("NEW_AGENT_HEARTBEAT_NOT_CONFIRMED: actual=" + str(device.software_version)
        + "; intermediate or stale HA latest is not accepted; no automatic second update or physical test")


def auto_payload(state):
    return {"action": "auto", "reason": "f53_r13_commissioning_stop", "manual_immediate_charge": True,
        "control_source": "manual_immediate_charge", "requested_charge_power_w": 0, "allowed_charge_power_w": 0,
        "requested_discharge_power_w": 0, "allowed_discharge_power_w": 0,
        "minimum_discharge_soc_percent": 20, "target_soc_percent": 95,
        "current_soc_percent": state["soc_percent"], "battery_capacity_kwh": state["battery_capacity_kwh"],
        "current_interval_selected": False, "valid_until": (now() + timedelta(seconds=180)).isoformat()}


def report_canary_success(result, canary_id):
    protection = obj(result.get("protection"))
    require(result.get("status") == "passed" and result.get("verified") is True
            and result.get("local_final_auto_verified") is True
            and result.get("profile_id") == "foxess_h3_10_0_e"
            and result.get("verification_scope") == "bidirectional_500w_and_stop"
            and protection.get("status") == "locked"
            and protection.get("runtime_fingerprint") == EXPECTED_RUNTIME_FINGERPRINT,
            "PHYSICAL_TEST_AND_PROTECTION_NOT_CONFIRMED")
    out("RESULT", {"status": "PASS_FOXESS_00018_LOCAL_AND_LOCKED", "agent_version": VERSION,
        "serial": SERIAL, "physical_test_command_id": str(canary_id),
        "verification_scope": result.get("verification_scope"), "protection": protection,
        "other_brands_physically_tested": False})


def main():
    out("SCOPE", {"revision": SOURCE, "target": SERIAL, "version": VERSION,
        "physical_canary_w": 500, "new_backend_image": False, "frontend_change": False,
        "module_configuration_change": False, "commands_for_other_devices": False})
    before = None
    lock_db = None
    lock_acquired = False
    try:
        release_preflight()
        before = protected_snapshot()
        lock_db = SessionLocal()
        lock_acquired = lock_db.execute(text("SELECT pg_try_advisory_lock(53016818)")).scalar() is True
        require(lock_acquired, "ANOTHER_F53_INSTALLER_IS_RUNNING")
        resumed = own_inflight_operation()
        if resumed:
            operation_type, operation_id = resumed
            if operation_type == "manual_battery_canary_v1":
                result = wait_command(operation_id, timeout=420)
                wait_version()
                report_canary_success(result, operation_id)
                return 0
            wait_command(operation_id, timeout=UPDATE_TIMEOUT)
            wait_version()
        state = preflight()
        if state["agent_version"] != VERSION:
            command_id = queue("agent_update", {"target_version": VERSION, "backup": True}, UPDATE_TIMEOUT + 120)
            wait_command(command_id, timeout=UPDATE_TIMEOUT)
            wait_version()
        state = preflight(require_version=True)
        out("ADDON_INSTALLED", {"serial": SERIAL, "version": VERSION})
        probe_id = queue("foxess_remote_control_probe", {"read_only": True}, 180)
        probe = wait_command(probe_id, timeout=180)
        out("FOXESS_PHYSICAL_BASELINE", probe)
        # Only a confirmed AUTO permits the bounded two-direction test.
        auto_id = queue("spot_battery_intent", auto_payload(state), 180)
        auto_result = wait_command(auto_id, timeout=180)
        require(auto_result.get("verified") is True and auto_result.get("action") == "auto", "BASELINE_AUTO_UNVERIFIED")
        preflight(require_version=True)
        canary_id = queue("manual_battery_canary_v1", canary_payload(), 420)
        result = wait_command(canary_id, timeout=420)
        report_canary_success(result, canary_id)
        return 0
    except Exception as exc:
        out("RESULT", {"status": "NOT_PHYSICALLY_CONFIRMED", "error_type": type(exc).__name__,
            "error": str(exc)[:1600], "serial": SERIAL, "instruction": "Review the report; do not remove owner markers or bypass readback."})
        return 2
    finally:
        try:
            if before is not None:
                after = protected_snapshot()
                out("PROTECTED_CONFIGURATION", {"unchanged": before == after,
                    "before": before, "after": after, "commands_created_by_this_script": 0})
        except Exception as exc:
            out("PROTECTED_CONFIGURATION", {"check_failed": type(exc).__name__, "commands_created_by_this_script": 0})
        finally:
            if lock_db is not None:
                try:
                    if lock_acquired:
                        require(lock_db.execute(text("SELECT pg_advisory_unlock(53016818)")).scalar() is True,
                                "INSTALLER_LOCK_RELEASE_UNCONFIRMED")
                except Exception as exc:
                    out("LOCK_RELEASE", {"error": str(exc)[:300]})
                    # A session advisory lock survives returning a connection to
                    # the pool. Invalidate it when a clean unlock is uncertain.
                    lock_db.invalidate()
                finally:
                    lock_db.close()


if __name__ == "__main__":
    raise SystemExit(main())
