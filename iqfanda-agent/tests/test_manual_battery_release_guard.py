"""Synthetic release/evidence fixtures only; these tests certify no real hardware."""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_manual_battery_release.py"
spec = importlib.util.spec_from_file_location("manual_battery_release_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(guard)


class ReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for relative in guard.MINIMUM_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic test fixture\n", encoding="utf-8")
        (self.root / (guard.APP + "VERSION")).write_text("0.1.168\n")
        (self.root / "iqfanda-agent/config.yaml").write_text(
            'version: "0.1.168"\noptions:\n  software_version: "0.1.168"\n')
        for profile in guard.PROFILE_BRANDS:
            value = {"profile_id": profile}
            if profile in ("goodwe_et", "foxess_h3_10_0_e"):
                prefix = "goodwe" if profile == "goodwe_et" else "foxess"
                value["control_capabilities"] = {"battery_charge_control": {
                    "schema_version": 1, "discharge_allowed": True,
                    "supported_actions": ["auto", "charge_grid", "discharge_grid"],
                    "driver_module": "communication." + prefix + "_ems_controller",
                    "driver_function": "execute_action",
                }}
            self.write_profile(profile, value)
        self.evidence = {"schema_version": 1, "records": [], "revocations": []}
        self.contract = guard.refresh(self.root, "Synthetic reviewed baseline for unit tests only")

    def write_profile(self, profile, value):
        (self.root / (guard.PROFILE_ROOT + profile + ".json")).write_text(json.dumps(value))

    def physical_fixture(self):
        # Never written into the real repository's physical evidence registry.
        return {
            "id": "SYNTHETIC-TEST-NOT-PHYSICAL-EVIDENCE",
            "profile_id": "goodwe_et", "status": "physically_verified",
            "source_fingerprint": self.contract["source_fingerprint"],
            "runtime_fingerprint": self.contract["runtime_fingerprint"],
            "agent_version": "0.1.168", "verification_origin": "physical_installation",
            "device_serial_number": "SYNTHETIC", "inverter_model": "fixture",
            "inverter_firmware": "fixture", "battery_model": "fixture",
            "verified_at": "2020-01-01T00:00:00+00:00", "reviewed_by": "fixture reviewer",
            "report_reference": "synthetic-test-fixture", "report_sha256": "a" * 64,
            "checks": {key: {"passed": True, "report_section": "synthetic section"}
                       for key in guard.PHYSICAL_CHECKS},
        }

    def test_empty_evidence_stays_pending_for_every_requested_brand(self):
        status = guard.check(self.root, self.contract, self.evidence)
        self.assertEqual({entry["brand"] for entry in status.values()}, {"GoodWe", "FoxESS", "SolaX", "Deye"})
        self.assertTrue(all(entry["physical_status"] == "pending" for entry in status.values()))
        self.assertFalse(status["solax_x3_hybrid_g4"]["control_available"])

    def test_changed_source_fails_until_reviewed_refresh(self):
        path = self.root / (guard.APP + "manual_battery_runtime.py")
        path.write_text("reviewed newer implementation\n")
        with self.assertRaisesRegex(guard.ContractError, "drift"):
            guard.check(self.root, self.contract, self.evidence)
        updated = guard.refresh(self.root, "Reviewed fix for restart ownership with regression coverage")
        status = guard.check(self.root, updated, self.evidence, self.contract)
        self.assertEqual(len(updated["change_history"]), 2)
        self.assertEqual(status["goodwe_et"]["physical_status"], "pending")

    def test_refresh_does_not_duplicate_a_source_revision(self):
        updated = guard.refresh(self.root, "Repeated manifest generation does not constitute a new release")
        self.assertEqual(updated, self.contract)

    def test_removing_startup_protection_fails(self):
        altered = copy.deepcopy(self.contract)
        del altered["files"][guard.APP + "startup.py"]
        with self.assertRaisesRegex(guard.ContractError, "mandatory"):
            guard.check(self.root, altered, self.evidence)

    def test_declared_driver_file_must_exist(self):
        path = self.root / (guard.PROFILE_ROOT + "goodwe_et.json")
        profile = json.loads(path.read_text())
        profile["control_capabilities"]["battery_charge_control"]["driver_module"] = "communication.missing_driver"
        path.write_text(json.dumps(profile))
        with self.assertRaisesRegex(guard.ContractError, "driver file"):
            guard.refresh(self.root, "Driver declaration changed but implementation was removed")

    def test_explicitly_unsupported_deye_profile_is_honest_and_needs_no_writer(self):
        self.write_profile("deye_3x_lv_off_grid", {"control_capabilities": {"battery_charge_control": {
            "supported_actions": [], "discharge_allowed": False,
            "unsupported_reason": "The exact physical write protocol is not verified.",
        }}})
        updated = guard.refresh(self.root, "Record an explicit unsupported Deye control protocol without guessing writes")
        status = guard.check(self.root, updated, self.evidence, self.contract)
        self.assertFalse(status["deye_3x_lv_off_grid"]["control_available"])
        self.assertEqual(status["deye_3x_lv_off_grid"]["physical_status"], "pending")
        self.assertTrue(status["foxess_h3_10_0_e"]["control_available"])

    def test_existing_control_capability_cannot_disappear_on_refresh(self):
        self.write_profile("goodwe_et", {"profile_id": "goodwe_et"})
        with self.assertRaisesRegex(guard.ContractError, "capability was removed"):
            guard.refresh(self.root, "Unintended profile replacement should be rejected by review gate")

    def test_auto_and_both_directions_are_required_together(self):
        path = self.root / (guard.PROFILE_ROOT + "foxess_h3_10_0_e.json")
        profile = json.loads(path.read_text())
        profile["control_capabilities"]["battery_charge_control"]["supported_actions"] = ["charge_grid", "discharge_grid"]
        path.write_text(json.dumps(profile))
        with self.assertRaisesRegex(guard.ContractError, "AUTO"):
            guard.refresh(self.root, "Removing STOP AUTO must not silently pass the source review gate")

    def test_exact_complete_evidence_applies_only_to_its_profile(self):
        self.evidence["records"].append(self.physical_fixture())
        status = guard.check(self.root, self.contract, self.evidence)
        self.assertEqual(status["goodwe_et"]["physical_status"], "physically_verified")
        self.assertEqual(status["foxess_h3_10_0_e"]["physical_status"], "pending")
        self.assertEqual(status["goodwe_et_15_30kw"]["physical_status"], "pending")

    def test_old_bytes_never_certify_a_new_source_revision(self):
        self.evidence["records"].append(self.physical_fixture())
        (self.root / (guard.APP + "heartbeat.py")).write_text("changed monitored integration\n")
        updated = guard.refresh(self.root, "Reviewed heartbeat integration change with a new fingerprint")
        status = guard.check(self.root, updated, self.evidence, self.contract)
        self.assertEqual(status["goodwe_et"]["physical_status"], "pending")
        self.assertEqual(len(self.evidence["records"]), 1)

    def test_test_only_change_keeps_physical_runtime_evidence(self):
        self.evidence["records"].append(self.physical_fixture())
        (self.root / "iqfanda-agent/tests/test_manual_battery_runtime.py").write_text("extra offline coverage\n")
        updated = guard.refresh(self.root, "Added regression coverage without changing production bytes")
        status = guard.check(self.root, updated, self.evidence, self.contract)
        self.assertNotEqual(updated["source_fingerprint"], self.contract["source_fingerprint"])
        self.assertEqual(updated["runtime_fingerprint"], self.contract["runtime_fingerprint"])
        self.assertEqual(status["goodwe_et"]["physical_status"], "physically_verified")

    def test_build_change_requires_new_physical_runtime_evidence(self):
        self.evidence["records"].append(self.physical_fixture())
        (self.root / "iqfanda-agent/Dockerfile").write_text("new dependency version\n")
        updated = guard.refresh(self.root, "Changed runtime dependencies and rebuilt the candidate image")
        status = guard.check(self.root, updated, self.evidence, self.contract)
        self.assertNotEqual(updated["runtime_fingerprint"], self.contract["runtime_fingerprint"])
        self.assertEqual(status["goodwe_et"]["physical_status"], "pending")

    def test_entrypoint_is_part_of_the_production_fingerprint(self):
        (self.root / (guard.ROOTFS + "etc/services.d/iqfanda/run")).write_text("reviewed startup change\n")
        updated = guard.refresh(self.root, "Reviewed service entrypoint change needs runtime revalidation")
        self.assertNotEqual(updated["runtime_fingerprint"], self.contract["runtime_fingerprint"])

    def test_derived_production_manifest_cannot_be_silently_changed(self):
        path = self.root / guard.PRODUCTION_MANIFEST_PATH
        manifest = json.loads(path.read_text())
        manifest["runtime_fingerprint"] = "0" * 64
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(guard.ContractError, "Production manifest drift"):
            guard.check(self.root, self.contract, self.evidence)

    def test_source_or_mock_tests_cannot_be_used_as_physical_evidence(self):
        record = self.physical_fixture()
        record["verification_origin"] = "unit_test"
        self.evidence["records"].append(record)
        with self.assertRaisesRegex(guard.ContractError, "cannot certify"):
            guard.check(self.root, self.contract, self.evidence)

    def test_charge_only_report_is_incomplete(self):
        record = self.physical_fixture()
        del record["checks"]["discharge_measured"]
        self.evidence["records"].append(record)
        with self.assertRaisesRegex(guard.ContractError, "incomplete"):
            guard.check(self.root, self.contract, self.evidence)

    def test_failed_stop_never_marks_verified(self):
        record = self.physical_fixture()
        record["checks"]["manual_stop_confirmed"]["passed"] = False
        self.evidence["records"].append(record)
        with self.assertRaisesRegex(guard.ContractError, "manual_stop"):
            guard.check(self.root, self.contract, self.evidence)

    def test_revoked_evidence_returns_pending_without_deleting_history(self):
        record = self.physical_fixture()
        self.evidence["records"].append(record)
        self.evidence["revocations"].append({"evidence_id": record["id"], "reason": "Later field regression"})
        self.assertEqual(guard.check(self.root, self.contract, self.evidence)["goodwe_et"]["physical_status"], "pending")
        self.assertEqual(len(self.evidence["records"]), 1)

    def test_evidence_history_cannot_be_rewritten(self):
        original = copy.deepcopy(self.evidence)
        original["records"].append(self.physical_fixture())
        with self.assertRaisesRegex(guard.ContractError, "append-only"):
            guard.check(self.root, self.contract, self.evidence, base_evidence=original)

    def test_review_history_cannot_be_erased(self):
        (self.root / (guard.APP + "manual_battery_runtime.py")).write_text("revision two\n")
        updated = guard.refresh(self.root, "Legitimate controlled change with mandatory test coverage")
        updated["change_history"][0]["reason"] = "Trying to rewrite the original review record"
        with self.assertRaisesRegex(guard.ContractError, "must not be edited"):
            guard.check(self.root, updated, self.evidence, self.contract)

    def test_paths_cannot_escape_repository(self):
        with self.assertRaises(guard.ContractError):
            guard.safe_path(self.root, "../outside.py")
        with self.assertRaises(guard.ContractError):
            guard.safe_path(self.root, "/absolute.py")

    def test_metadata_versions_must_agree(self):
        (self.root / (guard.APP + "VERSION")).write_text("0.1.169\n")
        with self.assertRaisesRegex(guard.ContractError, "differs"):
            guard.refresh(self.root, "Version was incompletely changed and must fail validation")


if __name__ == "__main__":
    unittest.main()
