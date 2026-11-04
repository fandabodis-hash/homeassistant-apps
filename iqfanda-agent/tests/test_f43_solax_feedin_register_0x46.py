"""F43 read-only Solax alternative meter register - offline regression only."""
import json
import unittest
from pathlib import Path
from communication.inverter_adapter import decode_profile_entities, validate_inverter_profile

PROFILE = Path(__file__).resolve().parents[1] / "rootfs/app/communication/inverter_profiles/solax_x3_hybrid_g4.json"

def fixture_registers(profile):
    regs={}
    for block in profile["read_blocks"]:
        for a in range(block["address"],block["address"]+block["count"]):
            regs[a]=0
    return regs

class Solax046Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile=json.loads(PROFILE.read_text(encoding="utf-8"))
        validate_inverter_profile(cls.profile)

    def test_original_required_blocks_are_unchanged(self):
        self.assertEqual([(r["address"],r["count"]) for r in self.profile["read_blocks"]],
                         [(0,30),(50,12),(72,12),(96,22),(145,12),(190,2)])

    def test_extra_read_is_non_required(self):
        self.assertEqual(self.profile["selector_read_blocks"][-1],
                         {"name":"diagnostic_meter_feed_in_0x46","address":70,"count":2,"required":False})

    def test_missing_optional_register_does_not_break_normal_telemetry(self):
        result=decode_profile_entities(profile=self.profile,registers=fixture_registers(self.profile))
        values={e["entity_key"]:e["value"] for e in result}
        self.assertNotIn("smartmeter.feedin_power_0x46_diagnostic",values)
        self.assertEqual(values["spotreba.vykon_celkem"],0)
        self.assertEqual(values["smartmeter.vykon_celkem"],0)

    def test_negative_import_3550watts_decoded_signed_little_endian(self):
        regs=fixture_registers(self.profile)
        number=(-3550) & 0xffffffff
        regs[70]=number & 0xffff
        regs[71]=(number >> 16) & 0xffff
        result=decode_profile_entities(profile=self.profile,registers=regs)
        values={e["entity_key"]:e["value"] for e in result}
        self.assertEqual(values["smartmeter.feedin_power_0x46_diagnostic"],-3550)
        self.assertEqual(values["smartmeter.vykon_celkem"],0)
        self.assertEqual(values["spotreba.vykon_celkem"],0)

    def test_positive_export_watts_decoded(self):
        regs=fixture_registers(self.profile)
        regs[70]=1100
        regs[71]=0
        result=decode_profile_entities(profile=self.profile,registers=regs)
        values={e["entity_key"]:e["value"] for e in result}
        self.assertEqual(values["smartmeter.feedin_power_0x46_diagnostic"],1100)

    def test_other_production_output_values_unchanged(self):
        regs=fixture_registers(self.profile)
        regs[100]=(0xffff-199)
        regs[101]=(0xffff-49)
        regs[102]=(0xffff-49)
        vals0={e["entity_key"]:e["value"] for e in decode_profile_entities(profile=self.profile,registers=regs)}
        regs[70]=0xffff
        regs[71]=0xffff
        vals1={e["entity_key"]:e["value"] for e in decode_profile_entities(profile=self.profile,registers=regs)}
        vals1.pop("smartmeter.feedin_power_0x46_diagnostic")
        self.assertEqual(vals0,vals1)

if __name__=="__main__":
    unittest.main()
