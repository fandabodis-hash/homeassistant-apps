"""Read-only FoxESS evidence: firmware words and sanitized owner state."""
from __future__ import annotations

from contextlib import nullcontext
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


def _load_probe():
    pymodbus = ModuleType("pymodbus")
    client = ModuleType("pymodbus.client")
    client.ModbusSerialClient = _forbidden_hardware
    pymodbus.client = client
    goodwe = ModuleType("communication.goodwe_probe")
    goodwe._find_communicator = _forbidden_hardware
    spec = importlib.util.spec_from_file_location(
        "_foxess_remote_control_probe_under_test",
        APP / "communication" / "foxess_remote_control_probe.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with patch.dict(sys.modules, {
        "pymodbus": pymodbus,
        "pymodbus.client": client,
        "communication.goodwe_probe": goodwe,
    }):
        spec.loader.exec_module(module)
    return module


probe = _load_probe()


class ReadOnlyInverter:
    def __init__(self):
        self.registers = {
            30028: 115, 41000: 0, 41010: 100, 44000: 1,
            44001: 180, 44002: 65536 - 500, 31036: 65536 - 374,
            31038: 72, 30016: 0x0223, 30017: 0x0105, 30018: 0x0195,
        }
        self.reads = []
        self.failed = set()
        self.closed = False

    def connect(self):
        return True

    def close(self):
        self.closed = True

    def read_holding_registers(self, *, address, count, device_id):
        if device_id != 247:
            raise AssertionError("Unexpected slave")
        self.reads.append((address, count))
        if address in self.failed:
            raise TimeoutError("Private path /dev/serial/by-id/PRIVATE_USB_ID")
        return SimpleNamespace(
            registers=[self.registers[address + i] for i in range(count)],
            isError=lambda: False,
        )

    def write_register(self, **kwargs):
        raise AssertionError("Diagnostic must never write")

    def write_registers(self, **kwargs):
        raise AssertionError("Diagnostic must never write")


class FoxessReadOnlyProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.marker_path = Path(self.temporary.name) / "owner.json"
        self.device = ReadOnlyInverter()
        self.sleeps = []
        patches = (
            patch.object(probe, "OWNER_MARKER_PATH", self.marker_path),
            patch.object(probe, "ModbusSerialClient", lambda **kwargs: self.device),
            patch.object(probe, "_find_communicator", lambda identifier: (
                None, "/dev/serial/by-id/PRIVATE_USB_ID"
            )),
            patch.object(probe, "ziskej_zamek_modbus_sbernice", lambda path: nullcontext()),
            patch.object(probe, "time", SimpleNamespace(sleep=self.sleeps.append)),
        )
        for active in patches:
            active.start()
            self.addCleanup(active.stop)

    def owner(self, **overrides):
        marker = {
            "schema_version": 1, "owner": "tng_iq_fanda",
            "profile_id": "foxess_h3_10_0_e", "device_id": 247,
            "original_work_mode": 0, "original_timeout": 20804,
            "private_field": "PRIVATE_MARKER_DATA",
        }
        marker.update(overrides)
        self.marker_path.write_text(json.dumps(marker), encoding="utf-8")
        return self.marker_path.read_bytes()

    def run_probe(self):
        return probe.probe_foxess_remote_control_from_cloud_config(
            cloud_config={"module_runtime_configurations": [{
                "module_key": "photovoltaic", "profile_id": "foxess_h3_10_0_e",
                "communication_type": "rs485", "communicator_id": "fixture",
                "modbus_device_id": 247,
            }]}
        )

    def test_reads_three_profile_firmware_words_and_preserves_owner(self):
        before = self.owner()
        result = self.run_probe()
        self.assertEqual(result["firmware_versions_raw"], {
            "30016": 0x0223, "30017": 0x0105, "30018": 0x0195,
        })
        self.assertTrue(result["firmware_read_complete"])
        self.assertEqual(self.device.reads[-3:], [(30016, 1), (30017, 1), (30018, 1)])
        self.assertEqual(result["owner_marker"], {
            "present": True, "owner": "tng_iq_fanda", "status": "owned",
            "restore_settings_valid": True,
        })
        self.assertEqual(self.marker_path.read_bytes(), before)
        self.assertEqual(result["registers"]["remote_active_power_w_44002"], -500)
        self.assertEqual(result["registers"]["battery_power_w_31036"], -374)
        self.assertTrue(result["read_only"])
        self.assertFalse(result["write_performed"])
        self.assertTrue(self.device.closed)
        self.assertEqual(self.sleeps, [0.03] * len(self.device.reads))
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_optional_firmware_failure_keeps_core_evidence_without_private_error(self):
        self.device.failed.add(30017)
        result = self.run_probe()
        self.assertFalse(result["firmware_read_complete"])
        self.assertIsNone(result["firmware_versions_raw"]["30017"])
        self.assertEqual(result["firmware_versions_raw"]["30018"], 0x0195)
        self.assertEqual(result["registers"]["remote_enable_44000"], 1)
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_core_register_failure_still_fails_and_closes_serial(self):
        self.device.failed.add(44000)
        with self.assertRaises(TimeoutError):
            self.run_probe()
        self.assertTrue(self.device.closed)

    def test_foreign_and_invalid_markers_are_classified_without_mutation(self):
        for overrides, owner, status in (
            ({"owner": "PRIVATE_FOREIGN_OWNER"}, "other", "foreign"),
            ({"device_id": 1}, "tng_iq_fanda", "invalid"),
            ({"original_timeout": "bad"}, "tng_iq_fanda", "invalid"),
        ):
            with self.subTest(overrides=overrides):
                before = self.owner(**overrides)
                result = self.run_probe()
                self.assertEqual(result["owner_marker"]["owner"], owner)
                self.assertEqual(result["owner_marker"]["status"], status)
                self.assertEqual(self.marker_path.read_bytes(), before)
                self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_missing_corrupt_and_unreadable_markers_are_explicit(self):
        self.assertEqual(probe._owner_marker_status(self.marker_path), {
            "present": False, "owner": None, "status": "missing",
        })
        self.marker_path.write_bytes(b"not json")
        self.assertEqual(probe._owner_marker_status(self.marker_path), {
            "present": True, "owner": None, "status": "invalid",
        })
        with patch.object(Path, "read_text", side_effect=PermissionError("PRIVATE_PATH")):
            self.assertEqual(probe._owner_marker_status(self.marker_path), {
                "present": None, "owner": None, "status": "unreadable",
            })

    def test_firmware_addresses_match_profile_identification_map(self):
        profile = json.loads((
            APP / "communication" / "inverter_profiles" / "foxess_h3_10_0_e.json"
        ).read_text(encoding="utf-8"))
        versions = profile["identification"]["versions"]
        self.assertEqual(probe.FIRMWARE_VERSION_REGISTERS, tuple(range(
            versions["address"], versions["address"] + versions["count"]
        )))


if __name__ == "__main__":
    unittest.main()
