"""Offline model-00013 SONOFF ZHA read-only probe tests."""
import unittest
from servo_zha_inching_probe import (
    summarize, InvalidZhaDevice, DEVICE_UUID, IEEE, CUSTOM_CLUSTER,
)

IDENT = {"device_uuid": DEVICE_UUID}
DEVICE = {
    "ieee": IEEE, "manufacturer": "SONOFF", "model": "MINI-ZB2GS",
    "signature": {"manufacturer": "SONOFF", "model": "MINI-ZB2GS", "endpoints": {
        "1": {"input_clusters": [0, 6, 0xFC11]},
        "2": {"input_clusters": [0, 6, "0xFC11"]},
    }}
}


class DeviceProbeTests(unittest.TestCase):
    def run_probe(self, device=None, identity=None):
        return summarize(IDENT if identity is None else identity,
                         [DEVICE if device is None else device])

    def test_exact_device(self):
        r=self.run_probe()
        self.assertEqual(r["onoff_channels"], [1,2])
        self.assertEqual(r["manufacturer_cluster_endpoints"], [1,2])
    def test_never_attests_off(self):
        r=self.run_probe()
        self.assertFalse(r["device_native_auto_off_verified"])
        self.assertFalse(r["device_native_auto_off_configured"])
        self.assertFalse(r["electrical_direction_interlock_verified"])
        self.assertFalse(r["actuation_allowed"])
    def test_wrong_model_id(self):
        with self.assertRaises(InvalidZhaDevice):
            self.run_probe(identity={"device_uuid":"other"})
    def test_wrong_ieee(self):
        with self.assertRaises(InvalidZhaDevice):
            self.run_probe({**DEVICE, "ieee": "08:fd:52:ff:fe:07:87:d8"})
    def test_duplicate(self):
        with self.assertRaises(InvalidZhaDevice):
            summarize(IDENT, [DEVICE, DEVICE])
    def test_wrong_manufacturer(self):
        x={**DEVICE,"signature":{**DEVICE["signature"],"manufacturer":"other"}}
        with self.assertRaises(InvalidZhaDevice):
            self.run_probe(x)
    def test_wrong_model(self):
        x={**DEVICE,"signature":{**DEVICE["signature"],"model":"MINI-ZB2GS-L"}}
        with self.assertRaises(InvalidZhaDevice):
            self.run_probe(x)
    def test_missing_signature(self):
        r=self.run_probe({k:v for k,v in DEVICE.items() if k!="signature"})
        self.assertEqual(r["manufacturer_cluster_endpoints"],[])
        self.assertFalse(r["actuation_allowed"])
    def test_input_cluster_missing(self):
        x={**DEVICE,"signature":{"endpoints":{"1":{"input_clusters":[0,6]}}}}
        r=self.run_probe(x)
        self.assertEqual(r["manufacturer_cluster_endpoints"],[])
    def test_cluster_dicts(self):
        x={**DEVICE, "signature": {"endpoints":{"1":{"input_clusters":[{"id":"0xFC11"},{"cluster_id":6}]}}}}
        r=self.run_probe(x)
        self.assertEqual(r["manufacturer_cluster_endpoints"],[1])
    def test_invalid_device_list(self):
        with self.assertRaises(InvalidZhaDevice):
            summarize(IDENT, {})
    def test_malformed_endpoints(self):
        x={**DEVICE, "signature":{"manufacturer":"SONOFF","model":"MINI-ZB2GS","endpoints":{"notint":{"input_clusters":[6]}}}}
        r=self.run_probe(x)
        self.assertEqual(r["onoff_channels"], [])
    def test_leaks_no_tokens(self):
        x={**DEVICE,"device_token":"TOP_SECRET","signature":{**DEVICE["signature"],"credentials":"TOP_SECRET"}}
        r=self.run_probe(x)
        self.assertNotIn("TOP_SECRET",str(r))
    def test_exact_signature_can_override_outer(self):
        x={**DEVICE, "model":"incorrect"}
        self.assertEqual(self.run_probe(x)["model"],"MINI-ZB2GS")


if __name__ == "__main__":
    unittest.main()
