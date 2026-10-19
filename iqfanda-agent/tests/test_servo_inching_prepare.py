"""F47 R117 — isolated no-actuation tests."""
import unittest
from servo_inching_prepare import prepare, frame, UnsafeConfiguration, OPEN, CLOSE, UUID, SERIAL, IEEE


def devices():
    return [{"ieee": IEEE, "manufacturer": "SONOFF", "model": "MINI-ZB2GS",
             "device_reg_id": "same-registry",
             "signature": {"manufacturer": "SONOFF", "model": "MINI-ZB2GS",
                           "endpoints": {"1": {"input_clusters": [6, 0xFC11]},
                                         "2": {"input_clusters": [6, 0xFC11]}}}}]


class SafetyTests(unittest.TestCase):
    def args(self):
        commands = []
        return {
            "identity": {"device_uuid": UUID},
            "payload": {"serial": SERIAL, "mode": "prepare_native_off_1s"},
            "get_zha_devices": devices,
            "get_entity_state": lambda _: {"state": "off"},
            "get_entity_device_id": lambda _: "same-registry",
            "get_ha_services": lambda: [{"domain": "zha", "services": {
                "issue_zigbee_cluster_command": {}}}],
            "call_ha_service": lambda **kw: commands.append(kw),
        }, commands

    def test_frame_ch1(self):
        self.assertEqual(frame(1)[:8], [1, 23, 7, 128, 128, 0, 2, 0])
        self.assertEqual(frame(1)[-1], 1 ^ 23 ^ 7 ^ 128 ^ 128 ^ 0 ^ 2)
    def test_frame_ch2(self):
        self.assertEqual(frame(2)[5], 1)
    def test_bad_endpoint(self):
        for x in (0, 3, True, "1"):
            with self.assertRaises(UnsafeConfiguration):
                frame(x)
    def test_prepare_no_actuation(self):
        args, calls = self.args()
        result = prepare(**args)
        self.assertFalse(result["device_native_auto_off_verified"])
        self.assertFalse(result["directional_interlock_verified"])
        self.assertFalse(result["actuation_enabled"])
        self.assertEqual([x["payload"]["endpoint_id"] for x in calls], [1, 2])
        self.assertEqual([x["payload"]["command"] for x in calls], [0, 0])
        self.assertEqual([x["payload"]["manufacturer"] for x in calls], [0x1286, 0x1286])
        self.assertFalse(any("switch" in str(x) for x in calls))
    def test_wrong_identity_no_commands(self):
        args, calls = self.args()
        args["identity"] = {"device_uuid": "other"}
        with self.assertRaises(UnsafeConfiguration):
            prepare(**args)
        self.assertEqual(calls, [])
    def test_wrong_payload_no_commands(self):
        args, calls = self.args()
        args["payload"] = {"serial": SERIAL, "mode": "prepare_native_off_1s",
                           "force": True}
        with self.assertRaises(UnsafeConfiguration):
            prepare(**args)
        self.assertEqual(calls, [])
    def test_wrong_ieee_no_commands(self):
        args, calls = self.args()
        args["get_zha_devices"] = lambda: [{"ieee": "another"}]
        with self.assertRaises(UnsafeConfiguration):
            prepare(**args)
        self.assertEqual(calls, [])
    def test_channel_on_no_commands(self):
        args, calls = self.args()
        args["get_entity_state"] = lambda x: {"state": "on" if x == OPEN else "off"}
        with self.assertRaises(UnsafeConfiguration):
            prepare(**args)
        self.assertEqual(calls, [])
    def test_missing_cluster_no_commands(self):
        args, calls = self.args()
        d = devices()
        d[0]["signature"]["endpoints"]["2"]["input_clusters"] = [6]
        args["get_zha_devices"] = lambda: d
        with self.assertRaises(UnsafeConfiguration):
            prepare(**args)
        self.assertEqual(calls, [])
    def test_bad_registry_no_commands(self):
        args, calls = self.args()
        args["get_entity_device_id"] = lambda x: x
        with self.assertRaises(UnsafeConfiguration):
            prepare(**args)
        self.assertEqual(calls, [])
    def test_no_cluster_service_no_commands(self):
        args, calls = self.args()
        args["get_ha_services"] = lambda: [{"domain": "zha", "services": {}}]
        with self.assertRaises(UnsafeConfiguration):
            prepare(**args)
        self.assertEqual(calls, [])
    def test_failed_first_write_stops(self):
        args, calls = self.args()
        def fail(**kw):
            calls.append(kw)
            raise RuntimeError("ZHA command schema unsupported")
        args["call_ha_service"] = fail
        with self.assertRaises(RuntimeError):
            prepare(**args)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
