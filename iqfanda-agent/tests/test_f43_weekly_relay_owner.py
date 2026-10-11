"""F43 SOC/weekly ownership: offline, no Home Assistant or relay."""
import unittest
from weekly_relay_owner import is_verified_weekly_owner

ID = "18e55901-486e-4e2f-a580-648c2fbc4014"
SW = "switch.sonoff_zbminir2"

def fixture():
    return {"module_runtime_configurations": [{
        "module_key": "pv_surplus_control",
        "configuration": {
            "weekly_temperature_programs": {ID: {"enabled": True}},
            "targets": [
                {"id": ID, "type": "generic_load", "enabled": True,
                 "configuration_status": "verified",
                 "output": {"status": "verified", "reference": SW},
                 "weekly_control_output": {
                     "status": "verified", "role": "thermostat_permission",
                     "reference": SW, "device_reg_id": "registry00014"}},
                {"id": "boiler", "type": "domestic_hot_water",
                 "enabled": True, "configuration_status": "verified",
                 "output": {"status": "verified", "reference": "switch.boiler"}}
            ]
        }
    }]}

class F43OwnershipTests(unittest.TestCase):
    def is_owner(self, config):
        return is_verified_weekly_owner(config, target_id=ID, output_reference=SW)

    def test_active_verified_weekly_claims_output(self):
        self.assertTrue(self.is_owner(fixture()))

    def test_soc_low_does_not_disable_normal_weekly(self):
        f = fixture()
        f["module_runtime_configurations"][0]["configuration"]["surplus_source"] = {
            "type": "battery_soc",
            "battery_soc": {"enable_soc_percent": 95, "disable_soc_percent": 85}}
        self.assertTrue(self.is_owner(f))

    def test_inactive_program_retains_legacy(self):
        f = fixture()
        f["module_runtime_configurations"][0]["configuration"]["weekly_temperature_programs"][ID]["enabled"] = False
        self.assertFalse(self.is_owner(f))

    def test_missing_profile_retains_legacy(self):
        f = fixture()
        f["module_runtime_configurations"][0]["configuration"].pop("weekly_temperature_programs")
        self.assertFalse(self.is_owner(f))

    def test_missing_or_unverified_role_retains_legacy(self):
        for key, value in (("status", "pending"), ("role", "direct_heating_load"),
                           ("device_reg_id", ""), ("reference", "switch.other")):
            f = fixture()
            f["module_runtime_configurations"][0]["configuration"]["targets"][0]["weekly_control_output"][key] = value
            self.assertFalse(self.is_owner(f))

    def test_wrong_target_or_relay_not_claimed(self):
        self.assertFalse(is_verified_weekly_owner(
            fixture(), target_id="boiler", output_reference="switch.boiler"))
        self.assertFalse(is_verified_weekly_owner(
            fixture(), target_id=ID, output_reference="switch.other"))

    def test_shared_relay_rejected(self):
        f = fixture()
        f["module_runtime_configurations"][0]["configuration"]["targets"][1]["output"]["reference"] = SW
        self.assertFalse(self.is_owner(f))

    def test_disabled_or_unverified_target_retains_legacy(self):
        for key,value in (("enabled", False),("configuration_status","pending")):
            f = fixture()
            f["module_runtime_configurations"][0]["configuration"]["targets"][0][key] = value
            self.assertFalse(self.is_owner(f))

    def test_empty_config_not_claimed(self):
        self.assertFalse(self.is_owner({}))

if __name__ == "__main__":
    unittest.main()
