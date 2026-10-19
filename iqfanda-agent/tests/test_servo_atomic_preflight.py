"""R122 read-only preflight regression tests for the exact model 00013."""
import unittest
from datetime import datetime, timedelta, timezone

from servo_atomic_preflight import safe_preflight, FLOOR, PUMP
from servo_timed_command_definition import SERIAL, UUID, IEEE, OPEN, CLOSE


class Fake:
    def __init__(self):
        self.states={OPEN:"off", CLOSE:"off", PUMP:"on", FLOOR:"37.0"}
        self.date=datetime.now(timezone.utc).isoformat()
        self.calls=[]
    def read(self,entity):
        self.calls.append(("read",entity))
        return {"state":self.states[entity],"last_reported":self.date}
    def registry(self,entity):
        self.calls.append(("registry",entity))
        return "ac89cb802d360fa4d08b89ea31673f76"
    def devices(self):
        self.calls.append(("devices",))
        return [{"ieee":IEEE,"manufacturer":"SONOFF","model":"MINI-ZB2GS",
                 "signature":{"endpoints":{
                     "1":{"input_clusters":[0,6,0xFC11]},
                     "2":{"input_clusters":[0,6,0xFC11]}}}}]
    def args(self):
        return dict(identity={"device_uuid":UUID},
                    payload={"serial":SERIAL,"target_c":25.0},
                    read_state=self.read,get_registry=self.registry,
                    get_zha_devices=self.devices)


class ReadOnlyTests(unittest.TestCase):
    def test_exact_model_ready_close(self):
        f=Fake()
        r=safe_preflight(**f.args())
        self.assertTrue(r["ready"])
        self.assertEqual(r["demand"],"close")
        self.assertEqual(r["actual_c"],37.0)
        self.assertFalse(r["actuation"])
        self.assertFalse(r["native_timed_off_verified"])
        self.assertFalse(any(x[0]=="write" for x in f.calls))
    def test_wrong_model_stops_before_reading(self):
        f=Fake();a=f.args();a["identity"]={"device_uuid":"other"}
        r=safe_preflight(**a)
        self.assertEqual(r["reason"],"wrong_device")
        self.assertEqual(f.calls,[])
    def test_missing_last_reported_fails(self):
        f=Fake();a=f.args()
        def no_report(e):
            r=f.read(e)
            if e==FLOOR:r.pop("last_reported")
            return r
        a["read_state"]=no_report
        r=safe_preflight(**a)
        self.assertEqual(r["reason"],"floor_last_reported_missing")
    def test_old_sensor_fails(self):
        f=Fake();f.date=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
        r=safe_preflight(**f.args())
        self.assertEqual(r["reason"],"floor_sensor_stale")
    def test_open_if_cold(self):
        f=Fake();f.states[FLOOR]="20.0"
        r=safe_preflight(**f.args())
        self.assertEqual(r["demand"],"open")
    def test_both_off_required(self):
        f=Fake();f.states[CLOSE]="on"
        r=safe_preflight(**f.args())
        self.assertEqual(r["reason"],"relay_not_both_off")
    def test_no_open_when_pump_off(self):
        f=Fake();f.states[PUMP]="off";f.states[FLOOR]="20.0"
        r=safe_preflight(**f.args())
        self.assertEqual(r["demand"],"hold_pump_off")
    def test_wrong_ieee(self):
        f=Fake();a=f.args();a["get_zha_devices"]=lambda: [{"ieee":"other"}]
        r=safe_preflight(**a)
        self.assertEqual(r["reason"],"zha_device_not_unique")
    def test_missing_zha_endpoint(self):
        f=Fake();a=f.args()
        def missing():
            d=f.devices();d[0]["signature"]["endpoints"].pop("2")
            return d
        a["get_zha_devices"]=missing
        r=safe_preflight(**a)
        self.assertEqual(r["reason"],"zha_endpoint_missing")
    def test_wrong_reg(self):
        f=Fake();a=f.args();a["get_registry"]=lambda e:e
        r=safe_preflight(**a)
        self.assertEqual(r["reason"],"relay_registry_mismatch")
    def test_no_additional_payload(self):
        f=Fake();a=f.args();a["payload"]["command"]="turn_on"
        r=safe_preflight(**a)
        self.assertEqual(r["reason"],"payload_contract_invalid")
        self.assertEqual(f.calls,[])


if __name__ == "__main__":
    unittest.main()
