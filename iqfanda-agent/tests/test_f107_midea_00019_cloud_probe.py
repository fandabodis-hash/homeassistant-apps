"""F107 contract: cloud command bridge may only read Modbus on exact FANDA 00019."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(ROOT))
from communication import midea_00019_cloud_probe as probe  # noqa: E402


class TestMideaCloudBridge(unittest.TestCase):
    def setUp(self):
        self.identity = {"device_uuid": probe.DEVICE_UUID, "site_id": probe.SITE_ID,
                         "device_token": "fake-token-not-used"}
        self.payload = dict(probe.EXPECTED)
        self.calls = []
        self.events = []

    def fake_probe(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "serial_number": probe.SERIAL,
            "read_only": True, "writes_performed": False,
            "probe_success": True, "physical_modbus_read": True,
            "measurements": {"water_flow_c": 40, "water_return_c": 35},
            "blocks": [{"address": 100, "count": 11, "status": "ok"}],
        }

    def send(self, *, identity=None, command_payload=None, driver=None):
        probe.execute_readonly_command(
            identity=self.identity if identity is None else identity,
            command_id="test-command-id",
            command_payload=self.payload if command_payload is None else command_payload,
            submit_result=lambda **x: self.events.append(x),
            probe=self.fake_probe if driver is None else driver,
        )
        return self.events[-1]

    def test_exact_read_only_job_returns_unverified_candidate(self):
        result = self.send()
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(result["result"]["profile_verified"])
        self.assertFalse(result["result"]["control_enabled"])
        self.assertFalse(result["result"]["writes_performed"])
        self.assertTrue(result["result"]["physical_modbus_read"])
        self.assertEqual(self.calls, [{
            "serial_number": probe.SERIAL, "port_path": probe.FTDI_PORT,
            "unit_id": 16, "function_code": 4,
        }])

    def test_bad_identity_and_site_never_open_port(self):
        for case in ("device_uuid", "site_id"):
            with self.subTest(case=case):
                identity = dict(self.identity)
                identity[case] = "00000000-0000-0000-0000-000000000000"
                self.assertEqual(self.send(identity=identity)["status"], "failed")
        self.assertEqual(self.calls, [])

    def test_arbitrary_modbus_command_not_allowed(self):
        for key, bad_value in [
            ("unit_id", 1), ("unit_id", True), ("function_code", 3),
            ("function_code", 6), ("port_path", "/dev/ttyUSB0"),
            ("read_only", False), ("serial_number", "F800711-TNG-00013"),
            ("scope", "write_any_register"),
        ]:
            with self.subTest(key=key, bad_value=bad_value):
                payload = dict(self.payload)
                payload[key] = bad_value
                self.assertEqual(self.send(command_payload=payload)["status"], "failed")
        payload = dict(self.payload)
        payload["register_address"] = 2
        self.assertEqual(self.send(command_payload=payload)["status"], "failed")
        self.assertEqual(self.calls, [])

    def test_result_provenance_does_not_allow_writes(self):
        def unsafe_driver(**kwargs):
            self.calls.append(kwargs)
            return {"serial_number": probe.SERIAL, "read_only": True,
                    "writes_performed": True}
        result = self.send(driver=unsafe_driver)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["result"]["reason"], "MIDEA_DRIVER_WRITE_GUARD")

    def test_failed_water_pair_keeps_partial_read_evidence(self):
        def partial_probe(**kwargs):
            return {
                "serial_number": probe.SERIAL, "read_only": True,
                "writes_performed": False, "probe_success": False,
                "physical_modbus_read": True,
                "raw_by_fc": {"3": {"0": 2}, "4": {}},
                "blocks": [{"function_code": 4, "address": 104,
                            "status": "modbus_exception", "exception_code": 1}],
            }
        event = self.send(driver=partial_probe)
        self.assertEqual(event["status"], "failed")
        self.assertTrue(event["result"]["physical_modbus_read"])
        self.assertFalse(event["result"]["probe_success"])
        self.assertFalse(event["result"]["control_enabled"])
        self.assertEqual(event["result"]["raw_by_fc"], {"3": {"0": 2}, "4": {}})
        self.assertEqual(event["result"]["blocks"][0]["exception_code"], 1)

    def test_missing_read_evidence_does_not_claim_success(self):
        def missing_probe(**kwargs):
            return {"serial_number": probe.SERIAL, "read_only": True,
                    "writes_performed": False}
        event = self.send(driver=missing_probe)
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["result"]["reason"], "MIDEA_DRIVER_MISSING_READ_EVIDENCE")


    def test_driver_exception_is_redacted(self):
        def unsafe_exception(**kwargs):
            raise RuntimeError("secret-should-not-be-reported")
        result = self.send(driver=unsafe_exception)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_message"], "RuntimeError")
        self.assertNotIn("secret-should-not-be-reported", str(result))


    def test_exact_bms1_scope_calls_only_unit_1(self):
        result=self.send(command_payload=dict(probe.EXPECTED_BMS1))
        self.assertEqual(result["status"],"succeeded")
        self.assertEqual(self.calls,[{"serial_number":probe.SERIAL,
            "port_path":probe.FTDI_PORT,"unit_id":1,"function_code":4}])

    def test_bms1_scope_requires_exact_identity_parameters(self):
        for key,value in (("unit_id",16),("unit_id",2),("unit_id",True),
                          ("function_code",6),("function_code",3),("read_only",False),
                          ("port_path","/dev/ttyUSB0"),("scope",probe.EXPECTED["scope"])):
            data=dict(probe.EXPECTED_BMS1);data[key]=value
            self.assertEqual(self.send(command_payload=data)["status"],"failed")
        self.assertEqual(self.calls,[])

    def test_bms1_default_dispatch_uses_correct_driver(self):
        from unittest.mock import patch
        with patch.object(probe,"probe_bms1_diagnostic_once",side_effect=self.fake_probe) as bms, \
             patch.object(probe,"probe_diagnostic_once") as legacy:
            probe.execute_readonly_command(identity=self.identity,command_id="test-bms",
                command_payload=dict(probe.EXPECTED_BMS1),
                submit_result=lambda **x:self.events.append(x))
            bms.assert_called_once()
            legacy.assert_not_called()
        self.assertEqual(self.events[-1]["status"],"succeeded")


if __name__ == "__main__":
    unittest.main()
