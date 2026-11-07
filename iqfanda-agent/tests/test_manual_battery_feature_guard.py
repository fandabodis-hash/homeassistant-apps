"""Offline integrity/pin fixtures; no inverter or actual physical evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "rootfs/app/communication/battery_feature_guard.py"
spec = importlib.util.spec_from_file_location("battery_feature_guard_test_module", MODULE)
guard = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(guard)


class LocalBatteryFeatureGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "app"
        self.root.mkdir()
        for relative in guard.REQUIRED_FILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic production fixture\n")
        for relative in guard.REQUIRED_ROOTFS_FILES:
            path = self.root.parent / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic entrypoint fixture\n")
        for profile_id in guard.PROFILE_IDS:
            (self.root / ("communication/inverter_profiles/" + profile_id + ".json")).write_text("{}\n")
        (self.root / "VERSION").write_text("0.1.168\n")
        self.manifest_path = self.root / "protection/manual_battery_manifest.json"
        self.pin_path = Path(temporary.name) / "manual_battery_verified_fingerprint.json"
        for name, value in (("APP_ROOT", self.root), ("MANIFEST_PATH", self.manifest_path), ("PIN_PATH", self.pin_path)):
            patcher = patch.object(guard, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manifest = self.refresh()

    def refresh(self):
        files = {relative: hashlib.sha256((self.root / relative).read_bytes()).hexdigest()
                 for relative in sorted(guard.REQUIRED_FILES)}
        payload = {"agent_version": (self.root / "VERSION").read_text().strip(), "files": files,
                   "rootfs_files": {relative: hashlib.sha256((self.root.parent / relative).read_bytes()).hexdigest()
                                     for relative in sorted(guard.REQUIRED_ROOTFS_FILES)},
                   "build_inputs": {"Dockerfile": "a" * 64, "config.yaml": "b" * 64}}
        manifest = {"schema_version": 1, "feature": "local_manual_battery", **payload,
                    "runtime_fingerprint": hashlib.sha256(guard._canonical(payload)).hexdigest()}
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(manifest))
        return manifest

    def write_pin(self, profile="foxess_h3_10_0_e", *, fingerprint=None, status="physically_verified"):
        self.pin_path.write_text(json.dumps({"schema_version": 1, "profiles": {profile: {
            "status": status, "runtime_fingerprint": fingerprint or self.manifest["runtime_fingerprint"],
            "agent_version": "0.1.168", "evidence_id": "SYNTHETIC-NOT-PHYSICAL",
            "report_sha256": "a" * 64,
        }}}))

    def test_matching_manifest_allows_pending_candidate(self):
        self.assertEqual(guard.verify_battery_feature("foxess_h3_10_0_e"), self.manifest["runtime_fingerprint"])
        self.assertFalse(self.pin_path.exists())

    def write_profile_scoped_pin(self, profile="foxess_h3_10_0_e"):
        self.write_pin(profile=profile)
        registry=json.loads(self.pin_path.read_text())
        registry["profiles"][profile]["control_feature_fingerprint"]=(
            guard.profile_control_fingerprint(profile)
        )
        self.pin_path.write_text(json.dumps(registry))

    def test_new_physical_profile_pin_survives_version_only_update(self):
        self.write_profile_scoped_pin()
        old_runtime=self.manifest["runtime_fingerprint"]
        (self.root / "VERSION").write_text("0.1.169\n")
        updated=self.refresh()
        self.assertNotEqual(old_runtime, updated["runtime_fingerprint"])
        self.assertEqual(guard.verify_battery_feature("foxess_h3_10_0_e"),
                         updated["runtime_fingerprint"])

    def test_old_version_only_pin_still_requires_revalidation(self):
        self.write_pin()
        (self.root / "VERSION").write_text("0.1.169\n")
        self.refresh()
        with self.assertRaisesRegex(RuntimeError, "runtime differs from the physically verified pin"):
            guard.verify_battery_feature("foxess_h3_10_0_e")

    def test_control_source_change_invalidates_physical_profile_pin(self):
        self.write_profile_scoped_pin()
        (self.root / "communication/foxess_ems_controller.py").write_text("changed protected driver")
        self.refresh()
        with self.assertRaisesRegex(RuntimeError, "profile control changed"):
            guard.verify_battery_feature("foxess_h3_10_0_e")

    def test_unrelated_goodwe_driver_change_does_not_invalidate_foxess_pin(self):
        self.write_profile_scoped_pin()
        (self.root / "communication/goodwe_ems_controller.py").write_text("reviewed other profile driver")
        refreshed=self.refresh()
        self.assertEqual(guard.verify_battery_feature("foxess_h3_10_0_e"),
                         refreshed["runtime_fingerprint"])

    def test_foxess_control_change_does_not_revoke_goodwe_physical_pin(self):
        self.write_profile_scoped_pin(profile="goodwe_et")
        (self.root / "communication/foxess_ems_controller.py").write_text("reviewed foxess driver")
        refreshed=self.refresh()
        self.assertEqual(guard.verify_battery_feature("goodwe_et"),
                         refreshed["runtime_fingerprint"])

    def test_changed_source_is_detected(self):
        (self.root / "command_worker.py").write_text("accidental older dispatcher\n")
        with self.assertRaisesRegex(RuntimeError, "hash differs.*command_worker"):
            guard.verify_battery_feature()

    def test_missing_production_file_is_detected(self):
        (self.root / "manual_battery_runtime.py").unlink()
        with self.assertRaisesRegex(RuntimeError, "missing file.*manual_battery_runtime"):
            guard.verify_battery_feature()

    def test_changed_service_entrypoint_cannot_bypass_the_local_loop(self):
        (self.root.parent / "etc/services.d/iqfanda/run").write_text("start only the cloud worker\n")
        with self.assertRaisesRegex(RuntimeError, "hash differs for rootfs"):
            guard.verify_battery_feature()

    def test_manifest_must_keep_required_production_files(self):
        del self.manifest["files"]["startup.py"]
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(RuntimeError, "omits"):
            guard.verify_battery_feature()

    def test_manifest_never_hashes_itself(self):
        self.manifest["files"]["protection/manual_battery_manifest.json"] = "a" * 64
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(RuntimeError, "hash itself"):
            guard.verify_battery_feature()

    def test_runtime_fingerprint_is_recomputed(self):
        self.manifest["runtime_fingerprint"] = "a" * 64
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(RuntimeError, "fingerprint differs"):
            guard.verify_battery_feature()

    def test_production_version_must_match_manifest(self):
        (self.root / "VERSION").write_text("0.1.169\n")
        with self.assertRaisesRegex(RuntimeError, "VERSION differs"):
            guard.verify_battery_feature()

    def test_profile_cannot_point_to_an_unprotected_driver(self):
        profile_path = self.root / "communication/inverter_profiles/foxess_h3_10_0_e.json"
        profile_path.write_text(json.dumps({"control_capabilities": {"battery_charge_control": {
            "driver_module": "communication.unprotected_driver",
        }}}))
        self.refresh()
        with self.assertRaisesRegex(RuntimeError, "driver is not protected"):
            guard.verify_battery_feature()

    def test_explicitly_unsupported_deye_profile_does_not_block_foxess(self):
        profile_path = self.root / "communication/inverter_profiles/deye_3x_lv_off_grid.json"
        profile_path.write_text(json.dumps({"control_capabilities": {"battery_charge_control": {
            "supported_actions": [], "discharge_allowed": False,
            "unsupported_reason": "Physical control protocol has not been verified.",
        }}}))
        updated = self.refresh()
        self.assertEqual(guard.verify_battery_feature("foxess_h3_10_0_e"), updated["runtime_fingerprint"])

    def test_reviewed_manifest_refresh_is_not_a_permanent_code_lock(self):
        (self.root / "manual_battery_runtime.py").write_text("reviewed corrected runtime\n")
        updated = self.refresh()
        self.assertNotEqual(updated["runtime_fingerprint"], self.manifest["runtime_fingerprint"])
        self.assertEqual(guard.verify_battery_feature("foxess_h3_10_0_e"), updated["runtime_fingerprint"])

    def test_matching_selected_profile_pin_allows_same_runtime(self):
        self.write_pin()
        self.assertEqual(guard.verify_battery_feature("foxess_h3_10_0_e"), self.manifest["runtime_fingerprint"])

    def test_drifted_verified_profile_pin_blocks_start(self):
        self.write_pin(fingerprint="0" * 64)
        with self.assertRaisesRegex(RuntimeError, "physically verified pin"):
            guard.verify_battery_feature("foxess_h3_10_0_e")

    def test_foxess_pin_never_certifies_or_blocks_other_profiles(self):
        self.write_pin(fingerprint="0" * 64)
        for profile_id in ("goodwe_et", "solax_x3_hybrid_g4", "deye_3x_lv_off_grid"):
            self.assertEqual(guard.verify_battery_feature(profile_id), self.manifest["runtime_fingerprint"])

    def test_no_profile_checks_integrity_without_claiming_a_pin(self):
        self.write_pin(fingerprint="0" * 64)
        self.assertEqual(guard.verify_battery_feature(), self.manifest["runtime_fingerprint"])

    def test_pending_pin_allows_candidate_without_promoting_it(self):
        self.write_pin(status="pending", fingerprint="0" * 64)
        before = self.pin_path.read_bytes()
        self.assertEqual(guard.verify_battery_feature("foxess_h3_10_0_e"), self.manifest["runtime_fingerprint"])
        self.assertEqual(self.pin_path.read_bytes(), before)

    def test_invalid_selected_pin_fails_closed(self):
        self.pin_path.write_text('{"schema_version":1,"profiles":{"foxess_h3_10_0_e":null}}')
        with self.assertRaisesRegex(RuntimeError, "invalid selected profile pin"):
            guard.verify_battery_feature("foxess_h3_10_0_e")

    def test_unknown_profile_is_not_treated_as_verified(self):
        with self.assertRaisesRegex(RuntimeError, "unknown selected profile"):
            guard.verify_battery_feature("unknown_model")

    def test_guard_never_writes_runtime_or_manifest(self):
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        guard.verify_battery_feature("foxess_h3_10_0_e")
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
