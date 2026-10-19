"""F47 R120: mock-only checks; NO Zigbee, HA, or actuator I/O."""
import unittest
from datetime import datetime, timezone
from servo_atomic_timed_executor import execute, SafetyStop, COMMAND, SOURCE, FLOOR
from servo_timed_command_definition import SERIAL, UUID, IEEE, OPEN, CLOSE

class Fake:
    def __init__(self):
        self.state={OPEN:"off",CLOSE:"off",FLOOR:"35.5"}
        self.actions=[]
    def read(self,entity):
        return {"state":self.state[entity],"last_reported":datetime.now(timezone.utc).isoformat()}
    def timed(self,request):
        self.actions.append(("timed",request["command"],request["endpoint_id"]))
    def off(self,entity):
        self.actions.append(("off",entity))
        self.state[entity]="off"
    def args(self):
        return dict(
          identity={"device_uuid":UUID},
          payload={"serial":SERIAL,"device_uuid":UUID,"ieee":IEEE,
                   "command_type":COMMAND,"device_reg_id":"same","source":SOURCE,
                   "target_c":25.0,"seconds":1.0},
          read_state=self.read,get_registry=lambda e:"same",
          get_zha_devices=lambda:[{"ieee":IEEE,"manufacturer":"SONOFF","model":"MINI-ZB2GS"}],
          send_timed=self.timed,send_off=self.off,
          wait=lambda n:None,locally_commissioned=True,
        )
class UnitTests(unittest.TestCase):
    def test_timed_request_and_off(self):
        f=Fake()
        x=execute(**f.args())
        self.assertEqual(x["action"],"timed_pulse")
        self.assertEqual(x["direction"],"close")
        self.assertEqual(f.actions[0],("timed",66,2))
        self.assertIn(("off",CLOSE),f.actions)
        self.assertFalse(x["native_timed_off_verified"])
    def test_uncommissioned_no_io(self):
        f=Fake();a=f.args();a["locally_commissioned"]=False
        with self.assertRaises(SafetyStop):execute(**a)
        self.assertEqual(f.actions,[])
    def test_wrong_fanda(self):
        f=Fake();a=f.args();a["identity"]={"device_uuid":"00012"}
        with self.assertRaises(SafetyStop):execute(**a)
        self.assertEqual(f.actions,[])
    def test_servo_relay_on(self):
        f=Fake();f.state[CLOSE]="on"
        with self.assertRaises(SafetyStop):execute(**f.args())
        self.assertEqual(f.actions,[])
    def test_no_action_in_band(self):
        f=Fake();f.state[FLOOR]="25.1"
        self.assertEqual(execute(**f.args())["action"],"hold")
        self.assertEqual(f.actions,[])
    def test_bad_target(self):
        f=Fake();a=f.args();a["payload"]["target_c"]=50
        with self.assertRaises(SafetyStop):execute(**a)
        self.assertEqual(f.actions,[])
    def test_wrong_owner(self):
        f=Fake();a=f.args();a["payload"]["source"]="other"
        with self.assertRaises(SafetyStop):execute(**a)
        self.assertEqual(f.actions,[])
    def test_timed_service_failure_off(self):
        f=Fake();a=f.args()
        def fail(r):raise RuntimeError("not supported")
        a["send_timed"]=fail
        with self.assertRaises(SafetyStop):execute(**a)
        self.assertIn(("off",OPEN),f.actions)
        self.assertIn(("off",CLOSE),f.actions)

if __name__=="__main__": unittest.main()
