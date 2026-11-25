"""F152 local reader: fake serial clients only."""
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rootfs/app"))
from communication import midea_00019_live as live

IDENTITY = {"device_uuid": live.DEVICE_UUID, "site_id": live.SITE_ID}

class Client:
    calls = []
    fail = set()
    def __init__(self, **kw):
        self.kw = kw
    def connect(self):
        return True
    def read_holding_registers(self, *, address, count, device_id):
        self.calls.append((address,count,device_id))
        if address in self.fail:
            return None
        class Response:
            def isError(self): return False
        r=Response()
        values={0:2,1:3,100:33,101:3,104:37,105:35,143:1,144:6719,150:115}
        r.registers=[values.get(i,0) for i in range(address,address+count)]
        return r
    def close(self): pass

class TestLive(unittest.TestCase):
    def setUp(self):
        Client.calls=[];Client.fail=set()
        self.lock=threading.Lock()
    def sample(self, identity=IDENTITY, port=True):
        with patch.object(live.time,"sleep"):
            return live.read_sample(identity,client_factory=Client,
                lock_factory=lambda _:self.lock,port_checker=lambda _:port)
    def test_exact_fc03_unit1_observed_blocks(self):
        r=self.sample()
        self.assertEqual(Client.calls,[(a,n,1) for a,n in live.BLOCKS])
        self.assertEqual(len(r["raw_registers"]),30)
        self.assertEqual(r["measurements"]["water_flow_c"],35)
        self.assertEqual(r["measurements"]["water_return_c"],37)
        self.assertEqual(r["measurements"]["energy_total_kwh"],722.55)
        self.assertFalse(r["profile_verified"])
        self.assertFalse(r["writes_performed"])
    def test_foreign_or_missing_port_no_reads(self):
        self.assertIsNone(self.sample({"device_uuid":"other","site_id":live.SITE_ID}))
        self.assertIsNone(self.sample(port=False))
        self.assertEqual(Client.calls,[])
    def test_busy_bus_does_not_wait(self):
        with self.lock:
            self.assertIsNone(self.sample())
        self.assertEqual(Client.calls,[])
    def test_failed_temperature_block_does_not_emit_stale_data(self):
        Client.fail={100}
        self.assertIsNone(self.sample())
    def test_partial_missing_power_block_does_not_make_up_values(self):
        Client.fail={138}
        r=self.sample()
        self.assertIsNone(r["measurements"]["electric_power_kw_candidate"])
        self.assertIsNone(r["measurements"]["energy_total_kwh"])
        self.assertEqual(r["measurements"]["water_flow_c"],35)

if __name__=="__main__":unittest.main()
