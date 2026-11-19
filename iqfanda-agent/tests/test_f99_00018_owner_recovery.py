"""F99 00018: offline gates for one-shot FoxESS zero-remote rescue.

No physical I/O, network, cloud, or database is ever used by these tests.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from foxess_00018_owner_recovery import (
    AUDIT_PATH, CONFIRMATION, EXPECTED_FIRMWARE, FIXED, PROFILE,
    RECOVERY_ID, SERIAL, SOURCE, VERSION, RecoveryRejected,
    _audit, check_request, check_snapshot
)


class OwnerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.device_uuid = str(uuid.uuid4())
        self.id = {"device_uuid": self.device_uuid}
        self.command_id = str(uuid.uuid4())
        from datetime import timedelta
        from foxess_00018_owner_recovery import now
        self.payload = {
            "serial_number": SERIAL, "device_uuid": self.device_uuid,
            "source": SOURCE, "confirmation": CONFIRMATION,
            "recovery_id": RECOVERY_ID, "expected_agent_version": VERSION,
            "expected_runtime_fingerprint": "a"*64,
            "issued_at": (now()-timedelta(seconds=5)).isoformat(),
            "expires_at": (now()+timedelta(seconds=95)).isoformat(),
        }

    def checked(self, **changes):
        return check_request(identity=self.id, serial=SERIAL,
                             command_id=self.command_id,
                             payload={**self.payload, **changes})

    def test_expected_scoped_action(self):
        self.assertEqual(self.checked()["serial_number"], SERIAL)
        self.assertEqual(self.checked()["recovery_id"], RECOVERY_ID)
        self.assertEqual(VERSION, "0.1.176")
        self.assertEqual(PROFILE, "foxess_h3_10_0_e")

    def test_no_other_device_or_scope(self):
        with self.assertRaises(RecoveryRejected):
            check_request(identity=self.id, serial="F800711-TNG-00007",
                          command_id=self.command_id, payload=self.payload)
        for changes in ({"serial_number": "F800711-TNG-00007"},
                        {"device_uuid": str(uuid.uuid4())},
                        {"source": "other"},
                        {"expected_agent_version": "0.1.175"},
                        {"expected_runtime_fingerprint": "invalid"},
                        {"confirmation": "NO_CONFIRMATION"},
                        {"extra_field": "unapproved"}):
            with self.subTest(changes=changes), self.assertRaises(RecoveryRejected):
                self.checked(**changes)

    def test_bounded_fresh_authorization(self):
        from datetime import timedelta
        from foxess_00018_owner_recovery import now
        with self.assertRaises(RecoveryRejected):
            self.checked(issued_at=(now()-timedelta(minutes=5)).isoformat(),
                         expires_at=(now()+timedelta(minutes=5)).isoformat())
        with self.assertRaises(RecoveryRejected):
            self.checked(issued_at=(now()-timedelta(minutes=5)).isoformat(),
                         expires_at=(now()-timedelta(seconds=1)).isoformat())

    def snapshot(self, **regs):
        return {"protocol_version": 115,
                "firmware": dict(EXPECTED_FIRMWARE),
                "registers": {**FIXED, "remote_enable_44000": 1, **regs}}

    def test_only_zero_active_power(self):
        check_snapshot(self.snapshot(), remote={0, 1})
        check_snapshot(self.snapshot(remote_enable_44000=0), remote={0})
        with self.assertRaises(RecoveryRejected):
            check_snapshot(self.snapshot(remote_active_power_44002=500), remote={0, 1})
        with self.assertRaises(RecoveryRejected):
            check_snapshot(self.snapshot(remote_active_power_44002=65036), remote={0, 1})

    def test_preserve_work_mode_and_timeout(self):
        for key, value in (("work_mode_41000", 2),
                           ("remote_timeout_44001", 180),
                           ("max_soc_41010", 90),
                           ("remote_enable_44000", 2)):
            with self.subTest(key=key), self.assertRaises(RecoveryRejected):
                check_snapshot(self.snapshot(**{key: value}), remote={0, 1})

    def test_firmware_not_guessable(self):
        wrong = self.snapshot()
        wrong["firmware"]["30018"] = 389
        with self.assertRaises(RecoveryRejected):
            check_snapshot(wrong, remote={0, 1})
        wrong = self.snapshot()
        wrong["protocol_version"] = 114
        with self.assertRaises(RecoveryRejected):
            check_snapshot(wrong, remote={0, 1})

    def test_audit_is_durable_one_use(self):
        from foxess_00018_owner_recovery import AUDIT_PATH as saved
        with tempfile.TemporaryDirectory() as tmp:
            import foxess_00018_owner_recovery as mod
            with patch.object(mod, "AUDIT_PATH", Path(tmp) / "attempt.json"):
                _audit({"schema_version": 1, "state": "write_intent"})
                audit_path = mod.AUDIT_PATH
                self.assertEqual(json.loads(audit_path.read_text())["state"], "write_intent")
                self.assertEqual(audit_path.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(RecoveryRejected):
                    _audit({"schema_version": 1, "state": "write_intent"})
                self.assertEqual(json.loads(audit_path.read_text())["state"], "write_intent")


if __name__ == "__main__":
    unittest.main()
