"""Read-only Supervisor preflight behavior using local synthetic responses."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/read_addon_update_preflight.py"
spec = importlib.util.spec_from_file_location("manual_battery_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class AddonUpdatePreflightTests(unittest.TestCase):
    def test_reads_only_self_and_source_without_returning_options_or_credentials(self):
        calls = []
        responses = {
            "/addons/self/info": {"slug": "abc_tng_iq_fanda_agent", "repository": "abc", "version": "0.1.167",
                                  "version_latest": "0.1.168", "auto_update": False, "build": True,
                                  "options": {"secret": "DO-NOT-RETURN"}},
            "/store/repositories/abc": {"source": "https://user:DO-NOT-RETURN@github.com/fandabodis-hash/homeassistant-apps#candidate"},
        }
        def get(path):
            calls.append(path)
            return responses[path]
        with tempfile.TemporaryDirectory() as root:
            result = preflight.collect_preflight(get, config_root=Path(root), app_root=Path(root))
        self.assertEqual(calls, list(responses))
        self.assertTrue(result["read_only"])
        self.assertFalse(result["update_requested"])
        self.assertFalse(result["fleet_auto_update_known"])
        self.assertNotIn("DO-NOT-RETURN", json.dumps(result))
        self.assertEqual(result["repository"]["configured_branch"], "candidate")
        self.assertTrue(result["repository"]["matches_expected_repository"])
        self.assertIsNone(result["active_or_pending_stop"])

    def test_active_local_owner_and_pin_are_whitelisted(self):
        def get(path):
            return {"repository": "abc"} if path.endswith("/info") else {"source": "https://github.com/fandabodis-hash/homeassistant-apps"}
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "manual_battery_runtime.json").write_text(json.dumps({"schema_version": 1, "state": "stop_pending", "manual": True,
                                                                           "intent": {"token": "DO-NOT-RETURN"}}))
            (root / "manual_battery_verified_fingerprint.json").write_text(json.dumps({"schema_version": 1, "profiles": {
                "foxess_h3_10_0_e": {"status": "physically_verified", "runtime_fingerprint": "a" * 64,
                                     "report_reference": "DO-NOT-RETURN"},
            }}))
            result = preflight.collect_preflight(get, config_root=root, app_root=root)
        self.assertTrue(result["active_or_pending_stop"])
        self.assertEqual(result["physical_pins"]["foxess_h3_10_0_e"]["status"], "physically_verified")
        self.assertNotIn("DO-NOT-RETURN", json.dumps(result))

    def test_repository_mismatch_is_visible(self):
        result = preflight.repository_summary({"source": "https://example.invalid/private-repo?access_token=DO-NOT-RETURN"})
        self.assertFalse(result["matches_expected_repository"])
        self.assertTrue(result["source_credentials_present"])
        self.assertNotIn("DO-NOT-RETURN", json.dumps(result))

    def test_unknown_runtime_schema_or_state_is_never_reported_as_idle(self):
        def get(path):
            return {"repository": "abc"} if path.endswith("/info") else {"source": "https://github.com/fandabodis-hash/homeassistant-apps"}
        for state in ({"schema_version": 99, "state": "stopped"},
                      {"schema_version": 1, "state": "unknown"}, {}):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as root:
                root = Path(root)
                (root / "manual_battery_runtime.json").write_text(json.dumps(state))
                result = preflight.collect_preflight(get, config_root=root, app_root=root)
                self.assertIsNone(result["active_or_pending_stop"])
                self.assertTrue(result["battery_state_read_error"])


if __name__ == "__main__":
    unittest.main()
