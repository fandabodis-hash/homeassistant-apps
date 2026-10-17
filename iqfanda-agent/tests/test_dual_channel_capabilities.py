"""No-Hardware tests for MINI-ZB2GS discovery metadata."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "rootfs", "app")))
from dual_channel_capabilities import mini_zb2gs_relay_channels as channels


def relay(eid):
    return {"entity_id": eid, "capabilities": ["binary_power_output"]}


class TestChannels(unittest.TestCase):
    def test_00013_tuv(self):
        got = channels("MINI-ZB2GS", [relay("switch.sonoff_mini_zb2gs_spinac_2"), relay("switch.sonoff_mini_zb2gs_spinac")])
        self.assertEqual([x["channel"] for x in got], [1, 2])
        self.assertEqual(got[0]["entity_id"], "switch.sonoff_mini_zb2gs_spinac")

    def test_00013_pumps(self):
        got = channels("MINI-ZB2GS", [relay("switch.sonoff_mini_zb2gs_spinac_6"), relay("switch.sonoff_mini_zb2gs_spinac_5")])
        self.assertEqual([x["entity_id"] for x in got], ["switch.sonoff_mini_zb2gs_spinac_5", "switch.sonoff_mini_zb2gs_spinac_6"])

    def test_configuration_not_relay(self):
        got = channels("MINI-ZB2GS", [relay("switch.switch_1"), relay("switch.switch_2"), {"entity_id": "switch.turbo", "entity_category": "config", "capabilities": []}])
        self.assertEqual(len(got), 2)

    def test_incomplete_or_other_device(self):
        self.assertEqual(channels("MINI-ZB2GS", [relay("switch.one_1")]), [])
        self.assertEqual(channels("OTHER", [relay("switch.one_1"), relay("switch.one_2")]), [])
        self.assertEqual(channels("MINI-ZB2GS", [{"entity_id": "switch.one_1"}, {"entity_id": "switch.one_2"}]), [])

    def test_different_stems_ambiguous(self):
        self.assertEqual(channels("MINI-ZB2GS", [relay("switch.left_1"), relay("switch.right_2")]), [])


if __name__ == "__main__":
    unittest.main()
