"""F47 R150: mock-only local CLOSE 10s, both OFF then observe 10s."""
import unittest
from datetime import datetime,timedelta,timezone
from servo_feedback_close_10 import run,PURPOSE
from servo_timed_command_definition import UUID,IEEE,SERIAL,OPEN,CLOSE
from servo_standard_pulse import PulseStopped,FLOOR,PUMP

class Rig:
    def __init__(self):
        self.states={OPEN:"off",CLOSE:"off",PUMP:"on"}
        self.events=[]
        self.temp=36.8
        self.age_s=3
        self.reg="verified-reg"
        self.zha=[{"ieee":IEEE,"model":"MINI-ZB2GS","manufacturer":"SONOFF",
                    "signature":{"endpoints":{"1":{"input_clusters":[6]},
                                              "2":{"input_clusters":[6]}}}}]
    def read(self,ent):
        if ent==FLOOR:
            return {"state":str(self.temp),
                    "last_reported":(datetime.now(timezone.utc)-timedelta(seconds=self.age_s)).isoformat()}
        return {"state":self.states[ent]}
    def switch(self,ent,on):
        self.events.append((ent,on))
        self.states[ent]="on" if on else "off"
    def wait(self,seconds):
        self.events.append(("wait",seconds))
        if seconds==10.0 and any(e==(CLOSE,False) for e in self.events):
            self.temp=36.4
    def args(self):
        return dict(identity={"device_uuid":UUID},
           payload={"serial":SERIAL,"device_uuid":UUID,"ieee":IEEE,
                    "device_reg_id":self.reg,"seconds":10.0,"purpose":PURPOSE,
                    "target_c":30.0},
           read_state=self.read,get_registry=lambda e:self.reg,
           get_zha_devices=lambda:self.zha,switch=self.switch,
           wait=self.wait,physical_endstops_confirmed=True)

class FeedbackCloseTests(unittest.TestCase):
    def test_close_10_off_then_wait_10(self):
        f=Rig();res=run(**f.args())
        self.assertEqual(res["seconds_requested"],10)
        self.assertEqual(res["observation_wait_s"],10)
        self.assertEqual(res["floor_c_before"],36.8)
        self.assertEqual(res["floor_c_after"],36.4)
        self.assertEqual(res["relays_after"],"off_off")
        self.assertNotIn((OPEN,True),f.events)
        self.assertLess(f.events.index((CLOSE,True)),f.events.index((CLOSE,False)))
        self.assertLess(f.events.index((CLOSE,False)),f.events.index(("wait",10.0),f.events.index((CLOSE,False))))
        self.assertEqual(f.states[OPEN],"off")
        self.assertEqual(f.states[CLOSE],"off")
    def test_wrong_model_no_io(self):
        f=Rig();a=f.args();a["identity"]={"device_uuid":"00012"}
        with self.assertRaises(PulseStopped):run(**a)
        self.assertEqual(f.events,[])
    def test_wrong_target_no_io(self):
        f=Rig();a=f.args();a["payload"]["target_c"]=38
        with self.assertRaises(PulseStopped):run(**a)
        self.assertEqual(f.events,[])
    def test_stale_sensor_no_io(self):
        f=Rig();f.age_s=400
        with self.assertRaises(PulseStopped):run(**f.args())
        self.assertEqual(f.events,[])
    def test_floor_over_40_5_no_io(self):
        f=Rig();f.temp=41
        with self.assertRaises(PulseStopped):run(**f.args())
        self.assertEqual(f.events,[])
    def test_pump_off_no_io(self):
        f=Rig();f.states[PUMP]="off"
        with self.assertRaises(PulseStopped):run(**f.args())
        self.assertEqual(f.events,[])
    def test_no_close_demand_no_io(self):
        f=Rig();f.temp=30.1
        with self.assertRaises(PulseStopped):run(**f.args())
        self.assertEqual(f.events,[])
    def test_relay_on_block(self):
        f=Rig();f.states[OPEN]="on"
        with self.assertRaises(PulseStopped):run(**f.args())
        self.assertEqual(f.events,[])
    def test_on_failure_still_offs_both(self):
        f=Rig()
        def switch(ent,on):
            f.events.append((ent,on))
            if on:raise OSError("network fails")
            f.states[ent]="off"
        a=f.args();a["switch"]=switch
        with self.assertRaises(PulseStopped):run(**a)
        self.assertIn((OPEN,False),f.events)
        self.assertIn((CLOSE,False),f.events)
    def test_off_failure_remains_error(self):
        f=Rig()
        def switch(ent,on):
            f.events.append((ent,on))
            if not on:raise OSError("radio lost")
            f.states[ent]="on"
        a=f.args();a["switch"]=switch
        with self.assertRaises(PulseStopped):run(**a)
        self.assertNotIn((OPEN,True),f.events)

if __name__=="__main__":
    unittest.main()
