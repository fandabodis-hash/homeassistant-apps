"""Physical-canary logic over the actual FoxESS driver and simulated serial I/O."""
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


def _no_hardware(*args, **kwargs):
    raise AssertionError("Tests must never open a real serial port")


def _load_modules():
    import communication
    pymodbus = ModuleType("pymodbus")
    client = ModuleType("pymodbus.client")
    client.ModbusSerialClient = _no_hardware
    pymodbus.client = client
    goodwe = ModuleType("communication.goodwe_probe")
    goodwe._find_communicator = _no_hardware
    spec = importlib.util.spec_from_file_location(
        "_canary_foxess_driver_under_test", APP / "communication" / "foxess_ems_controller.py"
    )
    driver = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = driver
    with patch.dict(sys.modules, {
        "pymodbus": pymodbus, "pymodbus.client": client, "communication.goodwe_probe": goodwe,
    }):
        spec.loader.exec_module(driver)
    updater = ModuleType("agent_updater")
    updater.read_installed_agent_version = lambda: "0.1.168"
    spec = importlib.util.spec_from_file_location(
        "_battery_canary_under_test", APP / "battery_control_canary.py"
    )
    canary = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = canary
    with patch.dict(sys.modules, {"agent_updater": updater, "communication.foxess_ems_controller": driver}):
        with patch.object(communication, "foxess_ems_controller", driver, create=True):
            spec.loader.exec_module(canary)
    return driver, canary


driver, canary = _load_modules()


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


class BusLock:
    def __init__(self):
        self.held = False
        self.entries = 0
        self.exits = 0

    def __enter__(self):
        if self.held:
            raise AssertionError("Canary must use a single locked client")
        self.held = True
        self.entries += 1

    def __exit__(self, *args):
        self.held = False
        self.exits += 1


class SerialInverter:
    def __init__(self, lock):
        self.lock = lock
        self.registers = {
            30028: 115, 41000: 0, 41010: 100, 44000: 0, 44001: 20804,
            44002: 0, 31038: 50, 30016: 0x0223, 30017: 0x0105, 30018: 0x0195,
        }
        self.measured = {500: [551, 571, 559], -500: [-374, -352, -390]}
        self.writes = []
        self.reads = []
        self.failed_reads = set()
        self.ignored_writes = set()
        self.reject_final_disable = False
        self.charge_started = False
        self.revert_active_mode = False
        self.active_power_read_error = None
        self.connected = False
        self.closed = False

    def connect(self):
        assert self.lock.held
        self.connected = True
        return True

    def close(self):
        assert self.lock.held
        self.closed = True

    def read_holding_registers(self, *, address, count, device_id):
        assert self.lock.held and device_id == 247 and count == 1
        self.reads.append((address, count))
        if address in self.failed_reads:
            raise TimeoutError("PRIVATE_USB_PATH and PRIVATE_AUTH_TOKEN")
        if address == 31036:
            setpoint = driver._decode_s16(self.registers[44002])
            if self.registers[44000] == 1 and setpoint:
                if self.active_power_read_error:
                    raise self.active_power_read_error
                samples = self.measured.get(setpoint, [setpoint])
                value = samples[0] if len(samples) == 1 else samples.pop(0)
            else:
                # AUTO can legitimately charge/discharge under self-use;
                # only the remote setpoint must be zero after restoration.
                value = -1200
        else:
            value = self.registers[address]
        return SimpleNamespace(registers=[value & 65535], isError=lambda: False)

    def write_register(self, *, address, value, device_id):
        assert self.lock.held and device_id == 247
        self.writes.append((address, value))
        reject = address in self.ignored_writes or (
            self.reject_final_disable and self.charge_started and address == 44000 and value == 0
        )
        if not reject:
            self.registers[address] = value
        if address == 44002 and driver._decode_s16(value) == -500:
            self.charge_started = True
        if address == 44002 and value == 500 and self.revert_active_mode:
            self.registers[41000] = 0
        return SimpleNamespace(isError=lambda: False)

    def write_registers(self, **kwargs):
        raise AssertionError("Canary must not introduce multiword control writes")


class BatteryCanaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.marker = Path(self.temporary.name) / "owner.json"
        self.clock = Clock()
        self.lock = BusLock()
        self.device = SerialInverter(self.lock)
        self.factory_calls = []
        def factory(**kwargs):
            self.factory_calls.append(kwargs)
            return self.device
        for active in (
            patch.object(driver, "time", self.clock),
            patch.object(canary, "time", self.clock),
            patch.object(driver, "OWNER_MARKER_PATH", self.marker),
            patch.object(driver, "ModbusSerialClient", factory),
            patch.object(driver, "_find_communicator", lambda identifier: (None, "/dev/serial/by-id/PRIVATE_USB_PATH")),
            patch.object(driver, "ziskej_zamek_modbus_sbernice", lambda path: self.lock),
        ):
            active.start()
            self.addCleanup(active.stop)
        self.config = {"cloud_secret": "PRIVATE_AUTH_TOKEN", "module_runtime_configurations": [{
            "module_key": "photovoltaic", "manufacturer": "foxess", "model": "H3-10.0-E",
            "profile_id": "foxess_h3_10_0_e", "communication_type": "rs485",
            "communicator_id": "fixture", "modbus_device_id": 247,
            "private_credential": "PRIVATE_AUTH_TOKEN",
        }]}

    def run_canary(self):
        result = canary.run_foxess_battery_canary(cloud_config=self.config, command_id="fixture-command")
        self.assertNotIn("PRIVATE_", json.dumps(result))
        return result

    def write_owner(self, **overrides):
        marker = {"schema_version": 1, "owner": "tng_iq_fanda", "profile_id": "foxess_h3_10_0_e",
                  "device_id": 247, "original_work_mode": 0, "original_timeout": 20804}
        marker.update(overrides)
        self.marker.write_text(json.dumps(marker), encoding="utf-8")

    def assert_stopped(self):
        self.assertEqual(self.device.registers[44000], 0)
        self.assertEqual(self.device.registers[44002], 0)
        self.assertEqual(self.device.registers[41000], 0)
        self.assertEqual(self.device.registers[44001], 20804)
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.device.closed)
        self.assertFalse(self.lock.held)

    def test_pass_requires_both_measured_directions_and_restored_auto(self):
        result = self.run_canary()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["verified"])
        self.assertEqual(result["verification_scope"], "bidirectional_500w_and_stop")
        self.assertEqual(result["agent_version"], "0.1.168")
        self.assertEqual(result["firmware_versions_raw"], {"30016": 0x0223, "30017": 0x0105, "30018": 0x0195})
        self.assertEqual([phase["requested_setpoint_w"] for phase in result["phases"]], [500, -500])
        self.assertEqual([[sample["registers"]["battery_power_w"] for sample in phase["samples"]] for phase in result["phases"]], [[551, 571, 559], [-374, -352, -390]])
        self.assertTrue(all(phase["consecutive_matching_samples"] == 3 for phase in result["phases"]))
        self.assertTrue(result["between_phases_auto"]["verified"])
        self.assertTrue(result["final_auto"]["verified"])
        self.assertEqual(result["final_auto"]["registers"]["battery_power_w"], -1200)
        self.assertTrue(all(address != 44003 for address, _ in self.device.writes))
        self.assertEqual((self.lock.entries, self.lock.exits), (1, 1))
        self.assert_stopped()

    def test_ack_and_setpoint_without_actual_battery_response_never_pass(self):
        self.device.measured[500] = [0]
        result = self.run_canary()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "physical_power_not_confirmed")
        self.assertEqual(len(result["phases"]), 1)
        self.assertTrue(result["phases"][0]["driver_result"]["verified"])
        self.assertLessEqual(len(result["phases"][0]["samples"]), 12)
        self.assertTrue(result["final_auto"]["verified"])
        self.assert_stopped()

    def test_a_single_matching_sample_is_insufficient(self):
        self.device.measured[500] = [551, -500]
        result = self.run_canary()
        self.assertFalse(result["verified"])
        self.assertEqual(result["phases"][0]["consecutive_matching_samples"], 0)
        self.assert_stopped()

    def test_final_auto_failure_overrides_two_successful_directions(self):
        self.device.reject_final_disable = True
        result = self.run_canary()
        self.assertTrue(all(phase["status"] == "passed" for phase in result["phases"]))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["verified"])
        self.assertFalse(result["final_auto"]["verified"])
        self.assertEqual(result["final_auto"]["error"]["register"], 44000)
        self.assertEqual(self.device.registers[44002], 0)
        self.assertTrue(self.marker.exists())
        self.assertTrue(self.device.closed)

    def test_foreign_remote_controller_is_never_touched(self):
        self.device.registers.update({44000: 1, 44002: 600})
        result = self.run_canary()
        self.assertEqual(result["error"]["code"], "foreign_remote_owner")
        self.assertFalse(result["final_auto"]["attempted"])
        self.assertEqual(self.device.writes, [])
        self.assertEqual(self.device.registers[44002], 600)

    def test_foreign_owner_marker_is_never_overwritten(self):
        self.write_owner(owner="PRIVATE_OTHER_OWNER")
        before = self.marker.read_bytes()
        result = self.run_canary()
        self.assertEqual(result["error"]["code"], "foreign_or_invalid_owner")
        self.assertEqual(self.marker.read_bytes(), before)
        self.assertEqual(self.device.writes, [])

    def test_owned_leftover_is_restored_before_first_direction(self):
        self.write_owner()
        self.device.registers.update({44000: 1, 44002: 500, 44001: 180, 41000: 2})
        result = self.run_canary()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(self.device.writes[:4], [(44002, 0), (44000, 0), (44001, 20804), (41000, 0)])
        self.assert_stopped()

    def test_soc_outside_window_never_applies_power(self):
        for soc in (29, 91):
            with self.subTest(soc=soc):
                self.device.registers[31038] = soc
                result = self.run_canary()
                self.assertEqual(result["error"]["code"], "soc_outside_canary_window")
                self.assertEqual(self.device.writes, [])
                self.assertTrue(result["final_auto"]["verified"])

    def test_existing_maximum_soc_is_respected(self):
        self.device.registers[41010] = 50
        result = self.run_canary()
        self.assertEqual(result["error"]["code"], "charge_soc_limit_reached")
        self.assertEqual(self.device.writes, [])

    def test_missing_firmware_identity_never_starts_power(self):
        self.device.failed_reads.add(30017)
        result = self.run_canary()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["phase"], "preflight")
        self.assertEqual(self.device.writes, [])
        self.assertTrue(result["final_auto"]["verified"])

    def test_reverted_active_mode_fails_even_when_driver_setpoint_acknowledged(self):
        self.device.revert_active_mode = True
        result = self.run_canary()
        self.assertEqual(result["error"]["code"], "active_readback_mismatch")
        self.assertTrue(result["phases"][0]["driver_result"]["verified"])
        self.assert_stopped()

    def test_measurement_failure_returns_evidence_and_stops_without_private_error(self):
        self.device.active_power_read_error = TimeoutError("PRIVATE_USB_PATH PRIVATE_AUTH_TOKEN")
        result = self.run_canary()
        self.assertEqual(result["error"]["phase"], "discharge_grid")
        self.assertEqual(result["error"]["exception_type"], "TimeoutError")
        self.assertTrue(result["final_auto"]["verified"])
        self.assert_stopped()

    def test_finally_stops_even_during_process_interrupt(self):
        self.device.active_power_read_error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_canary()
        self.assert_stopped()

    def test_old_image_and_other_profile_are_rejected_before_serial_open(self):
        with patch.object(canary, "read_installed_agent_version", lambda: "0.1.167"):
            result = self.run_canary()
        self.assertEqual(result["error"]["code"], "agent_upgrade_required")
        self.assertEqual(self.factory_calls, [])
        self.config["module_runtime_configurations"][0]["model"] = "H3-15.0-E"
        result = self.run_canary()
        self.assertEqual(result["error"]["code"], "unsupported_profile")
        self.assertEqual(self.factory_calls, [])

    def test_actual_cloud_runtime_without_profile_id_resolves_exact_local_profile(self):
        runtime = self.config["module_runtime_configurations"][0]
        del runtime["profile_id"]
        runtime["configuration"] = {"communication": {"matched_device_id": 247}}
        result = self.run_canary()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["profile_id"], "foxess_h3_10_0_e")
        self.assertNotIn("profile_id", runtime)
        self.assert_stopped()

    def test_explicit_different_profile_is_never_replaced_by_model_inference(self):
        runtime = self.config["module_runtime_configurations"][0]
        for field in ("profile_id", "inverter_profile_id"):
            with self.subTest(field=field):
                runtime["profile_id"] = "foxess_h3_10_0_e"
                runtime.pop("inverter_profile_id", None)
                runtime[field] = "goodwe_et"
                result = self.run_canary()
                self.assertEqual(result["error"]["code"], "unsupported_profile")
                self.assertEqual(self.factory_calls, [])
                self.assertEqual(self.device.writes, [])


if __name__ == "__main__":
    unittest.main()
