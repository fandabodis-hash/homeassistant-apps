"""F51 testy: pripustit pouze presne jeden restart 00011."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
import os

from ha_core_restart_00011 import RestartRejected, validate_restart_request, _write_once_marker

NOW = datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)
UUID = "f6283e2d-0e1b-43ee-b9e3-2c7b6fb9d001"
ID = "f6283e2d-0e1b-43ee-b9e3-2c7b6fb9d002"
NONCE = "f6283e2d-0e1b-43ee-b9e3-2c7b6fb9d003"


def payload():
    return {"target_serial": "F800711-TNG-00011", "device_uuid": UUID,
            "source": "f51_supervisor_core_restart",
            "confirmation": "RESTART_HA_CORE_ONLY", "nonce": NONCE,
            "expires_at": (NOW + timedelta(seconds=90)).isoformat()}


class RestartSafety(TestCase):
    def verify(self, p=None, serial="F800711-TNG-00011"):
        return validate_restart_request(cloud_identity={"device_uuid": UUID},
            permanent_serial=serial, command_id=ID,
            payload=p if p is not None else payload(), now=NOW)

    def test_exact_only(self):
        self.assertEqual(self.verify()["device_uuid"], UUID)

    def test_other_models_rejected(self):
        for serial in ("F800711-TNG-00012", "F800711-TNG-00013", None):
            with self.subTest(serial=serial), self.assertRaises(RestartRejected):
                self.verify(serial=serial)

    def test_wrong_device_uuid(self):
        p=payload(); p["device_uuid"]="f6283e2d-0e1b-43ee-b9e3-2c7b6fb9d004"
        with self.assertRaises(RestartRejected): self.verify(p)

    def test_wrong_confirmation(self):
        p=payload(); p["confirmation"]="REBOOT_HOST"
        with self.assertRaises(RestartRejected): self.verify(p)

    def test_extra_key(self):
        p=payload(); p["shell"]="arbitrary_command"
        with self.assertRaises(RestartRejected): self.verify(p)

    def test_expired(self):
        p=payload(); p["expires_at"]=(NOW-timedelta(seconds=5)).isoformat()
        with self.assertRaises(RestartRejected): self.verify(p)

    def test_long_ttl(self):
        p=payload(); p["expires_at"]=(NOW+timedelta(hours=1)).isoformat()
        with self.assertRaises(RestartRejected): self.verify(p)

    def test_marker_cannot_overwrite(self):
        with TemporaryDirectory() as d:
            path=Path(d)/"restart-once.json"
            _write_once_marker(path=path,command_id=ID,device_uuid=UUID,now=NOW)
            with self.assertRaises(FileExistsError):
                _write_once_marker(path=path,command_id=ID,device_uuid=UUID,now=NOW)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

if __name__ == "__main__": main()
