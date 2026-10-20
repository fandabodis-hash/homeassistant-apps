"""F47 R144: mock-only safety tests for supervised 240s CLOSE."""
import unittest
from unittest.mock import patch
from servo_supervised_full_close_240 import run, PURPOSE, SECONDS
from servo_timed_command_definition import UUID,IEEE,SERIAL,OPEN,CLOSE
from servo_standard_pulse import PulseStopped

class Rig:
    def __init__(self):
        self.states={OPEN:"off",CLOSE:"off"}
        self.calls=[]
        self.reg="verified-reg"
        self.zha=[{"ieee":IEEE,"model":"MINI-ZB2GS","manufacturer":"SONOFF",
            "signature":{"endpoints":{"1":{"input_clusters":[6]},
                                       "2":{"input_clusters":[6]}}}}]
    def read(self,e):
        return {"state":self.states[e]}
    def write(self,e,on):
        self.calls.append((e,on))
        self.states[e]="on" if on else "off"
    def args(self):
        return dict(identity={"device_uuid":UUID},
            payload={"serial":SERIAL,"device_uuid":UUID,"ieee":IEEE,
                     "device_reg_id":self.reg,"seconds":240,
                     "purpose":PURPOSE},
            read_state=self.read,
            get_registry=lambda e:self.reg,
            get_zha_devices=lambda:self.zha,
            switch=self.write,
            wait=lambda seconds:self.calls.append(("wait",seconds)),
            physical_endstops_confirmed=True)

class SupervisedFullClose240(unittest.TestCase):
    def test_full_240_close_and_both_off(self):
        f=Rig()
        result=run(**f.args())
        self.assertEqual(result["direction"],"close")
        self.assertEqual(result["seconds_requested"],240)
        self.assertEqual(result["relays_after"],"off_off")
        self.assertIn((CLOSE,True),f.calls)
        self.assertIn(("wait",240.0),f.calls)
        self.assertIn((CLOSE,False),f.calls)
        self.assertNotIn((OPEN,True),f.calls)
        self.assertEqual(f.states[OPEN],"off")
        self.assertEqual(f.states[CLOSE],"off")
        self.assertFalse(result["firmware_auto_off_verified"])
    def test_wrong_model_does_not_switch(self):
        f=Rig();args=f.args();args["identity"]={"device_uuid":"00012"}
        with self.assertRaises(PulseStopped):
            run(**args)
        self.assertEqual(f.calls,[])
    def test_only_exact_240_allowed(self):
        f=Rig();args=f.args();args["payload"]["seconds"]=121
        with self.assertRaises(PulseStopped):run(**args)
        self.assertEqual(f.calls,[])
    def test_no_endstops_no_motion(self):
        f=Rig();args=f.args();args["physical_endstops_confirmed"]=False
        with self.assertRaises(PulseStopped):run(**args)
        self.assertEqual(f.calls,[])
    def test_open_is_not_off_blocks(self):
        f=Rig();f.states[OPEN]="on"
        with self.assertRaises(PulseStopped):run(**f.args())
        self.assertEqual(f.calls,[])
    def test_wrong_imei_or_registration_blocks(self):
        f=Rig();args=f.args();args["payload"]["ieee"]="00:00:00:00:00:00:00:00"
        with self.assertRaises(PulseStopped):run(**args)
        self.assertEqual(f.calls,[])
    def test_on_command_failure_attempts_both_off(self):
        f=Rig()
        def fail_on(e,on):
            f.calls.append((e,on))
            if on:raise IOError("no switch on reply")
            f.states[e]="off"
        args=f.args();args["switch"]=fail_on
        with self.assertRaisesRegex(PulseStopped,"close_on_or_wait_failed"):run(**args)
        self.assertIn((CLOSE,False),f.calls)
        self.assertNotIn((OPEN,True),f.calls)
    def test_off_command_failure_is_locked(self):
        f=Rig()
        def fail_off(e,on):
            f.calls.append((e,on))
            if not on:raise IOError("HA down")
            f.states[e]="on"
        args=f.args();args["switch"]=fail_off
        with self.assertRaises(PulseStopped):run(**args)
        self.assertNotIn((OPEN,True),f.calls)

if __name__=="__main__":
    unittest.main()
