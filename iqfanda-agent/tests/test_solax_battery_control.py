"""Offline protocol/ownership tests. No serial port or hardware is accessed."""

from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(APP))

from communication import solax_ems_controller as driver
from communication.battery_control_safety import BatteryControlSafetyStop


class Response(SimpleNamespace):
    def isError(self):
        return getattr(self, "error", False)


def ascii_words(value):
    raw = value.ljust(14).encode("ascii")
    return [int.from_bytes(raw[i:i + 2], "big") for i in range(0, 14, 2)]


class FakeInverter:
    """Separate FC03 and FC04 maps; the command address is never its mirror."""

    def __init__(self):
        self.holding = {}
        self.input = {0x54: 0, 0x100: 0, 0x11C: 100, 0x11D: 10}
        self.log = []
        self.reject_remote = False
        self.reject_auto = False
        self.bad_ack_once = False
        self.ignore_remote = False
        self.reject_unlock = False
        self.drop_reads_after_remote = False
        self.incomplete_read = None
        self.set_holding(0, ascii_words("H34A15L7224081")
                         + ascii_words("Solax") + ascii_words("X3-Hybrid G4"))
        self.set_holding(0x7D, [60, 0, 1, 1, 0, 100, 58])
        self.set_holding(0x8B, [0, 0, 1, 0, 0, 300, 300, 0,
                                (10 << 8) | 1, 100, (100 << 8) | 10, (100 << 8) | 30])
        self.set_input(0x14, [4000, 25, 1000, 1, 25, 1, 0, 2, 50,
                              0, 0, 0, 0, 0, 0, 0, 100, 100])
        self.set_input(0x3E, [0] * 8)

    def set_holding(self, address, words):
        self.holding.update({address + i: word for i, word in enumerate(words)})

    def set_input(self, address, words):
        self.input.update({address + i: word for i, word in enumerate(words)})

    def _read(self, fc, address, count, device_id):
        self.log.append((fc, address, count, device_id))
        if self.drop_reads_after_remote:
            return Response(error=True)
        mapping = self.input if fc == 4 else self.holding
        values = [mapping[address + offset] for offset in range(count)]
        if (fc, address) == self.incomplete_read:
            values = values[:-1]
        return Response(registers=values)

    def read_holding_registers(self, *, address, count, device_id):
        return self._read(3, address, count, device_id)

    def read_input_registers(self, *, address, count, device_id):
        return self._read(4, address, count, device_id)

    def write_register(self, *, address, value, device_id):
        self.log.append((6, address, value, device_id))
        assert address == 0
        if not (value == 2014 and self.reject_unlock):
            self.input[0x54] = 1 if value == 2014 else 0
        return Response(address=address, registers=[value])

    def write_registers(self, *, address, values, device_id):
        self.log.append((16, address, list(values), device_id))
        assert address == 0x7C
        assert len(values) == 15
        assert self.input[0x54] == 1
        if values[0] == 4 and self.reject_remote:
            return Response(error=True)
        if values[0] == 0 and self.reject_auto:
            return Response(error=True)
        if not (values[0] == 4 and self.ignore_remote):
            self.input[0x100] = values[0]
        count = 14 if self.bad_ack_once else 15
        self.bad_ack_once = False
        return Response(address=address, count=count)

    @property
    def writes(self):
        return [entry for entry in self.log if entry[0] in (6, 16)]

    @property
    def remote_writes(self):
        return [entry for entry in self.log if entry[0] == 16]


class SolaxControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.marker = Path(self.temp.name) / "owner.json"
        self.inverter = FakeInverter()
        self.time = 0.0

    def sleep(self, seconds):
        self.assertGreaterEqual(seconds, 0)
        self.time += seconds

    def apply(self, action="charge_grid", **kwargs):
        parameters = dict(client=self.inverter, device_id=1, action=action,
                          allowed_charge_power_w=1000, target_soc_percent=80,
                          allowed_discharge_power_w=1000, minimum_discharge_soc_percent=20,
                          marker_path=self.marker, transport_id="/dev/serial/by-id/test",
                          sleeper=self.sleep, clock=lambda: self.time)
        parameters.update(kwargs)
        return driver.apply_solax_x3_g4_action(**parameters)

    def test_charge_exact_signed_write_and_distinct_mode_readback(self):
        result = self.apply()
        self.assertEqual(self.inverter.remote_writes[0][2],
                         [4, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 60, 0xFC18, 0xFFFF])
        self.assertTrue(result.verified)
        self.assertEqual(result.applied_power_w, 1000)
        self.assertEqual(result.observed_battery_power_w, -1000)
        self.assertEqual(result.verification_scope, "accepted_write_and_remote_mode_readback")
        self.assertFalse(result.hardware_watchdog_verified)
        self.assertIn((4, 0x100, 1, 1), self.inverter.log)
        self.assertFalse(any(entry[0] == 3 and entry[1] == 0x7C for entry in self.inverter.log))

    def test_discharge_word_order_and_sign(self):
        result = self.apply("discharge_grid")
        self.assertEqual(self.inverter.remote_writes[0][2][13:], [1000, 0])
        self.assertEqual(result.applied_power_w, 1000)

    def test_power_is_clamped_to_fresh_bms_voltage_and_direction_current(self):
        self.inverter.input[0x24] = 30  # 400 V x 3 A = 1200 W charging
        self.inverter.input[0x25] = 20  # 400 V x 2 A = 800 W discharging
        charge = self.apply(allowed_charge_power_w=5000)
        discharge = self.apply("discharge_grid", allowed_discharge_power_w=5000)
        self.assertEqual((charge.applied_power_w, discharge.applied_power_w), (1200, 800))
        self.assertEqual(charge.requested_power_w, 5000)

    def test_configured_reserve_and_margin_are_not_overridden(self):
        self.inverter.holding[0x93] = 45 << 8
        self.inverter.input[0x1C] = 47
        with self.assertRaises(BatteryControlSafetyStop) as raised:
            self.apply("discharge_grid", minimum_discharge_soc_percent=10)
        self.assertEqual(raised.exception.reason, "minimum_soc_reached")
        self.assertEqual(self.inverter.writes, [])

    def test_feed_in_and_backup_reserve_packing(self):
        for mode, address in ((1, 0x95), (2, 0x96)):
            with self.subTest(mode=mode):
                self.inverter = FakeInverter()
                self.inverter.holding[0x8B] = mode
                self.inverter.holding[address] = (100 << 8) | 49
                with self.assertRaises(BatteryControlSafetyStop):
                    self.apply("discharge_grid")
                self.assertFalse(self.inverter.writes)

    def test_target_soc_stops_before_any_write(self):
        self.inverter.input[0x1C] = 80
        with self.assertRaises(BatteryControlSafetyStop) as raised:
            self.apply()
        self.assertEqual(raised.exception.reason, "target_soc_reached")
        self.assertEqual(raised.exception.observed_soc_percent, 80)
        self.assertEqual(self.inverter.writes, [])
        self.assertFalse(self.marker.exists())

    def test_live_limits_faults_and_disconnection_stop_without_writes(self):
        cases = [(0x17, 0), (0x1A, 1), (0x1C, 101), (0x24, 0),
                 (0x24, 0xFFFF), (0x14, 0), (0x3E, 1), (0x3F, 1),
                 (0x40, 1), (0x43, 1), (0x44, 1), (0x45, 1), (0x11C, 0)]
        for address, value in cases:
            with self.subTest(address=hex(address), value=value):
                self.inverter = FakeInverter()
                self.inverter.input[address] = value
                with self.assertRaises(BatteryControlSafetyStop):
                    self.apply()
                self.assertEqual(self.inverter.writes, [])

    def test_foreign_remote_mode_is_never_taken_over_even_on_stop(self):
        for mode in (1, 2, 3, 4, 8, 9):
            for action in ("charge_grid", "auto"):
                with self.subTest(mode=mode, action=action):
                    self.inverter = FakeInverter()
                    self.inverter.input[0x100] = mode
                    with self.assertRaises(RuntimeError):
                        self.apply(action)
                    self.assertEqual(self.inverter.writes, [])

    def test_stop_restores_mode_and_lock_without_touching_persistent_settings(self):
        before = dict(self.inverter.holding)
        self.apply()
        self.inverter.input[0x1C] = 100
        self.inverter.input[0x17] = 0  # A BMS stop must still allow AUTO.
        result = self.apply("auto")
        self.assertTrue(result.verified)
        self.assertEqual(self.inverter.remote_writes[-1][2], [0] * 15)
        self.assertEqual(self.inverter.input[0x100], 0)
        self.assertEqual(self.inverter.input[0x54], 0)
        self.assertEqual(self.inverter.holding, before)
        self.assertFalse(self.marker.exists())

    def test_existing_unlocked_state_is_restored(self):
        self.inverter.input[0x54] = 1
        self.apply()
        self.apply("auto")
        self.assertEqual(self.inverter.input[0x54], 1)
        self.assertFalse(any(item[0] == 6 for item in self.inverter.writes))

    def test_auto_without_owner_is_idempotent_and_does_not_write(self):
        self.assertFalse(self.apply("auto").write_performed)
        self.assertFalse(self.apply("auto").write_performed)
        self.assertEqual(self.inverter.writes, [])

    def test_repeated_ticks_preserve_original_snapshot(self):
        self.apply()
        original = self.marker.read_text()
        self.apply("discharge_grid")
        self.assertEqual(self.marker.read_text(), original)
        self.assertEqual(self.inverter.remote_writes[-1][2][13:], [1000, 0])

    def test_bad_ack_is_failure_and_rolls_back_to_auto(self):
        self.inverter.bad_ack_once = True
        with self.assertRaisesRegex(RuntimeError, "acknowledge"):
            self.apply()
        self.assertEqual(self.inverter.input[0x100], 0)
        self.assertEqual(self.inverter.input[0x54], 0)
        self.assertFalse(self.marker.exists())

    def test_ack_without_mode_readback_is_not_reported_as_verified(self):
        self.inverter.ignore_remote = True
        with self.assertRaisesRegex(RuntimeError, "remote mode readback"):
            self.apply()
        self.assertEqual(self.inverter.input[0x100], 0)
        self.assertEqual(self.inverter.input[0x54], 0)
        self.assertFalse(self.marker.exists())
        # A zero readback may be a delayed command, so explicitly cancel the
        # uncertain write instead of merely deleting its ownership marker.
        self.assertEqual([item[2][0] for item in self.inverter.remote_writes], [4, 0])

    def test_failed_unlock_does_not_leave_a_false_active_owner(self):
        self.inverter.reject_unlock = True
        with self.assertRaisesRegex(RuntimeError, "did not unlock"):
            self.apply()
        self.assertEqual(self.inverter.input[0x100], 0)
        self.assertEqual(self.inverter.input[0x54], 0)
        self.assertFalse(self.inverter.remote_writes)
        self.assertFalse(self.marker.exists())

    def test_failed_auto_retains_ownership_for_restart_recovery(self):
        self.apply()
        self.inverter.reject_auto = True
        with self.assertRaises(RuntimeError):
            self.apply("auto")
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.inverter.input[0x100], 4)
        self.inverter.reject_auto = False
        self.apply("auto")
        self.assertFalse(self.marker.exists())

    def test_command_rejected_restores_original_parameter_lock(self):
        self.inverter.reject_remote = True
        with self.assertRaises(RuntimeError):
            self.apply()
        self.assertEqual(self.inverter.input[0x54], 0)
        self.assertFalse(self.marker.exists())

    def test_corrupt_or_different_owner_refuses_all_writes(self):
        self.marker.write_text("invalid json")
        with self.assertRaises(RuntimeError):
            self.apply()
        self.assertFalse(self.inverter.writes)
        self.marker.unlink()
        self.apply()
        marker = json.loads(self.marker.read_text())
        marker["serial"] = "DIFFERENT"
        self.marker.write_text(json.dumps(marker))
        self.inverter.log.clear()
        with self.assertRaises(RuntimeError):
            self.apply("auto")
        self.assertFalse(self.inverter.writes)

    def test_owner_persistence_failure_precedes_first_physical_write(self):
        with patch.object(driver, "_save_marker", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertFalse(self.inverter.writes)

    def test_unsupported_firmware_or_wrong_model_do_not_write(self):
        for address, value in ((0x7D, 40), (0x83, 39), (0x7F, 2), (0x80, 2), (0xE, 0)):
            with self.subTest(address=address, value=value):
                self.inverter = FakeInverter()
                self.inverter.holding[address] = value
                with self.assertRaises(RuntimeError):
                    self.apply()
                self.assertFalse(self.inverter.writes)

    def test_invalid_requests_never_read_or_write_hardware(self):
        cases = [dict(device_id=2), dict(device_id=True), dict(action="unknown"),
                 dict(allowed_charge_power_w=0), dict(allowed_charge_power_w=True),
                 dict(allowed_charge_power_w=1.5), dict(target_soc_percent=float("nan")),
                 dict(minimum_discharge_soc_percent=float("inf"))]
        for params in cases:
            with self.subTest(params=params):
                self.inverter.log.clear()
                with self.assertRaises(ValueError):
                    self.apply(**params)
                self.assertEqual(self.inverter.log, [])

    def test_incomplete_reads_do_not_create_writes(self):
        self.inverter.incomplete_read = (4, 0x14)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.apply()
        self.assertFalse(self.inverter.writes)

    def test_transport_observes_manufacturer_command_interval(self):
        self.apply()
        self.assertGreaterEqual(self.time, len(self.inverter.log) - 1)

    def test_restore_does_not_guess_or_overwrite_changed_work_mode(self):
        self.apply()
        self.inverter.holding[0x8B] = 2
        with self.assertRaisesRegex(RuntimeError, "original work mode"):
            self.apply("auto")
        self.assertEqual(self.inverter.input[0x100], 0)
        self.assertEqual(self.inverter.holding[0x8B], 2)
        self.assertTrue(self.marker.exists())

    def test_profile_distinguishes_protocol_implementation_from_hardware_verification(self):
        profile = json.loads((APP / "communication/inverter_profiles/solax_x3_hybrid_g4.json").read_text())
        capability = profile["control_capabilities"]["battery_charge_control"]
        self.assertEqual(set(capability["supported_actions"]), driver.SUPPORTED_ACTIONS)
        self.assertIn("hardware_canary_pending", capability["verification_status"])
        self.assertFalse(capability["hardware_watchdog_verified"])


if __name__ == "__main__":
    unittest.main()
