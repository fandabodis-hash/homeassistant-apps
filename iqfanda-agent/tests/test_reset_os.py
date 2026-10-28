"""F52 Reset OS: testy bez pristupu k Supervisoru a bez skutecneho restartu."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs" / "app"))
import reset_os


NOW = datetime(2026, 10, 28, 18, 30, tzinfo=timezone.utc)
UUID = "fae6f7c1-64c9-4de0-a9f4-c81ebbb5a910"
COMMAND_ID = "fae6f7c1-64c9-4de0-a9f4-c81ebbb5a911"
NONCE = "fae6f7c1-64c9-4de0-a9f4-c81ebbb5a912"


def payload(serial="F800711-TNG-00011", expires_at=None):
    return {
        "serial_number": serial,
        "device_uuid": UUID,
        "confirmation": "RESET_OS",
        "nonce": NONCE,
        "expires_at": (expires_at or NOW + timedelta(seconds=120)).isoformat(),
        "source": "admin_portal",
    }


class ResetOsSafetyTests(TestCase):
    def check(self, serial="F800711-TNG-00011", custom=None):
        return reset_os.check_request(
            cloud_identity={"device_uuid": UUID},
            manufacturing_serial=serial,
            command_id=COMMAND_ID,
            payload=custom if custom is not None else payload(serial),
            now=NOW,
        )

    def test_accept_all_valid_factory_serials(self):
        for serial in ("F800711-TNG-00007", "F800711-TNG-00011",
                       "F800711-TNG-00012", "F800711-TNG-00013",
                       "F800711-TNG-99999"):
            with self.subTest(serial=serial):
                self.assertEqual(self.check(serial)["serial_number"], serial)

    def test_wrong_target_rejected(self):
        with self.assertRaises(reset_os.ResetOsRejected):
            self.check(custom=payload("F800711-TNG-00012"))

    def test_unknown_factory_serial_rejected(self):
        with self.assertRaises(reset_os.ResetOsRejected):
            self.check(serial="HACKER-DEVICE", custom=payload("HACKER-DEVICE"))

    def test_bad_device_uuid_rejected(self):
        p = payload()
        p["device_uuid"] = str(uuid.uuid4())
        with self.assertRaises(reset_os.ResetOsRejected):
            self.check(custom=p)

    def test_extra_command_argument_rejected(self):
        p = payload()
        p["command"] = "shutdown"
        with self.assertRaises(reset_os.ResetOsRejected):
            self.check(custom=p)

    def test_invalid_confirmation_rejected(self):
        p = payload()
        p["confirmation"] = "REBOOT_RPI"
        with self.assertRaises(reset_os.ResetOsRejected):
            self.check(custom=p)

    def test_expired_and_too_long_commands_rejected(self):
        for delay in (-1, 190):
            with self.subTest(delay=delay):
                with self.assertRaises(reset_os.ResetOsRejected):
                    self.check(custom=payload(expires_at=NOW + timedelta(seconds=delay)))

    def test_nonce_must_not_equal_command_id(self):
        p = payload()
        p["nonce"] = COMMAND_ID
        with self.assertRaises(reset_os.ResetOsRejected):
            self.check(custom=p)

    def test_cooldown_rejects_immediate_repeat(self):
        checked = self.check()
        previous = {"command_id": str(uuid.uuid4()),
                    "attempted_at": (NOW - timedelta(minutes=1)).isoformat()}
        with self.assertRaises(reset_os.ResetOsRejected):
            reset_os.check_cooldown(previous, checked, NOW)

    def test_cooldown_allows_after_ten_minutes(self):
        checked = self.check()
        previous = {"command_id": str(uuid.uuid4()),
                    "attempted_at": (NOW - timedelta(minutes=11)).isoformat()}
        reset_os.check_cooldown(previous, checked, NOW)

    def test_actual_execution_invokes_core_only(self):
        calls = []
        results = []

        def api(method, url, token, payload=None):
            calls.append((method, url, payload))
            return {"result": "ok", "data": {"state": "started"}}

        with TemporaryDirectory() as tmp:
            with patch.object(reset_os, "STATE_PATH", Path(tmp) / "state.json"), \
                 patch.object(reset_os, "LOCK_PATH", Path(tmp) / "lock"), \
                 patch("host.identity.DeviceIdentityService") as identity_service, \
                 patch.object(reset_os, "_api", side_effect=api), \
                 patch.dict("os.environ", {"SUPERVISOR_TOKEN": "test-token"}, clear=False):
                identity_service.return_value.get_serial_number.return_value = "F800711-TNG-00011"
                reset_os.execute_reset_os(
                    identity={"device_uuid": UUID},
                    command_id=COMMAND_ID,
                    command_payload=payload(),
                    submit_result=lambda **kwargs: results.append(kwargs),
                )
                self.assertTrue((Path(tmp) / "state.json").exists())
                self.assertEqual([x[1] for x in calls],
                                 ["http://supervisor/core/info", "http://supervisor/core/restart"])
                self.assertEqual(calls[1][0], "POST")
                self.assertEqual(calls[1][2], {"safe_mode": False, "force": False})
                self.assertEqual(results[-1]["status"], "succeeded")
                self.assertFalse(results[-1]["result"]["core_running_verified_after"])

    def test_same_request_never_restarts_twice(self):
        calls = []
        results = []

        def api(method, url, token, payload=None):
            calls.append(url)
            return {"result": "ok", "data": {"state": "started"}}

        with TemporaryDirectory() as tmp:
            with patch.object(reset_os, "STATE_PATH", Path(tmp) / "state.json"), \
                 patch.object(reset_os, "LOCK_PATH", Path(tmp) / "lock"), \
                 patch("host.identity.DeviceIdentityService") as identity_service, \
                 patch.object(reset_os, "_api", side_effect=api), \
                 patch.dict("os.environ", {"SUPERVISOR_TOKEN": "test-token"}, clear=False):
                identity_service.return_value.get_serial_number.return_value = "F800711-TNG-00011"
                for _ in range(2):
                    reset_os.execute_reset_os(
                        identity={"device_uuid": UUID},
                        command_id=COMMAND_ID,
                        command_payload=payload(),
                        submit_result=lambda **kwargs: results.append(kwargs),
                    )
            self.assertEqual(
                sum(url == "http://supervisor/core/restart" for url in calls), 1
            )
            self.assertEqual([r["status"] for r in results], ["succeeded", "failed"])


if __name__ == "__main__":
    main()
