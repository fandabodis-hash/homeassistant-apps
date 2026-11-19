"""F99 00018: offline gates for one-shot FoxESS zero-remote rescue.

No physical I/O, network, cloud, or database is ever used by these tests.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from foxess_00018_owner_recovery import (
    AUDIT_PATH, CONFIRMATION, EXPECTED_FIRMWARE, FIXED, PROFILE,
    RECOVERY_ID, SERIAL, SOURCE, VERSION, RecoveryRejected,
    _audit, check_request, check_snapshot
)


class OwnerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.device_uuid = str(uuid.uuid4())
        self.id = {"device_uuid": self.device_uuid}
        self.command_id = str(uuid.uuid4())
        from datetime import timedelta
        from foxess_00018_owner_recovery import now
        self.payload = {
            "serial_number": SERIAL, "device_uuid": self.device_uuid,
            "source": SOURCE, "confirmation": CONFIRMATION,
            "recovery_id": RECOVERY_ID, "expected_agent_version": VERSION,
            "expected_runtime_fingerprint": "a"*64,
            "issued_at": (now()-timedelta(seconds=5)).isoformat(),
            "expires_at": (now()+timedelta(seconds=95)).isoformat(),
        }

    def checked(self, **changes):
        return check_request(identity=self.id, serial=SERIAL,
                             command_id=self.command_id,
                             payload={**self.payload, **changes})

    def test_expected_scoped_action(self):
        self.assertEqual(self.checked()["serial_number"], SERIAL)
        self.assertEqual(self.checked()["recovery_id"], RECOVERY_ID)
        self.assertEqual(VERSION, "0.1.176")
        self.assertEqual(PROFILE, "foxess_h3_10_0_e")

    def test_no_other_device_or_scope(self):
        with self.assertRaises(RecoveryRejected):
            check_request(identity=self.id, serial="F800711-TNG-00007",
                          command_id=self.command_id, payload=self.payload)
        for changes in ({"serial_number": "F800711-TNG-00007"},
                        {"device_uuid": str(uuid.uuid4())},
                        {"source": "other"},
                        {"expected_agent_version": "0.1.175"},
                        {"expected_runtime_fingerprint": "invalid"},
                        {"confirmation": "NO_CONFIRMATION"},
                        {"extra_field": "unapproved"}):
            with self.subTest(changes=changes), self.assertRaises(RecoveryRejected):
                self.checked(**changes)

    def test_bounded_fresh_authorization(self):
        from datetime import timedelta
        from foxess_00018_owner_recovery import now
        with self.assertRaises(RecoveryRejected):
            self.checked(issued_at=(now()-timedelta(minutes=5)).isoformat(),
                         expires_at=(now()+timedelta(minutes=5)).isoformat())
        with self.assertRaises(RecoveryRejected):
            self.checked(issued_at=(now()-timedelta(minutes=5)).isoformat(),
                         expires_at=(now()-timedelta(seconds=1)).isoformat())

    def snapshot(self, **regs):
        return {"protocol_version": 115,
                "firmware": dict(EXPECTED_FIRMWARE),
                "registers": {**FIXED, "remote_enable_44000": 1, **regs}}

    def test_only_zero_active_power(self):
        check_snapshot(self.snapshot(), remote={0, 1})
        check_snapshot(self.snapshot(remote_enable_44000=0), remote={0})
        with self.assertRaises(RecoveryRejected):
            check_snapshot(self.snapshot(remote_active_power_44002=500), remote={0, 1})
        with self.assertRaises(RecoveryRejected):
            check_snapshot(self.snapshot(remote_active_power_44002=65036), remote={0, 1})

    def test_preserve_work_mode_and_timeout(self):
        for key, value in (("work_mode_41000", 2),
                           ("remote_timeout_44001", 180),
                           ("max_soc_41010", 90),
                           ("remote_enable_44000", 2)):
            with self.subTest(key=key), self.assertRaises(RecoveryRejected):
                check_snapshot(self.snapshot(**{key: value}), remote={0, 1})

    def test_firmware_not_guessable(self):
        wrong = self.snapshot()
        wrong["firmware"]["30018"] = 389
        with self.assertRaises(RecoveryRejected):
            check_snapshot(wrong, remote={0, 1})
        wrong = self.snapshot()
        wrong["protocol_version"] = 114
        with self.assertRaises(RecoveryRejected):
            check_snapshot(wrong, remote={0, 1})

    def test_audit_is_durable_one_use(self):
        from foxess_00018_owner_recovery import AUDIT_PATH as saved
        with tempfile.TemporaryDirectory() as tmp:
            import foxess_00018_owner_recovery as mod
            with patch.object(mod, "AUDIT_PATH", Path(tmp) / "attempt.json"):
                _audit({"schema_version": 1, "state": "write_intent"})
                audit_path = mod.AUDIT_PATH
                self.assertEqual(json.loads(audit_path.read_text())["state"], "write_intent")
                self.assertEqual(audit_path.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(RecoveryRejected):
                    _audit({"schema_version": 1, "state": "write_intent"})
                self.assertEqual(json.loads(audit_path.read_text())["state"], "write_intent")


    def test_scoped_one_write_then_normal_auto_no_repeat(self):
        """Exercise the real recovery coordinator with an in-memory Modbus fake."""
        from contextlib import nullcontext
        from datetime import timedelta
        from threading import RLock
        from types import SimpleNamespace
        import foxess_00018_owner_recovery as mod

        class Reply:
            def isError(self):
                return False

        class Port:
            def __init__(self):
                self.registers = {
                    30028: 115, 30016: 223, 30017: 103, 30018: 407,
                    41000: 0, 41010: 100, 44000: 1,
                    44001: 20804, 44002: 0,
                }
                self.writes = []
            def connect(self):
                return True
            def close(self):
                pass
            def write_register(self, *, address, value, device_id):
                self.writes.append((address, value, device_id))
                self.registers[address] = value
                return Reply()

        cfg = {"module_runtime_configurations": [{
            "module_key": "photovoltaic", "manufacturer": "foxess",
            "model": "H3-10.0-E", "communicator_id": "comm",
            "modbus_device_id": 247,
        }]}
        local = {
            "state": "stop_pending", "manual": True, "profile_id": PROFILE,
            "control_config": cfg,
            "expires_at": (mod.now()-timedelta(days=1)).isoformat(),
            "intent": {
                "current_soc_percent": 98.0, "target_soc_percent": 10.0,
                "minimum_discharge_soc_percent": 10.0,
                "battery_capacity_kwh": 20.0,
            },
        }
        port = Port()
        calls = []
        rt_obj = SimpleNamespace(
            _load=lambda: local,
            submit=lambda **kw: (
                calls.append(kw) or {
                    "local_runtime": {
                        "state": "stopped", "action": "auto",
                        "verified": True, "applied_power_w": 0,
                    },
                    "applied": {
                        "action": "auto", "verified": True,
                        "write_performed": False,
                    },
                }
            )
        )
        runtime = SimpleNamespace(
            _LOCK=RLock(), _RUNTIME=rt_obj, _route=lambda cfg: "exact-route")
        driver = SimpleNamespace(
            OWNER_MARKER_PATH=None,
            _select_runtime=lambda cfg: cfg["module_runtime_configurations"][0],
            _find_communicator=lambda cid: (None, "/dev/serial/by-id/usb-mock"),
            ziskej_zamek_modbus_sbernice=lambda path: nullcontext(),
            ModbusSerialClient=lambda **kw: port,
            _read_one=lambda *, client, device_id, address: client.registers[address],
            MODBUS_WRITE_SETTLE_SECONDS=0,
        )
        services = SimpleNamespace(
            runtime=runtime, driver=driver,
            serial=lambda: SERIAL, version=lambda: VERSION,
            guard=lambda: "a"*64,
            config=lambda: cfg,
            probe=lambda *, cloud_config: {
                "read_only": True, "write_performed": False,
                "owner_marker": {"present": False, "status": "missing"},
                "firmware_versions_raw": dict(EXPECTED_FIRMWARE),
                "registers": {
                    "work_mode_41000": 0, "max_soc_41010": 100,
                    "remote_enable_44000": port.registers[44000],
                    "remote_timeout_44001": 20804,
                    "remote_active_power_w_44002": 0,
                },
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            driver.OWNER_MARKER_PATH = Path(tmp) / "no-owner.json"
            with patch.object(mod, "AUDIT_PATH", Path(tmp) / "attempt.json"):
                result = {}
                mod.release(services, self.checked(), result)
                self.assertEqual(port.writes, [(44000, 0, 247)])
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["payload"]["action"], "auto")
                self.assertEqual(calls[0]["payload"]["allowed_discharge_power_w"], 0)
                self.assertEqual(json.loads(mod.AUDIT_PATH.read_text())["state"], "completed")
                self.assertTrue(result["verified"])
                self.assertEqual(port.registers[44000], 0)
                # Even when a later actor toggles enable, old approval cannot repeat.
                port.registers[44000] = 1
                with self.assertRaises(RecoveryRejected):
                    mod.release(services, self.checked(), {})
                self.assertEqual(port.writes, [(44000, 0, 247)])

    def test_changed_hardware_blocks_before_first_write(self):
        """A changed inverter or nonzero setpoint must fail before the write."""
        from contextlib import nullcontext
        from datetime import timedelta
        from threading import RLock
        from types import SimpleNamespace
        import foxess_00018_owner_recovery as mod
        with tempfile.TemporaryDirectory() as tmp:
            registers = {
                30028: 115, 30016: 223, 30017: 103, 30018: 407,
                41000: 0, 41010: 100, 44000: 1, 44001: 20804,
                44002: 500,
            }
            writes = []
            port = SimpleNamespace(
                connect=lambda: True, close=lambda: None,
                write_register=lambda **kw: writes.append(kw),
                registers=registers,
            )
            cfg={"module_runtime_configurations": [{
                "module_key":"photovoltaic", "manufacturer":"foxess",
                "model":"H3-10.0-E", "communicator_id":"comm",
                "modbus_device_id":247,
            }]}
            state={
                "state":"stop_pending", "manual":True,
                "profile_id":PROFILE, "control_config":cfg,
                "expires_at":(mod.now()-timedelta(days=1)).isoformat(),
                "intent": {
                    "current_soc_percent":98.0, "target_soc_percent":10.0,
                    "minimum_discharge_soc_percent":10.0,
                    "battery_capacity_kwh":20.0
                },
            }
            drv=SimpleNamespace(
                OWNER_MARKER_PATH=Path(tmp)/"no-marker.json",
                _select_runtime=lambda conf:conf["module_runtime_configurations"][0],
                _find_communicator=lambda _: (None,"/dev/serial/by-id/mock"),
                ziskej_zamek_modbus_sbernice=lambda _:nullcontext(),
                ModbusSerialClient=lambda **kw:port,
                _read_one=lambda *,client,device_id,address:client.registers[address],
            )
            services=SimpleNamespace(
                runtime=SimpleNamespace(
                    _LOCK=RLock(),_RUNTIME=SimpleNamespace(_load=lambda:state),
                    _route=lambda _: "same-route",
                ),driver=drv,serial=lambda:SERIAL,version=lambda:VERSION,
                guard=lambda:"a"*64,config=lambda:cfg,
            )
            with patch.object(mod,"AUDIT_PATH",Path(tmp)/"attempt.json"):
                with self.assertRaises(RecoveryRejected):
                    mod.release(services,self.checked(),{})
                self.assertEqual(writes, [])
                self.assertFalse(mod.AUDIT_PATH.exists())


if __name__ == "__main__":
    unittest.main()
