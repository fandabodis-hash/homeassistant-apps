"""R16 uses the real FoxESS driver/runtime over a simulated locked serial bus."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


APP = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(APP))
import foxess_zero_remote_release as release
import manual_battery_runtime as runtime


def _no_hardware(*args, **kwargs):
    raise AssertionError("A test must never open a real serial port")


def _load_serial_modules():
    pymodbus, client, goodwe = ModuleType("pymodbus"), ModuleType("pymodbus.client"), ModuleType("communication.goodwe_probe")
    client.ModbusSerialClient = goodwe._find_communicator = _no_hardware
    pymodbus.client = client
    loaded = []
    with patch.dict(sys.modules, {"pymodbus": pymodbus, "pymodbus.client": client, "communication.goodwe_probe": goodwe}):
        for name in ("foxess_ems_controller", "foxess_remote_control_probe"):
            spec = importlib.util.spec_from_file_location("_release_test_" + name, APP / "communication" / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            loaded.append(module)
    return loaded


driver, probe_module = _load_serial_modules()
IDENTITY = {"device_uuid": "8401b954-abda-4b31-b843-84be75800aae"}
FINGERPRINT = "1" * 64


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 1, 16, tzinfo=timezone.utc)
    def __call__(self):
        return self.value
    def sleep(self, seconds):
        self.value += timedelta(seconds=seconds)


class BusLock:
    def __init__(self):
        self.held, self.entries = False, 0
    def __enter__(self):
        if self.held:
            raise AssertionError("Normal STOP attempted while the service still holds the bus")
        if not runtime._LOCK._is_owned():
            raise AssertionError("Hardware operation escaped the in-process runtime lock")
        self.held = True
        self.entries += 1
    def __exit__(self, *exc):
        self.held = False


class Response:
    def __init__(self, registers=None, error=False):
        self.registers, self.error = registers, error
    def isError(self):
        return self.error


class SerialClient:
    def __init__(self, rig, **kwargs):
        self.rig, self.closed = rig, False
        rig.clients.append(self)
        rig.assertEqual(kwargs, {"port": "/dev/serial/by-id/test-inverter", "baudrate": 9600,
            "bytesize": 8, "parity": "N", "stopbits": 1, "timeout": kwargs["timeout"], "retries": 0})
    def connect(self):
        self.rig.assertTrue(self.rig.bus.held)
        return True
    def close(self):
        self.rig.assertTrue(self.rig.bus.held)
        self.closed = True
    def read_holding_registers(self, *, address, count, device_id):
        self.rig.assertTrue(self.rig.bus.held)
        self.rig.assertTrue(runtime._LOCK._is_owned())
        self.rig.assertEqual((count, device_id), (1, 247))
        self.rig.reads.append(address)
        if self.rig.read_hook:
            self.rig.read_hook(address)
        return Response([self.rig.registers[address]])
    def write_register(self, *, address, value, device_id):
        self.rig.assertTrue(self.rig.bus.held)
        self.rig.assertTrue(runtime._LOCK._is_owned())
        self.rig.assertEqual((address, value, device_id), (44000, 0, 247))
        record = json.loads(release.AUDIT_PATH.read_text())
        self.rig.assertEqual(record["status"], "write_intent")
        self.rig.assertTrue(record["write_intent_recorded"])
        self.rig.assertEqual(release.AUDIT_PATH.stat().st_mode & 0o777, 0o600)
        self.rig.writes.append((address, value, device_id))
        if not self.rig.ignore_write:
            self.rig.registers[address] = value
        if self.rig.write_hook:
            self.rig.write_hook()
        if self.rig.crash_after_write:
            raise SystemExit("simulated process termination after the hardware request")
        if self.rig.lose_ack:
            raise TimeoutError("simulated lost ACK")
        return Response()
    def write_registers(self, **kwargs):
        raise AssertionError("No multi-register write is authorized")


class ZeroRemoteReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root, self.clock, self.bus = Path(self.tmp.name), Clock(), BusLock()
        self.registers = {30028: 115, 30016: 223, 30017: 103, 30018: 403, 41000: 0,
                          41010: 100, 44000: 1, 44001: 500, 44002: 0, 31036: 65213, 31038: 47}
        self.reads, self.writes, self.clients, self.results = [], [], [], []
        self.ignore_write = self.lose_ack = self.crash_after_write = False
        self.read_hook = self.write_hook = None
        self.config = {"module_runtime_configurations": [{"module_key": "photovoltaic", "manufacturer": "foxess",
            "model": "H3-10.0-E", "communication_type": "rs485", "communicator_id": "test-inverter", "modbus_device_id": 247}]}
        self.local = runtime.LocalBatteryRuntime(path=self.root / "runtime.json", clock=self.clock,
            execute=driver.execute_foxess_h3_from_cloud_config, config_loader=lambda: self.config,
            feature_guard=lambda: FINGERPRINT)
        self.original_state = {"state": "stop_pending", "action": "charge_grid", "manual": True,
            "command_id": release.EXPECTED_SESSION, "session_id": release.EXPECTED_SESSION,
            "started_at": (self.clock() - timedelta(hours=1)).isoformat(),
            "expires_at": (self.clock() - timedelta(minutes=50)).isoformat(),
            "reason": "process_restart", "verified": False, "profile_id": release.PROFILE_ID,
            "feature_fingerprint": "0" * 64, "signature": "prior-manual-signature",
            "blocked_signature": "prior-manual-signature", "blocked_manual_signatures": ["prior-manual-signature"],
            "next_stop_retry_at": (self.clock() + timedelta(minutes=5)).isoformat(),
            "control_config": copy.deepcopy(self.config), "intent": {"action": "charge_grid",
                "allowed_charge_power_w": 1000, "allowed_discharge_power_w": 0,
                "current_soc_percent": 44, "target_soc_percent": 90,
                "minimum_discharge_soc_percent": 20, "battery_capacity_kwh": 12}}
        self.local._save(copy.deepcopy(self.original_state))
        self.services = SimpleNamespace(serial=lambda: release.SERIAL, version=lambda: "0.1.169",
            config=lambda: self.config, guard=lambda: FINGERPRINT, driver=driver, runtime=runtime,
            probe=probe_module.probe_foxess_remote_control_from_cloud_config)
        patches = [patch.object(release, "_services", return_value=self.services), patch.object(release, "_now", self.clock),
            patch.object(release, "AUDIT_PATH", self.root / "audit.json"), patch.object(runtime, "_RUNTIME", self.local),
            patch.object(release.time, "sleep", self.clock.sleep)]
        for module in (driver, probe_module):
            patches += [patch.object(module, "OWNER_MARKER_PATH", self.root / "owner.json"),
                patch.object(module, "_find_communicator", return_value=({}, "/dev/serial/by-id/test-inverter")),
                patch.object(module, "ziskej_zamek_modbus_sbernice", return_value=self.bus),
                patch.object(module, "ModbusSerialClient", side_effect=lambda **kwargs: SerialClient(self, **kwargs))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def payload(self, **changes):
        return {"serial_number": release.SERIAL, "device_uuid": IDENTITY["device_uuid"],
            "source": release.SOURCE, "confirmation": release.CONFIRMATION, "recovery_id": release.RECOVERY_ID,
            "expected_agent_version": "0.1.169", "expected_runtime_fingerprint": FINGERPRINT,
            "issued_at": self.clock().isoformat(), "expires_at": (self.clock() + timedelta(seconds=180)).isoformat(), **changes}

    def execute(self, payload=None):
        release.execute_foxess_zero_remote_release(identity=IDENTITY, command_id=str(uuid.uuid4()),
            command_payload=self.payload() if payload is None else payload, submit_result=lambda **value: self.results.append(value))
        return self.results[-1]

    def assert_rejected_without_write(self, payload=None):
        value = self.execute(payload)
        self.assertEqual(value["status"], "failed")
        self.assertFalse(value["result"]["verified"])
        self.assertEqual(self.writes, [])
        return value

    def test_one_write_then_real_normal_stop_clears_latch_and_probe_verifies(self):
        value = self.execute()
        self.assertEqual(value["status"], "succeeded", value)
        result = value["result"]
        self.assertEqual(self.writes, [(44000, 0, 247)])
        self.assertEqual(result["hardware_write_calls_this_command"], 1)
        self.assertTrue(result["write_acknowledged"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["normal_stop"], {"state": "stopped", "action": "auto", "verified": True,
                                                "applied_power_w": 0, "write_performed": False})
        self.assertEqual(result["final_probe"]["registers"]["battery_power_w_31036"], -323)
        self.assertEqual(result["final_probe"]["owner_marker"]["status"], "missing")
        self.assertEqual(self.local._load()["blocked_manual_signatures"], [])
        self.assertIsNone(self.local._load()["blocked_signature"])
        self.assertFalse(driver.OWNER_MARKER_PATH.exists())
        self.assertEqual(json.loads(release.AUDIT_PATH.read_text())["status"], "completed")
        self.assertTrue(all(client.closed for client in self.clients))

    def test_noop_on_zero_performs_no_hardware_write_and_does_not_infer_zero_battery_power(self):
        self.registers[44000] = 0
        state = copy.deepcopy(self.original_state)
        state.update(state="stopped", action="auto", verified=True, applied_power_w=0)
        self.local._save(state)
        value = self.execute()
        self.assertEqual(value["status"], "succeeded", value)
        self.assertEqual(value["result"]["phase"], "already_zero_verified")
        self.assertEqual(self.writes, [])

    def test_manufacturing_serial_payload_and_uuid_scope_are_strict(self):
        for change in ({"serial_number": "F800711-TNG-00018"}, {"device_uuid": str(uuid.uuid4())},
                       {"confirmation": "AUTO"}, {"source": "other"}, {"recovery_id": "next-recovery"},
                       {"address": 44000}, {"expected_agent_version": "0.1.168"}):
            with self.subTest(change=change):
                self.assert_rejected_without_write(self.payload(**change))
        self.services.serial = lambda: "F800711-TNG-00018"
        self.assert_rejected_without_write()

    def test_expired_future_and_overlong_authorizations_are_rejected(self):
        for issued, expiry in ((-200, -1), (1, 100), (0, 181), (0, 0)):
            with self.subTest(issued=issued, expiry=expiry):
                self.assert_rejected_without_write(self.payload(
                    issued_at=(self.clock() + timedelta(seconds=issued)).isoformat(),
                    expires_at=(self.clock() + timedelta(seconds=expiry)).isoformat()))

    def test_installed_version_and_exact_fingerprint_are_required(self):
        self.services.version = lambda: "0.1.168"
        self.assert_rejected_without_write()
        self.services.version = lambda: "0.1.169"
        self.services.guard = lambda: "2" * 64
        self.assert_rejected_without_write()

    def test_only_exact_expired_pending_session_can_authorize_first_write(self):
        for change in ({"state": "active"}, {"state": "preparing"}, {"session_id": "another-session"},
                       {"expires_at": (self.clock() + timedelta(seconds=30)).isoformat()}, {"state": "stopped"}):
            with self.subTest(change=change):
                self.local._save({**copy.deepcopy(self.original_state), **change})
                self.assert_rejected_without_write()
        self.local.path.unlink()
        self.assert_rejected_without_write()

    def test_persisted_route_mismatch_rejects_before_opening_serial_port(self):
        self.config["module_runtime_configurations"][0]["communicator_id"] = "other-port"
        self.assert_rejected_without_write()
        self.assertEqual(self.clients, [])

    def test_every_fixed_hardware_precondition_is_checked_without_writes(self):
        for address in (30028, 30016, 30017, 30018, 41000, 41010, 44001, 44002):
            with self.subTest(address=address):
                original = self.registers[address]
                self.registers[address] = original + 1
                self.assert_rejected_without_write()
                self.registers[address] = original
        self.assertFalse(release.AUDIT_PATH.exists())

    def test_existing_owner_and_dangling_owner_symlink_are_rejected(self):
        driver.OWNER_MARKER_PATH.write_text("{}")
        self.assert_rejected_without_write()
        driver.OWNER_MARKER_PATH.unlink()
        driver.OWNER_MARKER_PATH.symlink_to(self.root / "absent-owner")
        self.assert_rejected_without_write()

    def test_two_different_preflight_snapshots_do_not_authorize_write(self):
        calls = [0]
        def change(address):
            if address == 44000:
                calls[0] += 1
                if calls[0] == 2:
                    self.registers[address] = 0
        self.read_hook = change
        self.assert_rejected_without_write()
        self.assertFalse(release.AUDIT_PATH.exists())

    def test_intent_fsync_failure_prevents_hardware_request(self):
        with patch.object(release.os, "fsync", side_effect=OSError("simulated fsync failure")):
            self.assert_rejected_without_write()

    def test_expiry_is_rechecked_after_reads_and_durable_write_intent(self):
        persist = release._persist
        def delayed(record):
            persist(record)
            self.clock.sleep(181)
        with patch.object(release, "_persist", side_effect=delayed):
            self.assert_rejected_without_write()
        self.assertEqual(json.loads(release.AUDIT_PATH.read_text())["status"], "write_intent")
        self.assert_rejected_without_write()  # A fresh request cannot reuse the consumed recovery ID.

    def test_lost_ack_can_succeed_only_from_verified_physical_zero_without_retry(self):
        self.lose_ack = True
        value = self.execute()
        self.assertEqual(value["status"], "succeeded", value)
        self.assertEqual(self.writes, [(44000, 0, 247)])
        self.assertFalse(value["result"]["write_acknowledged"])
        self.assertTrue(value["result"]["remote_enable_changed_observed"])

    def test_rejected_write_keeps_attempt_and_never_retries_remote_one(self):
        self.ignore_write = True
        first = self.execute()
        self.assertEqual(first["status"], "failed")
        self.assertEqual(self.writes, [(44000, 0, 247)])
        self.assertEqual(json.loads(release.AUDIT_PATH.read_text())["status"], "write_intent")
        second = self.execute()
        self.assertEqual(second["status"], "failed")
        self.assertEqual(self.writes, [(44000, 0, 247)])
        self.assertEqual(self.local.status()["state"], "stop_pending")

    def test_crash_after_write_can_resume_only_as_zero_verification(self):
        self.crash_after_write = True
        with self.assertRaises(SystemExit):
            self.execute()
        self.assertEqual(json.loads(release.AUDIT_PATH.read_text())["status"], "write_intent")
        self.assertEqual(self.registers[44000], 0)
        self.crash_after_write = False
        value = self.execute()
        self.assertEqual(value["status"], "succeeded", value)
        self.assertEqual(value["result"]["hardware_write_calls_this_command"], 0)
        self.assertEqual(self.writes, [(44000, 0, 247)])

    def test_completed_audit_never_authorizes_a_second_remote_one_release(self):
        self.assertEqual(self.execute()["status"], "succeeded")
        self.registers[44000] = 1
        self.assertEqual(self.execute()["status"], "failed")
        self.assertEqual(self.writes, [(44000, 0, 247)])

    def test_unexpected_register_change_after_write_is_not_silently_restored(self):
        self.write_hook = lambda: self.registers.update({44001: 600})
        value = self.execute()
        self.assertEqual(value["status"], "failed")
        self.assertEqual(self.writes, [(44000, 0, 247)])
        self.assertEqual(self.registers[44001], 600)
        self.assertEqual(json.loads(release.AUDIT_PATH.read_text())["status"], "write_intent")

    def test_new_owner_marker_after_release_blocks_normal_auto(self):
        stable = release._stable
        def appears(*args, **kwargs):
            value = stable(*args, **kwargs)
            if kwargs["remote"] == {0}:
                driver.OWNER_MARKER_PATH.write_text("{}")
            return value
        with patch.object(release, "_stable", side_effect=appears):
            value = self.execute()
        self.assertEqual(value["status"], "failed")
        self.assertEqual(self.writes, [(44000, 0, 247)])
        self.assertEqual(self.local.status()["state"], "stop_pending")

    def test_cache_route_change_after_normal_stop_does_not_mark_completed(self):
        submit = self.local.submit
        def changed(**kwargs):
            outcome = submit(**kwargs)
            self.config = copy.deepcopy(self.config)
            self.config["module_runtime_configurations"][0]["communicator_id"] = "changed-route"
            return outcome
        with patch.object(self.local, "submit", side_effect=changed):
            value = self.execute()
        self.assertEqual(value["status"], "failed")
        self.assertEqual(self.writes, [(44000, 0, 247)])
        self.assertEqual(json.loads(release.AUDIT_PATH.read_text())["status"], "hardware_verified")

    def test_invalid_or_symlink_audit_fails_closed(self):
        release.AUDIT_PATH.write_text("{}")
        self.assert_rejected_without_write()
        release.AUDIT_PATH.unlink()
        release.AUDIT_PATH.symlink_to(self.root / "absent-audit")
        self.assert_rejected_without_write()


if __name__ == "__main__":
    unittest.main()
