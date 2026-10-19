"""F47 R127: pure mock tests; ZHA uncached temperature read only."""
import unittest
from datetime import datetime,timedelta,timezone
from servo_floor_fresh import read,safe_read,locate_sensor,MODEL_UUID,FLOOR,SensorNotFresh,classify_ha_read_error

REG="sensor-device-reg"
IEEE="aa:bb:cc:dd:ee:ff:00:11"

class Fixture:
    def __init__(self,age=600):
        self.age=age
        self.calls=[]
        self.sensor="37.2"
        self.raw="3710"
    def state(self,name):
        self.calls.append(("state",name))
        return {"state":self.sensor,
                "last_reported":(datetime.now(timezone.utc)-timedelta(seconds=self.age)).isoformat()}
    def reg(self,name):
        self.calls.append(("reg",name))
        return REG
    def zha(self):
        self.calls.append(("zha",))
        return [{"device_reg_id":REG,"ieee":IEEE,
                 "signature":{"endpoints":{"1":{"input_clusters":[0,1,1026]}}}}]
    def cluster(self,payload):
        self.calls.append(("read",payload))
        return self.raw
    def args(self):
        return dict(identity={"device_uuid":MODEL_UUID},get_state=self.state,
                    get_registry=self.reg,get_devices=self.zha,
                    read_cluster=self.cluster)

class SensorReadTests(unittest.TestCase):
    def test_fresh_state_no_zha_read(self):
        f=Fixture(age=20);r=read(**f.args())
        self.assertEqual(r["source"],"ha_last_reported")
        self.assertEqual(r["temperature_c"],37.2)
        self.assertFalse(any(x[0]=="read" for x in f.calls))
    def test_stale_state_gets_uncached_zha(self):
        f=Fixture(age=800);r=read(**f.args())
        self.assertTrue(r["fresh"])
        self.assertEqual(r["temperature_c"],37.1)
        self.assertEqual(r["source"],"direct_zha_uncached")
        calls=[x for x in f.calls if x[0]=="read"]
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1],{"ieee":IEEE,"endpoint_id":1,
                                    "cluster_id":1026,"cluster_type":"in","attribute":0})
    def test_wrong_model_does_not_probe(self):
        f=Fixture();a=f.args();a["identity"]={"device_uuid":"00012"}
        r=safe_read(**a);self.assertFalse(r["fresh"])
        self.assertEqual(f.calls,[])
    def test_unreachable_sensor_rejected(self):
        f=Fixture();a=f.args();a["read_cluster"]=lambda d:None
        r=safe_read(**a)
        self.assertFalse(r["fresh"])
        self.assertEqual(r["reason"],"floor_zha_direct_read_invalid_response")
    def test_wrong_reg_rejected(self):
        f=Fixture();a=f.args();a["get_devices"]=lambda:[{"device_reg_id":"other","ieee":IEEE}]
        r=safe_read(**a)
        self.assertEqual(r["reason"],"floor_zha_device_not_unique")
    def test_invalid_number_rejected(self):
        f=Fixture();f.raw="None";r=safe_read(**f.args())
        self.assertEqual(r["reason"],"floor_zha_direct_read_not_number")
    def test_zigbee_error_no_fake_freshness(self):
        f=Fixture();a=f.args()
        def fail(payload):
            raise TimeoutError("unreachable")
        a["read_cluster"]=fail
        r=safe_read(**a)
        self.assertFalse(r["fresh"])
        self.assertEqual(r["reason"],"floor_zha_direct_read_TimeoutError_zha_zigbee_timeout")
    def test_wrong_sensor_unit(self):
        f=Fixture();f.raw="371000";r=safe_read(**f.args())
        self.assertEqual(r["reason"],"floor_zha_direct_read_out_of_range")
    def test_fresh_direct_forced(self):
        f=Fixture(age=1);r=read(**f.args(),require_direct=True)
        self.assertTrue(r["direct_read"])
    def test_unmatched_endpoint(self):
        f=Fixture();a=f.args()
        a["get_devices"]=lambda:[{"device_reg_id":REG,"ieee":IEEE,
                                   "signature":{"endpoints":{"1":{"input_clusters":[6]}}}}]
        r=safe_read(**a)
        self.assertEqual(r["reason"],"floor_zha_temperature_endpoint_not_unique")

    def test_websocket_unsupported_sanitized(self):
        f=Fixture();a=f.args()
        def fail(payload):
            raise RuntimeError("Home Assistant WebSocket prikaz selhal: {'code': 'unknown_command', 'message': 'Unknown command 1 for user private@example.cz, token=abc123'}")
        a["read_cluster"]=fail
        result=safe_read(**a)
        self.assertEqual(result["reason"],"floor_zha_direct_read_RuntimeError_ws_command_unknown")
        self.assertNotIn("abc123",str(result))
        self.assertNotIn("private@",str(result))
    def test_unreachable_zha_device_category(self):
        self.assertEqual(classify_ha_read_error(RuntimeError("DeliveryError: failed to deliver packet")), "zha_zigbee_no_reply")
    def test_authorization_category(self):
        self.assertEqual(classify_ha_read_error(RuntimeError("not authorized as HA administrator")), "ws_not_authorized")
    def test_unsupported_attribute_category(self):
        self.assertEqual(classify_ha_read_error(RuntimeError("Unsupported attribute 0x0000")), "zha_attribute_unsupported")
    def test_unknown_error_does_not_copy_raw(self):
        phrase="username=testuser PRIVATE=SECRET!" 
        result=classify_ha_read_error(RuntimeError(phrase))
        self.assertEqual(result,"unknown_ha_error")
        self.assertNotIn("SECRET",result)

if __name__=="__main__":
    unittest.main()
