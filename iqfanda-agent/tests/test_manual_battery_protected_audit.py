"""Offline tests of the audit boundary; no app imports, database, or commands."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "f53_protected_config_audit.py"
_SPEC = importlib.util.spec_from_file_location("f53_protected_config_audit", _PATH)
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


class ProtectedConfigAuditTests(unittest.TestCase):
    KEY = b"offline-test-key-do-not-use-in-a-live-report"

    def snapshot(self, config=None, modules=None, key=None):
        return audit.make_snapshot({} if config is None else config,
                                   [] if modules is None else modules,
                                   digest_key=self.KEY if key is None else key)

    def module(self, module_key, configuration):
        return {"module": module_key, "status": "verified", "configuration": configuration,
                "manufacturer": "test", "model": "test"}

    def compare_module(self, name, before_config, after_config):
        return audit.diff_snapshots(self.snapshot(modules=[self.module(name, before_config)]),
                                    self.snapshot(modules=[self.module(name, after_config)]))

    def changed(self, diff, suffix):
        return next(row for row in diff["changed_paths"] if row["path"].endswith(suffix))

    def test_legacy_hash_matches_exact_r13_fields_serialization_and_input_order(self):
        config = {"remote_commands_enabled": True, "cloud_enabled": True,
                  "settings": {"value": "žluťoučký", "mixed": [1, None, False]},
                  "active_modules": ["photovoltaic", "building"], "config_version": 7,
                  "timezone": "Europe/Prague"}
        modules = [self.module("photovoltaic", {"x": 1}), self.module("building", {})]
        legacy = {"config": {k: config.get(k) for k in (
            "remote_commands_enabled", "cloud_enabled", "settings", "active_modules", "config_version")},
            "modules": [{k: m.get(k) for k in ("module", "status", "configuration")} for m in modules]}
        expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, default=str).encode()).hexdigest()
        self.assertEqual(self.snapshot(config, modules)["configuration_sha256"], expected)

    def test_extended_config_and_module_columns_are_audited_outside_legacy(self):
        config = {"timezone": "Europe/Prague", "heartbeat_interval_seconds": 60}
        module = self.module("photovoltaic", {})
        before = self.snapshot(config, [module])
        config["heartbeat_interval_seconds"] = 120
        module["model"] = "other model"
        diff = audit.diff_snapshots(before, self.snapshot(config, [module]))
        self.assertTrue(diff["legacy_unchanged"])
        self.assertFalse(diff["unchanged"])
        self.assertEqual(diff["changed_counts"], {"configuration": 2, "control_state": 0, "observation": 0})

    def test_dhw_loop_timestamp_and_temperature_are_observations_only(self):
        config = {"settings": {"heat_pump": {"dhw_automation_v1": {"enabled": True,
            "runtime": {"last_loop_at": "2026-11-01T12:00:00+00:00", "current_temp_c": 42.1}}}}}
        after = copy.deepcopy(config)
        after["settings"]["heat_pump"]["dhw_automation_v1"]["runtime"].update(
            last_loop_at="2026-11-01T12:00:03+00:00", current_temp_c=42.2)
        diff = audit.diff_snapshots(self.snapshot(config), self.snapshot(after))
        self.assertEqual(diff["changed_counts"], {"configuration": 0, "control_state": 0, "observation": 2})
        self.assertFalse(diff["legacy_unchanged"])
        self.assertEqual(diff["r13_original_mismatch"], "unresolved")

    def test_manual_intent_and_restore_pending_are_control_not_observation(self):
        before = {"heating_setpoint_entities_v1": {"summer_winter": {"runtime": {
            "manual_intent_enabled": True, "release_restore_pending": False}}}}
        after = copy.deepcopy(before)
        after["heating_setpoint_entities_v1"]["summer_winter"]["runtime"].update(
            manual_intent_enabled=False, release_restore_pending=True)
        diff = self.compare_module("individual_project", before, after)
        self.assertEqual(diff["changed_counts"], {"configuration": 0, "control_state": 2, "observation": 0})
        self.assertEqual(self.changed(diff, "/manual_intent_enabled")["category"], "control_state")

    def test_climate_values_and_timestamps_are_observations_but_identity_is_configuration(self):
        before = {"indoor_climate": {"temperature_entity": {
            "entity_id": "sensor.original", "state": "20.1", "last_updated": "2026-11-01T12:00:00Z"}}}
        after = copy.deepcopy(before)
        after["indoor_climate"]["temperature_entity"].update(
            entity_id="sensor.replacement", state="20.2", last_updated="2026-11-01T12:01:00Z")
        diff = self.compare_module("building", before, after)
        self.assertEqual(diff["changed_counts"], {"configuration": 1, "control_state": 0, "observation": 2})
        self.assertEqual(self.changed(diff, "/entity_id")["category"], "configuration")

    def test_generic_runtime_state_or_timestamp_names_are_not_whitelisted(self):
        before = {"settings": {"runtime": {"state": "one", "last_updated": "old"},
                               "heat_pump": {"dhw_automation_v1": {"runtime": {"new_field": 1}}}}}
        after = copy.deepcopy(before)
        after["settings"]["runtime"].update(state="two", last_updated="new")
        after["settings"]["heat_pump"]["dhw_automation_v1"]["runtime"]["new_field"] = 2
        diff = audit.diff_snapshots(self.snapshot(before), self.snapshot(after))
        self.assertEqual(diff["changed_counts"], {"configuration": 3, "control_state": 0, "observation": 0})

    def test_automatic_disable_still_is_configuration(self):
        before = {"dhw_thermostat_v1": {"enabled": True, "runtime": {"fault": None}}}
        after = {"dhw_thermostat_v1": {"enabled": False, "runtime": {"fault": "command_failed"}}}
        diff = self.compare_module("individual_project", before, after)
        self.assertEqual(self.changed(diff, "/enabled")["category"], "configuration")
        self.assertEqual(self.changed(diff, "/fault")["category"], "control_state")

    def test_precise_control_entity_list_is_atomic_and_never_exports_entity_ids(self):
        before = {"dhw_thermostat_v1": {"runtime": {"requested_entities": ["switch.private_a"]}}}
        after = {"dhw_thermostat_v1": {"runtime": {"requested_entities": ["switch.private_b", "switch.private_c"]}}}
        diff = self.compare_module("individual_project", before, after)
        self.assertEqual(diff["changed_counts"], {"configuration": 0, "control_state": 1, "observation": 0})
        self.assertNotIn("switch.private", json.dumps(diff))

    def test_new_structure_at_observation_leaf_is_configuration(self):
        before = {"indoor_climate": {"humidity_entity": {"state": "45"}}}
        after = {"indoor_climate": {"humidity_entity": {"state": {"enabled": True}}}}
        diff = self.compare_module("building", before, after)
        self.assertEqual(self.changed(diff, "/humidity_entity/state")["category"], "configuration")
        self.assertTrue(diff["configuration_changed"])

    def test_unexpected_timestamp_type_is_not_dismissed_as_an_observation(self):
        before = {"settings": {"heat_pump": {"dhw_automation_v1": {"runtime": {"last_loop_at": "old"}}}}}
        after = copy.deepcopy(before)
        after["settings"]["heat_pump"]["dhw_automation_v1"]["runtime"]["last_loop_at"] = True
        diff = audit.diff_snapshots(self.snapshot(before), self.snapshot(after))
        self.assertEqual(diff["changed_counts"], {"configuration": 1, "control_state": 0, "observation": 0})

    def test_absent_null_empty_object_and_empty_array_are_all_distinct(self):
        snapshots = [self.snapshot(config) for config in ({}, {"settings": None}, {"settings": {}}, {"settings": []})]
        kinds = []
        for snapshot in snapshots:
            kinds.append(snapshot["_field_map"]["entries"]["/config/settings"]["kind"])
        self.assertEqual(kinds, ["missing", "null", "object", "array"])
        self.assertEqual(snapshots[0]["configuration_sha256"], snapshots[1]["configuration_sha256"])
        for before, after in zip(snapshots, snapshots[1:]):
            self.assertTrue(audit.diff_snapshots(before, after)["configuration_changed"])

    def test_added_removed_unknown_fields_are_detected_without_exposing_their_names(self):
        before = self.snapshot({"settings": {"private-old-name": None}})
        after = self.snapshot({"settings": {"private-new-name": []}})
        diff = audit.diff_snapshots(before, after)
        self.assertEqual({r["change"] for r in diff["changed_paths"]}, {"added", "removed"})
        self.assertEqual(diff["changed_counts"]["configuration"], 2)
        self.assertNotIn("private-", json.dumps(diff))

    def test_secrets_dynamic_keys_module_names_and_digest_key_are_never_exported(self):
        config = {"settings": {"private-password-label": "password-value-never-export",
                               "zigbee_device_names": {"private-person-or-ieee": "private-display-name"}}}
        modules = [self.module("private-module-title", {"secret-key-label": "secret-value"})]
        before = self.snapshot(config, modules)
        config["settings"]["private-password-label"] = "changed-password-never-export"
        after = self.snapshot(config, modules)
        exported = json.dumps([before, after, audit.diff_snapshots(before, after)])
        for private in ("private-password-label", "password-value-never-export", "private-person-or-ieee",
                        "private-display-name", "private-module-title", "secret-key-label", "secret-value",
                        "changed-password-never-export", self.KEY.decode()):
            self.assertNotIn(private, exported)

    def test_identical_input_and_object_key_reordering_produce_identical_snapshots(self):
        config = {"settings": {"b": [1, 2], "a": {"y": True, "x": None}}}
        reordered = {"settings": {"a": {"x": None, "y": True}, "b": [1, 2]}}
        before, after = self.snapshot(config), self.snapshot(reordered)
        self.assertEqual(before, after)
        diff = audit.diff_snapshots(before, after)
        self.assertTrue(diff["unchanged"])
        self.assertEqual(diff["changed_paths"], [])
        self.assertEqual(diff["r13_original_mismatch"], "unresolved")

    def test_array_and_module_order_are_preserved(self):
        before = self.snapshot({"active_modules": ["a", "b"]}, [self.module("building", {}), self.module("heat_pump", {})])
        after = self.snapshot({"active_modules": ["b", "a"]}, [self.module("heat_pump", {}), self.module("building", {})])
        diff = audit.diff_snapshots(before, after)
        self.assertEqual(diff["changed_counts"]["configuration"], 3)
        self.assertFalse(diff["legacy_unchanged"])
        self.assertEqual(self.changed(diff, "/modules")["after"]["kind"], "module_order")

    def test_diff_counts_all_changes_even_when_public_detail_is_limited(self):
        before = self.snapshot({"settings": {f"key-{i}": False for i in range(200)}})
        after = self.snapshot({"settings": {f"key-{i}": True for i in range(200)}})
        diff = audit.diff_snapshots(before, after)
        self.assertEqual(diff["changed_count"], 200)
        self.assertEqual(diff["changed_counts"]["configuration"], 200)
        self.assertEqual(len(diff["changed_paths"]), audit.MAX_CHANGED_PATHS)
        self.assertTrue(diff["changed_paths_truncated"])
        self.assertNotIn("_field_map", diff)

    def test_known_control_changes_are_not_hidden_behind_many_observations(self):
        before = {"heating_setpoint_entities_v1": {"summer_winter": {"runtime": {
            "manual_intent_enabled": False, "last_weather_temperature_c": 2.0}}}}
        after = copy.deepcopy(before)
        after["heating_setpoint_entities_v1"]["summer_winter"]["runtime"].update(
            manual_intent_enabled=True, last_weather_temperature_c=3.0)
        diff = self.compare_module("individual_project", before, after)
        self.assertEqual([x["category"] for x in diff["changed_paths"]], ["control_state", "observation"])

    def test_different_digest_keys_cannot_be_compared(self):
        before = self.snapshot({"settings": {"private": "value"}})
        after = self.snapshot({"settings": {"private": "value"}}, key=b"different-offline-key-for-comparison")
        self.assertEqual(before["configuration_sha256"], after["configuration_sha256"])
        with self.assertRaisesRegex(ValueError, "different digest keys"):
            audit.diff_snapshots(before, after)

    def test_malformed_schema_or_missing_comparison_map_fails_closed(self):
        before = self.snapshot()
        after = copy.deepcopy(before)
        after["schema_version"] = 999
        with self.assertRaises(ValueError):
            audit.diff_snapshots(before, after)
        with self.assertRaises(ValueError):
            audit.diff_snapshots(before, {k: v for k, v in before.items() if k != "_field_map"})

    def test_duplicate_identifiers_and_non_json_inputs_reject_without_private_details(self):
        duplicate = self.module("private-duplicate-name", {})
        with self.assertRaisesRegex(ValueError, "duplicate module identifiers") as error:
            self.snapshot(modules=[duplicate, duplicate])
        self.assertNotIn("private-duplicate-name", str(error.exception))
        for value in (float("nan"), float("inf"), object(), {1: "private-value"}):
            with self.subTest(type=type(value)):
                with self.assertRaises(ValueError):
                    self.snapshot({"settings": value})
        cycle = {}
        cycle["private-cycle-name"] = cycle
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.snapshot(cycle)

    def test_caller_input_is_not_mutated_and_snapshot_does_not_retain_raw_references(self):
        config = {"settings": {"heat_pump": {"dhw_automation_v1": {"runtime": {"last_loop_at": "old"}}}}}
        modules = [self.module("building", {"indoor_climate": {"temperature_entity": {"state": "21"}}})]
        original = copy.deepcopy((config, modules))
        snapshot = self.snapshot(config, modules)
        frozen = copy.deepcopy(snapshot)
        self.assertEqual((config, modules), original)
        config["settings"].clear()
        modules[0]["configuration"].clear()
        self.assertEqual(snapshot, frozen)


if __name__ == "__main__":
    unittest.main()
