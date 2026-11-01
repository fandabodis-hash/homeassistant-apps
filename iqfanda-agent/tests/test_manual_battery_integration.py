"""Offline integration of the real SolaX driver and local ownership boundary."""

from datetime import timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_manual_battery_runtime as runtime_fixture
from test_solax_battery_control import FakeInverter
from communication import inverter_control_adapter as adapter
from communication import solax_ems_controller as solax
from communication.battery_control_safety import BatteryControlSafetyStop


class ManualBatteryIntegrationTests(unittest.TestCase):
    def fixture(self, manufacturer="foxess", model="H3-10.0-E", profile_id="foxess_h3_10_0_e"):
        rig = runtime_fixture.LocalRuntimeTests()
        rig.setUp()
        self.addCleanup(rig.doCleanups)
        rig.config["module_runtime_configurations"][0].update(
            manufacturer=manufacturer, model=model, profile_id=profile_id)
        return rig

    def passed_canary(self):
        auto = {"verified": True, "registers": {"protocol_version": 115,
            "remote_enable": 0, "remote_active_power_w": 0, "work_mode": 0,
            "remote_timeout_seconds": 180}}
        phases = []
        for action, sign in (("discharge_grid", 1), ("charge_grid", -1)):
            phases.append({"action": action, "status": "passed",
                "requested_setpoint_w": sign * 500,
                "samples": [{"elapsed_seconds": seconds, "readback_verified": True,
                    "physical_power_within_tolerance": True, "registers": {
                        "protocol_version": 115, "battery_power_w": sign * 500,
                        "remote_active_power_w": sign * 500, "remote_enable": 1,
                        "remote_timeout_seconds": 180, "work_mode": 2,
                        "battery_soc_percent": 50,
                    }} for seconds in (5, 10, 15)]})
        return {
            "schema_version": 1, "status": "passed", "verified": True,
            "profile_id": "foxess_h3_10_0_e",
            "verification_scope": "bidirectional_500w_and_stop",
            "phases": phases, "preflight": {"auto": auto},
            "between_phases_auto": auto, "final_auto": auto, "error": None,
        }

    def test_real_solax_driver_refreshes_without_cloud_then_restores_on_local_expiry(self):
        rig = self.fixture("solax", "X3-Hybrid G4", "solax_x3_hybrid_g4")
        rig.config["module_runtime_configurations"][0]["modbus_device_id"] = 1
        hardware = FakeInverter()
        owner = Path(rig.tmp.name) / "solax-owner.json"
        cache_reads = []
        rig.runtime.config_loader = lambda: (cache_reads.append(True) or rig.config)
        rig.runtime.execute = adapter.execute_battery_control_from_cloud_config

        def physical(**kwargs):
            kwargs.pop("cloud_config")
            return solax.apply_solax_x3_g4_action(
                client=hardware, device_id=1, marker_path=owner, transport_id="fake-bus",
                sleeper=rig.clock.advance, clock=lambda: rig.clock().timestamp(), **kwargs)

        with patch.object(solax, "execute_solax_x3_g4_from_cloud_config", side_effect=physical):
            rig.submit(manual_session_id="offline-session",
                       manual_session_expires_at=(rig.clock() + timedelta(seconds=600)).isoformat())
            rig.clock.advance(181)  # The transport lease is now expired.
            result = rig.runtime.tick()
            self.assertEqual(result["state"], "active")
            self.assertEqual(sum(item[2][0] == 4 for item in hardware.remote_writes), 2)
            self.assertEqual(len(cache_reads), 1)
            rig.clock.advance(600)
            result = rig.runtime.tick()
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["reason"], "authorization_expired")
        self.assertEqual(hardware.input[0x100], 0)
        self.assertEqual(hardware.remote_writes[-1][2], [0] * 15)
        self.assertFalse(owner.exists())

    def test_deye_unsupported_start_never_persists_or_attempts_physical_cleanup(self):
        for model, profile, reason in (
            ("3xDEYE LV off grid", "deye_3x_lv_off_grid", "off_grid_fixed_battery_power_unavailable"),
            ("Deye 50 hybrid HV", "deye_50_hybrid_hv", "deye_hv_control_protocol_unverified"),
        ):
            with self.subTest(model=model):
                rig = self.fixture("deye", model, profile)
                with self.assertRaisesRegex(RuntimeError, reason):
                    rig.submit()
                self.assertEqual(rig.inverter.calls, [])
                self.assertFalse(rig.path.exists())
                state = adapter.build_inverter_control_capability_state(rig.config)
                control = state["capabilities"]["battery_charge_control"]
                self.assertEqual(control["unsupported_reason"], reason)
                self.assertFalse(control["available"])
                self.assertTrue(set(control) <= {"available", "supported_actions",
                    "discharge_allowed", "unsupported_reason", "verification_status"})

    def test_failed_canary_does_not_rearm_a_safety_terminated_manual_request(self):
        rig = self.fixture()
        old_payload = rig.payload()
        rig.submit()
        rig.inverter.start_error = BatteryControlSafetyStop("minimum_soc_reached", 42)
        rig.runtime.tick()
        rig.inverter.start_error = None
        before = set(json.loads(rig.path.read_text())["blocked_manual_signatures"])
        self.assertTrue(before)
        result = rig.runtime.canary(callback=lambda **kwargs: {
            "status": "failed", "verified": False, "profile_id": "foxess_h3_10_0_e",
            "error": {"code": "preflight_failed"},
        }, cloud_config=rig.config, command_id="failed-canary", expected_runtime_fingerprint="feature-1")
        self.assertEqual(result["status"], "failed")
        after = set(json.loads(rig.path.read_text())["blocked_manual_signatures"])
        self.assertTrue(before <= after)
        with self.assertRaisesRegex(RuntimeError, "already ended locally"):
            rig.runtime.submit(payload=old_payload, command_id="delayed-old-request",
                               cloud_config=rig.config)
        self.assertEqual(rig.inverter.calls[-1]["action"], "auto")

    def test_incomplete_or_contradictory_canary_report_never_creates_a_pin(self):
        malformed = [
            {"status": "passed", "verified": False, "profile_id": "foxess_h3_10_0_e", "phases": []},
            {**self.passed_canary(), "profile_id": "solax_x3_hybrid_g4"},
            {**self.passed_canary(), "phases": [{"action": "charge_grid", "status": "passed"}]},
            {**self.passed_canary(), "final_auto": {"verified": False}},
            {**self.passed_canary(), "verification_scope": "different_test"},
        ]
        for case_number, report in enumerate(malformed):
            with self.subTest(case_number=case_number):
                rig = self.fixture()
                with patch.object(rig.runtime, "_pin_canary") as pin:
                    result = rig.runtime.canary(callback=lambda **kwargs: report,
                        cloud_config=rig.config, command_id="incomplete-canary", expected_runtime_fingerprint="feature-1")
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result.get("verified", False))
                pin.assert_not_called()
                self.assertEqual(rig.runtime.status()["state"], "stopped")

    def test_failed_final_stop_cannot_leave_verified_true_or_create_a_pin(self):
        rig = self.fixture()
        rig.inverter.stop_error = RuntimeError("STOP rejected")
        with patch.object(rig.runtime, "_pin_canary") as pin:
            result = rig.runtime.canary(callback=lambda **kwargs: self.passed_canary(),
                cloud_config=rig.config, command_id="unverified-stop-canary", expected_runtime_fingerprint="feature-1")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result.get("verified", False))
        self.assertFalse(result["local_final_auto_verified"])
        self.assertEqual(rig.runtime.status()["state"], "stop_pending")
        pin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
