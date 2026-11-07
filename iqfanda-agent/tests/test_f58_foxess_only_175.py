"""FoxESS-only 0.1.175 source acceptance, no Modbus/cloud writes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

APP = Path(__file__).resolve().parents[1] / "rootfs/app"
ADDON = APP.parent.parent
VERIFIED_VACULIK_FOXESS_BLOB = "680f9c89221dc9fc20d0408bf28c8637a367c7c5"


class FoxessOnly175Tests(unittest.TestCase):
    def test_vaculik_physically_working_168_driver_is_byte_identical(self):
        data = (APP / "communication/foxess_ems_controller.py").read_bytes()
        git_sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        self.assertEqual(git_sha, VERIFIED_VACULIK_FOXESS_BLOB)

    def test_version_is_175_but_foxess_driver_is_not_a_new_protocol(self):
        self.assertEqual((APP/"VERSION").read_text().strip(), "0.1.175")
        settings=(ADDON/"config.yaml").read_text()
        self.assertIn('version: "0.1.175"', settings)
        self.assertIn('software_version: "0.1.175"', settings)

    def test_no_new_ha_smartmeter_service_in_175_foxess_controls(self):
        # The installed FoxESS smart meter belongs to the inverter. This
        # release must not require new HA entities as an extra prerequisite.
        source=(APP/"manual_battery_runtime.py").read_text()
        self.assertNotIn("foxess_commissioning_grid_guard", source)
        self.assertNotIn("foxess_grid_commissioning_guard", source)

    def test_physical_pin_proof_is_never_auto_approved_or_deleted(self):
        source=(APP/"manual_battery_runtime.py").read_text()
        self.assertIn("O_EXCL", source)
        self.assertIn("prior_pins", source)
        self.assertIn("expected_previous_pin_sha256", source)
        self.assertIn("explicitly_approved", source)
        self.assertIn("foxess_control_fingerprint", source)

    def test_only_00007_can_be_commissioned_on_175(self):
        source=(APP/"command_worker.py").read_text()
        self.assertIn('read_installed_agent_version() == "0.1.175"', source)
        self.assertIn('1c25a8d4-b986-43ee-b2fc-eaa152e9bdf8', source)
        self.assertIn("explicit_technician_approval", source)

    def test_original_foxess_169_evidence_remains_append_only(self):
        history=json.loads((ADDON/"contracts/manual_battery_release.json").read_text())
        revisions=history.get("change_history",[])
        self.assertTrue(any(
            r.get("agent_version")=="0.1.169" and
            r.get("runtime_fingerprint")=="4baabda610229bb7b0ac55f0990ef464b3db3deb86e7fed98fc3473929530f9c"
            for r in revisions))
        self.assertEqual(history["agent_version"],"0.1.175")
        self.assertTrue(revisions[-1]["reason"].startswith("F58 0.1.175"))


if __name__=="__main__":
    unittest.main()
