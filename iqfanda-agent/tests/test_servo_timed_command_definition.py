"""F47 R120 safe static ZCL command contract test; no Zigbee I/O."""
import unittest
from servo_timed_command_definition import (
    make_timed_request, SafetyStop, UUID, IEEE,
)

class TimedCommandDefinitionTests(unittest.TestCase):
    def test_exact_timed_duration(self):
        cmd=make_timed_request(
            device_uuid=UUID,ieee=IEEE,endpoint=1,
            relay_open_state="off",relay_close_state="off",
        )
        self.assertEqual(cmd["cluster_id"],6)
        self.assertEqual(cmd["command"],0x42)
        self.assertEqual(cmd["params"],{
            "on_off_control":0,"on_time":10,"off_wait_time":20,
        })
        self.assertNotIn("manufacturer",cmd)

    def test_each_endpoint(self):
        for ep in (1,2):
            with self.subTest(endpoint=ep):
                cmd=make_timed_request(
                    device_uuid=UUID,ieee=IEEE,endpoint=ep,
                    relay_open_state="off",relay_close_state="off",
                )
                self.assertEqual(cmd["endpoint_id"],ep)
    def test_reject_incorrect_identity(self):
        with self.assertRaises(SafetyStop):
            make_timed_request(
                device_uuid="invalid",ieee=IEEE,endpoint=1,
                relay_open_state="off",relay_close_state="off",
            )
    def test_reject_channel_on(self):
        with self.assertRaises(SafetyStop):
            make_timed_request(
                device_uuid=UUID,ieee=IEEE,endpoint=1,
                relay_open_state="on",relay_close_state="off",
            )
    def test_reject_invalid_endpoint(self):
        with self.assertRaises(SafetyStop):
            make_timed_request(
                device_uuid=UUID,ieee=IEEE,endpoint=3,
                relay_open_state="off",relay_close_state="off",
            )

if __name__=="__main__":
    unittest.main()
