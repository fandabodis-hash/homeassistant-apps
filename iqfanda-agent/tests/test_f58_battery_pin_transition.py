"""F58/F59 0.1.174 – pure no-export and append-only pin transition tests."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from local_modules.battery_pin_transition import (
    PinTransitionSnapshot, archive_previous_verified_pin,
    evaluate_pin_transition,
)

ORIGINAL = PinTransitionSnapshot(
    device_uuid="00007-uuid", expected_device_uuid="00007-uuid",
    installed_agent_version="0.1.174",
    runtime_fingerprint="b"*64, source_integrity_ok=True,
    pin_status="physically_verified", pinned_agent_version="0.1.169",
    pinned_runtime_fingerprint="a"*64, evidence_id="physical-test-00007",
    report_sha256="c"*64, local_runtime_state="stopped",
    local_auto_verified=True, remote_enable=0, remote_power_w=0,
    remote_owner="missing", readback_age_seconds=2.0,
    battery_soc_percent=41.0, bms_safe=True,
    export_forbidden=True, grid_import_w=0.0, grid_meter_age_seconds=1.0,
)


class PinMigrationTests(unittest.TestCase):
    def test_old_pin_requires_new_physical_evidence(self):
        d = evaluate_pin_transition(ORIGINAL)
        self.assertEqual(d.state, "new_physical_verification_required")
        self.assertTrue(d.charge_preflight_possible)
        self.assertFalse(d.discharge_preflight_possible)
        self.assertTrue(d.preserve_previous_pin)
        self.assertTrue(d.requires_physical_revalidation)
        self.assertFalse(d.automatically_arm)
        self.assertFalse(d.automatically_restart)
        self.assertFalse(d.automatically_change_export_limit)

    def test_extra_local_consumption_only_makes_discharge_preflight_possible(self):
        d = evaluate_pin_transition(replace(ORIGINAL,grid_import_w=950))
        self.assertTrue(d.discharge_preflight_possible)
        self.assertTrue(d.requires_physical_revalidation)
        self.assertFalse(d.automatically_arm)

    def test_export_ban_with_stale_meter_never_authorizes_discharge(self):
        self.assertFalse(evaluate_pin_transition(replace(
            ORIGINAL,grid_import_w=2500,grid_meter_age_seconds=30
        )).discharge_preflight_possible)

    def test_unknown_export_policy_is_denied(self):
        self.assertFalse(evaluate_pin_transition(replace(
            ORIGINAL,export_forbidden=None,grid_import_w=4000
        )).discharge_preflight_possible)

    def test_wrong_device_uuid_blocks_everything(self):
        d=evaluate_pin_transition(replace(ORIGINAL,device_uuid="other"))
        self.assertEqual(d.state,"device_mismatch")
        self.assertFalse(d.charge_preflight_possible)

    def test_corrupt_current_runtime_blocks(self):
        d=evaluate_pin_transition(replace(ORIGINAL,source_integrity_ok=False))
        self.assertEqual(d.state,"source_integrity_failed")

    def test_incomplete_prior_pin_blocks(self):
        d=evaluate_pin_transition(replace(ORIGINAL,report_sha256=None))
        self.assertEqual(d.state,"invalid_proof")

    def test_local_pending_stop_blocks(self):
        d=evaluate_pin_transition(replace(ORIGINAL,local_runtime_state="stop_pending"))
        self.assertEqual(d.state,"local_stop_not_verified")

    def test_foreign_owner_blocks(self):
        d=evaluate_pin_transition(replace(ORIGINAL,remote_owner="foreign"))
        self.assertEqual(d.state,"inverter_not_idle")

    def test_active_remote_register_blocks(self):
        d=evaluate_pin_transition(replace(ORIGINAL,remote_enable=1))
        self.assertEqual(d.state,"inverter_not_idle")

    def test_stale_remote_register_blocks(self):
        d=evaluate_pin_transition(replace(ORIGINAL,readback_age_seconds=80))
        self.assertEqual(d.state,"inverter_not_idle")

    def test_bms_or_soc_blocks(self):
        self.assertEqual(evaluate_pin_transition(replace(ORIGINAL,bms_safe=False)).state,
                         "battery_safety_not_verified")
        self.assertEqual(evaluate_pin_transition(replace(ORIGINAL,battery_soc_percent=18)).state,
                         "battery_safety_not_verified")

    def test_same_valid_pin_no_revalidation(self):
        s=replace(ORIGINAL,pinned_agent_version="0.1.174",pinned_runtime_fingerprint="b"*64)
        d=evaluate_pin_transition(s)
        self.assertEqual(d.state,"already_verified")
        self.assertFalse(d.requires_physical_revalidation)

    def _fixtures(self, folder):
        path=Path(folder)/"manual_battery_verified_fingerprint.json"
        data={"schema_version":1,"profiles":{"foxess_h3_10_0_e":{
            "status":"physically_verified","agent_version":"0.1.169",
            "runtime_fingerprint":"a"*64,"evidence_id":"real-ref",
            "report_sha256":"b"*64}}}
        original=json.dumps(data,sort_keys=True).encode()
        path.write_bytes(original)
        return path,original,hashlib.sha256(original).hexdigest()

    def test_append_only_archive_never_rewrites_pin(self):
        with TemporaryDirectory() as temp:
            pin,original,digest=self._fixtures(temp)
            directory=Path(temp)/"history"
            output=archive_previous_verified_pin(pin_path=pin,archive_directory=directory,
                expected_file_sha256=digest,approved=True)
            self.assertEqual(output.read_bytes(),original)
            self.assertEqual(pin.read_bytes(),original)
            self.assertEqual(archive_previous_verified_pin(pin_path=pin,
                archive_directory=directory,expected_file_sha256=digest,approved=True),output)
            self.assertEqual(len(list(directory.iterdir())),1)

    def test_archive_requires_explicit_approval(self):
        with TemporaryDirectory() as temp:
            pin,original,digest=self._fixtures(temp)
            with self.assertRaises(PermissionError):
                archive_previous_verified_pin(pin_path=pin,
                    archive_directory=Path(temp)/"history",
                    expected_file_sha256=digest,approved=False)
            self.assertEqual(pin.read_bytes(),original)

    def test_archive_refuses_drift(self):
        with TemporaryDirectory() as temp:
            pin,original,digest=self._fixtures(temp)
            pin.write_bytes(original+b" ")
            with self.assertRaises(RuntimeError):
                archive_previous_verified_pin(pin_path=pin,
                    archive_directory=Path(temp)/"history",
                    expected_file_sha256=digest,approved=True)


if __name__=="__main__":
    unittest.main()
