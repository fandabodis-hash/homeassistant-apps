"""FoxESS protocol-115 regressions; simulated serial bus, no hardware/network."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


APP = Path(__file__).resolve().parents[1] / "rootfs" / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _forbidden_hardware(*args, **kwargs):
    raise AssertionError("The regression suite must never open a serial port.")


def _load_driver():
    """Isolate optional I/O dependencies without replacing other test modules."""
    pymodbus = ModuleType("pymodbus")
    client = ModuleType("pymodbus.client")
    client.ModbusSerialClient = _forbidden_hardware
    pymodbus.client = client
    probe = ModuleType("communication.goodwe_probe")
    probe._find_communicator = _forbidden_hardware
    spec = importlib.util.spec_from_file_location(
        "_foxess_battery_control_under_test",
        APP / "communication" / "foxess_ems_controller.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with patch.dict(sys.modules, {
        "pymodbus": pymodbus,
        "pymodbus.client": client,
        "communication.goodwe_probe": probe,
    }):
        spec.loader.exec_module(module)
    return module


driver = _load_driver()


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds


class Response:
    def __init__(self, registers=None, error=False):
        self.registers = registers
        self.error = error

    def isError(self):
        return self.error


class SerialInverter:
    """Stateful device with delayed writes, rejected writes and missing ACKs."""
    def __init__(self, clock):
        self.clock = clock
        self.registers = {
            30028: 115, 41000: 0, 41010: 100, 44000: 0,
            44001: 3600, 44002: 0, 31038: 72,
        }
        self.pending = {}
        self.delays = {}
        self.ignored = set()
        self.lost_ack = set()
        self.failed_reads = set()
        self.writes = []
        self.reads = []
        self.last_transaction_at = None
        self.minimum_gap = 0.0
        self.after_write = None

    def _transaction(self):
        if self.last_transaction_at is not None:
            if self.clock.now - self.last_transaction_at < self.minimum_gap - 1e-9:
                raise TimeoutError("Inverter serial interface still settling")
        self.last_transaction_at = self.clock.now

    def read_holding_registers(self, *, address, count, device_id):
        self._transaction()
        self.reads.append((address, count))
        if device_id != 247:
            raise AssertionError("Unexpected slave")
        if address in self.failed_reads:
            raise TimeoutError("Simulated read timeout")
        for register, (ready, value) in list(self.pending.items()):
            if self.clock.now >= ready:
                self.registers[register] = value
                del self.pending[register]
        return Response([self.registers[address + i] for i in range(count)])

    def write_register(self, *, address, value, device_id):
        self._transaction()
        if device_id != 247:
            raise AssertionError("Unexpected slave")
        self.writes.append((address, value))
        if address not in self.ignored:
            delay = self.delays.get(address, 0)
            if delay:
                self.pending[address] = (self.clock.now + delay, value)
            else:
                self.registers[address] = value
        if self.after_write:
            self.after_write(address, value)
        if address in self.lost_ack:
            raise TimeoutError("Write applied, acknowledgement lost")
        return Response()

    def write_registers(self, **kwargs):
        raise AssertionError("Protocol 115 setpoint is a single signed word")


class FoxessBatteryControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.marker = Path(self.temporary.name) / "owner.json"
        self.clock = Clock()
        self.patched_time = patch.object(driver, "time", SimpleNamespace(sleep=self.clock.sleep))
        self.patched_time.start()
        self.addCleanup(self.patched_time.stop)
        self.inverter = SerialInverter(self.clock)

    def owner(self, **overrides):
        marker = {
            "schema_version": 1, "owner": "tng_iq_fanda",
            "profile_id": "foxess_h3_10_0_e", "device_id": 247,
            "original_work_mode": 0, "original_timeout": 3600,
        }
        marker.update(overrides)
        self.marker.write_text(json.dumps(marker), encoding="utf-8")
        return marker

    def apply(self, action="auto", **kwargs):
        values = dict(
            client=self.inverter, device_id=247, action=action,
            allowed_charge_power_w=500 if action == "charge_grid" else 0,
            allowed_discharge_power_w=500 if action == "discharge_grid" else 0,
            target_soc_percent=90, minimum_discharge_soc_percent=20,
            marker_path=self.marker,
        )
        values.update(kwargs)
        return driver.apply_foxess_h3_action(**values)

    def test_delayed_register_update_is_polled_before_rewrite(self):
        # Rewriting every 0.8 seconds restarts this device's 1.1-second
        # update forever; the old write/read/rewrite sequence fails here.
        self.inverter.registers[44000] = 1
        self.inverter.delays[44000] = 1.1
        driver._write_one(client=self.inverter, device_id=247, address=44000, value=0)
        self.assertEqual(self.inverter.registers[44000], 0)
        self.assertEqual(self.inverter.writes, [(44000, 0)])

    def test_persistent_rejected_enable_write_is_never_success(self):
        self.inverter.registers[44000] = 1
        self.inverter.ignored.add(44000)
        with self.assertRaisesRegex(RuntimeError, "44000: ocekavano 0, nacteno 1"):
            driver._write_one(client=self.inverter, device_id=247, address=44000, value=0)
        self.assertEqual(self.inverter.writes, [(44000, 0)] * 3)

    def test_ack_loss_requires_real_readback_and_does_not_duplicate_write(self):
        self.inverter.lost_ack.add(44000)
        driver._write_one(client=self.inverter, device_id=247, address=44000, value=1)
        self.assertEqual(self.inverter.writes, [(44000, 1)])
        self.assertEqual(self.inverter.registers[44000], 1)

    def test_one_matching_read_followed_by_reversion_is_not_success(self):
        self.inverter.ignored.add(44000)
        read = self.inverter.read_holding_registers
        def transient_read(**kwargs):
            result = read(**kwargs)
            if len(self.inverter.reads) == 1:
                return Response([1])
            return result
        with patch.object(self.inverter, "read_holding_registers", transient_read):
            with self.assertRaisesRegex(RuntimeError, "44000: ocekavano 1, nacteno 0"):
                driver._write_one(client=self.inverter, device_id=247, address=44000, value=1)
        self.assertEqual(len(self.inverter.writes), 3)

    def test_transport_read_failures_are_bounded(self):
        self.inverter.failed_reads.add(44000)
        with self.assertRaises(TimeoutError):
            driver._write_one(client=self.inverter, device_id=247, address=44000, value=1)
        self.assertEqual(len(self.inverter.writes), 3)
        self.assertEqual(len(self.inverter.reads), 9)

    def test_auto_without_ownership_does_not_claim_active_remote_is_stopped(self):
        self.inverter.registers[44000] = 1
        with self.assertRaisesRegex(RuntimeError, "AUTO neni potvrzeno"):
            self.apply()
        self.assertEqual(self.inverter.writes, [])
        self.assertFalse(self.marker.exists())

    def test_auto_already_disabled_without_owner_is_noop(self):
        result = self.apply()
        self.assertTrue(result.verified)
        self.assertFalse(result.write_performed)
        self.assertEqual(self.inverter.writes, [])

    def test_owned_stop_restores_all_original_settings_then_removes_marker(self):
        self.owner()
        self.inverter.registers.update({41000: 2, 44000: 1, 44001: 180, 44002: 500})
        result = self.apply()
        self.assertTrue(result.verified)
        self.assertEqual(self.inverter.writes, [(44002, 0), (44000, 0), (44001, 3600), (41000, 0)])
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.inverter.reads[-4:], [(44002, 1), (44000, 1), (44001, 1), (41000, 1)])

    def test_interrupted_stop_keeps_owner_and_can_resume_after_restart(self):
        self.owner()
        self.inverter.registers.update({41000: 2, 44000: 1, 44001: 180, 44002: 500})
        self.inverter.ignored.add(44000)
        with self.assertRaisesRegex(RuntimeError, "44000: ocekavano 0, nacteno 1"):
            self.apply()
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.inverter.registers[44002], 0)
        self.inverter.ignored.clear()
        self.assertTrue(self.apply().verified)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.inverter.registers[41000], 0)

    def test_final_remote_race_does_not_discard_owner_or_report_auto(self):
        self.owner()
        self.inverter.registers.update({41000: 2, 44000: 1, 44001: 180, 44002: 500})
        def competing_controller(address, value):
            if address == 41000:
                self.inverter.registers[44000] = 1
        self.inverter.after_write = competing_controller
        with self.assertRaisesRegex(RuntimeError, "AUTO final readback 44000"):
            self.apply()
        self.assertTrue(self.marker.exists())

    def test_corrupt_restore_settings_are_rejected_before_any_write(self):
        self.owner(original_timeout="not-an-integer")
        self.inverter.registers.update({44000: 1, 44002: 500})
        with self.assertRaisesRegex(RuntimeError, "puvodni timeout"):
            self.apply()
        self.assertEqual(self.inverter.writes, [])
        self.assertTrue(self.marker.exists())

    def test_charge_and_discharge_use_single_signed16_word_with_serial_gaps(self):
        self.inverter.minimum_gap = 0.03
        for action, raw in (("charge_grid", 65536 - 500), ("discharge_grid", 500)):
            with self.subTest(action=action):
                result = self.apply(action)
                self.assertTrue(result.verified)
                self.assertEqual(self.inverter.registers[44002], raw)
                self.assertTrue(self.marker.exists())
                self.assertTrue(self.apply().verified)
                self.assertFalse(self.marker.exists())
        self.assertTrue(all(address != 44003 for address, _ in self.inverter.writes))

    def test_foreign_remote_owner_cannot_be_taken_over_for_charge(self):
        self.inverter.registers[44000] = 1
        with self.assertRaisesRegex(RuntimeError, "mimo TNG IQ FANDA"):
            self.apply("charge_grid")
        self.assertEqual(self.inverter.writes, [])

    def test_rejected_work_mode_never_sends_charge_or_discharge_power(self):
        for action in ("charge_grid", "discharge_grid"):
            with self.subTest(action=action):
                self.inverter.ignored.add(41000)
                self.inverter.writes.clear()
                with self.assertRaisesRegex(RuntimeError, "41000: ocekavano 2, nacteno 0"):
                    self.apply(action)
                self.assertFalse(any(a == 44002 and v != 0 for a, v in self.inverter.writes))

    def test_target_soc_ends_session_with_typed_stop(self):
        self.inverter.registers[31038] = 90
        with self.assertRaises(driver.BatteryControlSafetyStop) as caught:
            self.apply("charge_grid")
        self.assertEqual(caught.exception.reason, "target_soc_reached")
        self.assertEqual(caught.exception.observed_soc_percent, 90)
        self.assertEqual(self.inverter.writes, [])

    def test_inverter_max_soc_ends_session_with_distinct_reason(self):
        self.inverter.registers.update({31038: 80, 41010: 80})
        with self.assertRaises(driver.BatteryControlSafetyStop) as caught:
            self.apply("charge_grid")
        self.assertEqual(caught.exception.reason, "inverter_max_soc_reached")

    def test_existing_discharge_two_point_margin_remains_enforced(self):
        self.inverter.registers[31038] = 22
        with self.assertRaises(driver.BatteryControlSafetyStop) as caught:
            self.apply("discharge_grid")
        self.assertEqual(caught.exception.reason, "minimum_soc_reached")
        self.assertEqual(self.inverter.writes, [])

    def test_failed_auto_at_soc_limit_is_error_not_normal_completed_session(self):
        self.owner()
        self.inverter.registers.update({31038: 90, 44000: 1})
        self.inverter.ignored.add(44000)
        with self.assertRaisesRegex(RuntimeError, "44000: ocekavano 0, nacteno 1") as caught:
            self.apply("charge_grid")
        self.assertNotIsInstance(caught.exception, driver.BatteryControlSafetyStop)
        self.assertTrue(self.marker.exists())

    def test_non_finite_soc_is_rejected_before_modbus(self):
        for key in ("target_soc_percent", "minimum_discharge_soc_percent"):
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        self.apply("charge_grid", **{key: value})
        self.assertEqual(self.inverter.reads, [])
        self.assertEqual(self.inverter.writes, [])


if __name__ == "__main__":
    unittest.main()
