import json
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(ROOT))
from communication.midea_00019_ftdi_inventory import enrich_if_verified, DEVICE_UUID, FTDI_PATH

GOOD = {"devices": [{"inventory_id": "ftdi-only-00019", "vid": "0403", "pid": "6001", "serial_number": "BG02YF7T", "connected": True, "serial_ports": [{"port": "/dev/ttyUSB1", "stable_path": FTDI_PATH}]}]}


class TestInventoryReadOnly(unittest.TestCase):
    def file(self, data):
        f = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        json.dump(data, f)
        f.close()
        self.addCleanup(lambda: Path(f.name).unlink(missing_ok=True))
        return Path(f.name)

    def call(self, state, *, device_uuid=DEVICE_UUID, data=GOOD, exists=True):
        return enrich_if_verified(state, device_uuid=device_uuid, inventory_path=self.file(data), is_serial_device=lambda _: exists)

    def test_exact_target_advertises_one(self):
        old = {"communicators": [{"type": "zigbee_coordinator", "preferred_path": "/dev/ttyACM0"}], "summary": {"communicators_total": 1}}
        out = self.call(old)
        self.assertEqual(len(out["communicators"]), 2)
        self.assertEqual(out["communicators"][1]["type"], "usb_serial")
        self.assertEqual(out["communicators"][1]["preferred_path"], FTDI_PATH)
        self.assertEqual(old["communicators"], [{"type": "zigbee_coordinator", "preferred_path": "/dev/ttyACM0"}])

    def test_other_fandas_untouched(self):
        old = {"communicators": []}
        self.assertIs(self.call(old, device_uuid="anything-else"), old)

    def test_missing_or_ambiguous_hardware_no_advertisement(self):
        old = {"communicators": []}
        self.assertIs(self.call(old, exists=False), old)
        self.assertIs(self.call(old, data={"devices": []}), old)
        self.assertIs(self.call(old, data={"devices": GOOD["devices"] * 2}), old)
        invalid = json.loads(json.dumps(GOOD))
        invalid["devices"][0]["serial_number"] = "WRONG"
        self.assertIs(self.call(old, data=invalid), old)

    def test_does_not_replace_existing_entry(self):
        old = {"communicators": [{"preferred_path": FTDI_PATH, "connected": False}]}
        self.assertIs(self.call(old), old)

    def test_invalid_inventory_no_advertisement(self):
        old = {"communicators": []}
        self.assertIs(self.call(old, data={"devices": "not-array"}), old)
        bad = {"communicators": "not-array"}
        self.assertIs(self.call(bad), bad)


if __name__ == "__main__":
    unittest.main()
