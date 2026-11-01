"""Deye's unsupported cases must stay explicit and must never call a writer."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(APP))

from communication.inverter_adapter import select_inverter_profile
from communication import inverter_control_adapter as adapter


class DeyeControlSupportTests(unittest.TestCase):
    MODELS = (
        ("3xDEYE LV off grid", "off_grid_fixed_battery_power_unavailable"),
        ("Deye 50 hybrid HV", "deye_hv_control_protocol_unverified"),
    )

    def config(self, model):
        return {"module_runtime_configurations": [
            {"module_key": "photovoltaic", "manufacturer": "deye", "model": model}
        ]}

    def test_each_profile_has_a_specific_reason_and_no_driver(self):
        for model, reason in self.MODELS:
            with self.subTest(model=model):
                profile = select_inverter_profile(manufacturer="deye", model=model)
                control = profile["control_capabilities"]["battery_charge_control"]
                self.assertEqual(control["supported_actions"], [])
                self.assertFalse(control["discharge_allowed"])
                self.assertEqual(control["unsupported_reason"], reason)
                self.assertNotIn("driver_module", control)
                self.assertNotIn("driver_function", control)

    def test_public_support_state_does_not_claim_availability(self):
        for model, _reason in self.MODELS:
            with self.subTest(model=model):
                state = adapter.build_inverter_control_capability_state(self.config(model))
                self.assertTrue(state["complete"])
                control = state["capabilities"]["battery_charge_control"]
                self.assertFalse(control["available"])
                self.assertFalse(control["discharge_allowed"])
                self.assertEqual(control["supported_actions"], [])
                self.assertNotIn("driver_module", control)
                self.assertNotIn("protocol", control)
                self.assertNotIn("firmware", control)

    def test_all_actions_are_rejected_before_importing_or_calling_a_driver(self):
        for model, _reason in self.MODELS:
            for action in ("charge_grid", "discharge_grid", "auto"):
                with self.subTest(model=model, action=action):
                    with patch.object(adapter.importlib, "import_module") as load_driver:
                        with self.assertRaises(adapter.UnsupportedInverterCapabilityError):
                            adapter.execute_battery_control_from_cloud_config(
                                cloud_config=self.config(model), action=action,
                                allowed_charge_power_w=1000, target_soc_percent=80,
                                allowed_discharge_power_w=1000, minimum_discharge_soc_percent=20)
                        load_driver.assert_not_called()

    def test_lv_profile_keeps_the_actual_off_grid_topology(self):
        profile = select_inverter_profile(manufacturer="deye", model=self.MODELS[0][0])
        self.assertFalse(profile["topology"]["grid_connected"])
        self.assertTrue(profile["topology"]["shared_dc_bus"])
        self.assertEqual(len(profile["topology"]["ac_buses"]), 2)
        self.assertEqual(profile["protocol"]["allowed_device_ids"], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
