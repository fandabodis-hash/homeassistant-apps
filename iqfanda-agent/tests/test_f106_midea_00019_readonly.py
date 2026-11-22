"""F106 R1 regression: no physical device, no writes, only 00019."""
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(ROOT))
from communication import midea_00019_readonly as midea  # noqa:E402

class FakeResponse:
    def __init__(self, values): self.registers = values
    def isError(self): return False

class FakeClient:
    writes = 0
    def __init__(self, **kwargs):
        self.params = kwargs
        self.reads = []
        self.closed = False
    def connect(self): return True
    def _read(self, mode, *, address, count, device_id):
        self.reads.append((mode, address, count, device_id))
        vals = {100: 43, 101: 3, 104: 37, 105: 45, 107: 65531,
                124: 0, 129: 8, 138: 225, 143: 0, 144: 1377,
                150: 720, 151: 312, 199: 3}
        return FakeResponse([vals.get(address + i, 0) for i in range(count)])
    def read_input_registers(self, **kwargs): return self._read(4, **kwargs)
    def read_holding_registers(self, **kwargs): return self._read(3, **kwargs)
    def write_register(self, *args, **kwargs):
        FakeClient.writes += 1
        raise AssertionError("NO_MODBUS_WRITE")
    def close(self): self.closed = True

class TestMideaR290ReadOnly(unittest.TestCase):
    def test_only_exact_model_and_stable_ftdi_path(self):
        for serial, port, slave, fc in [
            ("F800711-TNG-00013", midea.FTDI_PORT, 1, 4),
            (midea.SERIAL, "/dev/ttyUSB0", 1, 4),
            (midea.SERIAL, midea.FTDI_PORT, 2, 4),
            (midea.SERIAL, midea.FTDI_PORT, 1, 6),
        ]:
            with self.assertRaises(midea.MideaProbeError):
                midea.validate_target(serial_number=serial, port_path=port,
                                      unit_id=slave, function_code=fc)

    def test_read_only_probe_decodes_temperature_energy_state(self):
        created = []
        def factory(**kw):
            x = FakeClient(**kw); created.append(x); return x
        result = midea.probe_once(serial_number=midea.SERIAL,
                                  port_path=midea.FTDI_PORT, unit_id=1, function_code=4,
                                  client_factory=factory, lock_factory=lambda _: nullcontext(),
                                  check_path=False)
        self.assertEqual(result["serial_number"], midea.SERIAL)
        self.assertTrue(result["read_only"])
        self.assertFalse(result["writes_performed"])
        self.assertEqual(result["measurements"]["water_return_c"], 37)
        self.assertEqual(result["measurements"]["water_flow_c"], 45)
        self.assertEqual(result["measurements"]["outside_temperature_c"], -5)
        self.assertEqual(result["measurements"]["internal_pump_on"], True)
        self.assertEqual(result["measurements"]["energy_total_kwh"], 13.77)
        self.assertEqual(result["measurements"]["electric_power_kw_candidate"], 7.2)
        self.assertEqual(len(created[0].reads), len(midea.BLOCKS))
        self.assertTrue(all(x[0] == 4 and x[3] == 1 for x in created[0].reads))
        self.assertTrue(created[0].closed)
        self.assertEqual(FakeClient.writes, 0)

    def test_official_map_is_not_claimed_verified_without_water_block(self):
        class NoResponseClient(FakeClient):
            def read_input_registers(self, **kwargs):
                return None
        with self.assertRaisesRegex(midea.MideaProbeError, "NO_WATER_TEMPERATURE_RESPONSE"):
            midea.probe_once(serial_number=midea.SERIAL, port_path=midea.FTDI_PORT,
                             unit_id=16, function_code=4, client_factory=NoResponseClient,
                             lock_factory=lambda _: nullcontext(), check_path=False)
        self.assertEqual(FakeClient.writes, 0)

if __name__ == "__main__":
    unittest.main()
