"""F47 R126 isolated ordinary 1s servo pulse tests. NO Zigbee access."""
import unittest
from datetime import datetime, timedelta, timezone

from servo_standard_pulse import (
    run, PulseStopped, COMMAND, SOURCE, FLOOR, PUMP
)
from servo_timed_command_definition import UUID, IEEE, SERIAL, OPEN, CLOSE

class Fixture:
    def __init__(self):
        self.states={OPEN:"off",CLOSE:"off",FLOOR:"37.0",PUMP:"on"}
        self.log=[]
        self.timestamp=datetime.now(timezone.utc).isoformat()
    def state(self,entity):
        self.log.append(("read",entity))
        return {"state":self.states[entity],"last_reported":self.timestamp}
    def registry(self,entity):return "reg-00013"
    def zha(self):
        return [{"ieee":IEEE,"model":"MINI-ZB2GS","manufacturer":"SONOFF",
                 "signature":{"endpoints":{
                     "1":{"input_clusters":[0,6,64529]},
                     "2":{"input_clusters":[0,6,64529]}}}}]
    def switch(self,entity,turn_on):
        self.log.append(("switch",entity,turn_on))
        self.states[entity]="on" if turn_on else "off"
    def args(self):
        return dict(
            identity={"device_uuid":UUID},
            payload={"serial":SERIAL,"device_uuid":UUID,"ieee":IEEE,
                     "device_reg_id":"reg-00013","target_c":25.0,
                     "seconds":1.0,"command_type":COMMAND,"source":SOURCE},
            read_state=self.state,get_registry=self.registry,
            get_zha_devices=self.zha,switch=self.switch,
            wait=lambda t:None,physical_protections_confirmed=True)

class PulseTests(unittest.TestCase):
    def test_close_on_then_both_off(self):
        f=Fixture();res=run(**f.args())
        self.assertEqual(res["direction"],"close")
        calls=[e for e in f.log if e[0]=="switch"]
        self.assertEqual(calls,[("switch",CLOSE,True),
                                ("switch",OPEN,False),("switch",CLOSE,False)])
        self.assertFalse(res["device_native_auto_off_verified"])
    def test_open(self):
        f=Fixture();f.states[FLOOR]="20.0"
        r=run(**f.args())
        self.assertEqual(r["direction"],"open")
        self.assertIn(("switch",OPEN,True),f.log)
    def test_hold(self):
        f=Fixture();f.states[FLOOR]="25.1"
        r=run(**f.args())
        self.assertEqual(r["action"],"hold")
        self.assertFalse(any(e[0]=="switch" for e in f.log))
    def test_pump_stopped_no_open(self):
        f=Fixture();f.states[FLOOR]="20";f.states[PUMP]="off"
        r=run(**f.args())
        self.assertEqual(r["action"],"hold_pump_off")
    def test_other_model_not_allowed(self):
        f=Fixture();args=f.args();args["identity"]={"device_uuid":"00012"}
        with self.assertRaises(PulseStopped):run(**args)
        self.assertEqual(f.log,[])
    def test_wrong_registry(self):
        f=Fixture();args=f.args();args["payload"]["device_reg_id"]="other"
        with self.assertRaises(PulseStopped):run(**args)
        self.assertFalse(any(e[0]=="switch" for e in f.log))
    def test_protections_gate(self):
        f=Fixture();args=f.args();args["physical_protections_confirmed"]=False
        with self.assertRaises(PulseStopped):run(**args)
        self.assertEqual(f.log,[])
    def test_on_channel_already_on(self):
        f=Fixture();f.states[OPEN]="on"
        with self.assertRaises(PulseStopped):run(**f.args())
        self.assertFalse(any(e[0]=="switch" for e in f.log))
    def test_old_sensor(self):
        f=Fixture();f.timestamp=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat()
        with self.assertRaises(PulseStopped):run(**f.args())
        self.assertFalse(any(e[0]=="switch" for e in f.log))
    def test_wrong_owner(self):
        f=Fixture();args=f.args();args["payload"]["source"]="manual"
        with self.assertRaises(PulseStopped):run(**args)
    def test_bad_target(self):
        f=Fixture();args=f.args();args["payload"]["target_c"]=45.0
        with self.assertRaises(PulseStopped):run(**args)
    def test_on_error_still_off(self):
        f=Fixture();args=f.args()
        def bad(entity,on):
            f.log.append(("switch",entity,on))
            if on:raise RuntimeError("on rejected")
            f.states[entity]="off"
        args["switch"]=bad
        with self.assertRaises(PulseStopped):run(**args)
        calls=[e for e in f.log if e[0]=="switch"]
        self.assertEqual(calls,[("switch",CLOSE,True),
                                ("switch",OPEN,False),("switch",CLOSE,False)])
    def test_off_error_rejects(self):
        f=Fixture();args=f.args()
        def bad(entity,on):
            f.log.append(("switch",entity,on))
            if not on and entity==CLOSE:raise RuntimeError("off failed")
            f.states[entity]="on" if on else "off"
        args["switch"]=bad
        with self.assertRaises(PulseStopped):run(**args)
    def test_bad_zha_signature(self):
        f=Fixture();args=f.args();args["get_zha_devices"]=lambda:[{"ieee":IEEE}]
        with self.assertRaises(PulseStopped):run(**args)
        self.assertFalse(any(e[0]=="switch" for e in f.log))

if __name__=="__main__":
    unittest.main()
