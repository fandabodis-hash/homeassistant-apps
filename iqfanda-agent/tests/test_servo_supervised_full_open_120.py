"""F47 R146: OPEN 120 seconds with sensor and pump safety gates; mock I/O."""
import unittest
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
from servo_supervised_full_open_120 import run,PURPOSE
from servo_timed_command_definition import UUID,IEEE,SERIAL,OPEN,CLOSE
from servo_standard_pulse import PulseStopped

class Rig:
    def __init__(self):
        self.states={OPEN:"off",CLOSE:"off",
                     "switch.sonoff_mini_zb2gs_spinac_5":"on"}
        self.floor=30.5
        self.sensor_age=2
        self.now=0.0
        self.calls=[]
        self.reg="verified-reg"
        self.zha=[{"ieee":IEEE,"model":"MINI-ZB2GS","manufacturer":"SONOFF",
            "signature":{"endpoints":{"1":{"input_clusters":[6]},
                                       "2":{"input_clusters":[6]}}}}]
    def read(self,e):
        if e=="sensor.owon_ths317_et_4":
            return {"state":str(self.floor),
                    "last_reported":(datetime.now(timezone.utc)
                        -timedelta(seconds=self.sensor_age)).isoformat()}
        return {"state":self.states[e]}
    def write(self,e,on):
        self.calls.append((e,on))
        self.states[e]="on" if on else "off"
    def wait(self,s):
        self.calls.append(("wait",s))
        self.now+=s
    def args(self):
        return dict(identity={"device_uuid":UUID},
            payload={"serial":SERIAL,"device_uuid":UUID,"ieee":IEEE,
                     "device_reg_id":self.reg,"seconds":120,
                     "purpose":PURPOSE},
            read_state=self.read,get_registry=lambda e:self.reg,
            get_zha_devices=lambda:self.zha,switch=self.write,
            wait=self.wait,physical_endstops_confirmed=True)
    def run(self,args=None):
        with patch("servo_supervised_full_open_120.time.monotonic",
                   side_effect=lambda:self.now):
            return run(**(self.args() if args is None else args))

class SupervisedOpen(unittest.TestCase):
    def test_120_second_open_both_off_and_temperature(self):
        f=Rig()
        def wait(sec):
            f.wait(sec)
            if f.now>=40:
                f.floor=39.2
        args=f.args();args["wait"]=wait
        result=f.run(args)
        self.assertEqual(result["seconds_requested"],120)
        self.assertEqual(result["direction"],"open")
        self.assertEqual(result["relays_after"],"off_off")
        self.assertEqual(result["floor_temperature_end_c"],39.2)
        self.assertIn((OPEN,True),f.calls)
        self.assertIn((OPEN,False),f.calls)
        self.assertIn((CLOSE,False),f.calls)
        self.assertNotIn((CLOSE,True),f.calls)
        self.assertEqual(f.states[OPEN],"off")
        self.assertEqual(f.states[CLOSE],"off")
    def test_wrong_model_no_motion(self):
        f=Rig();a=f.args();a["identity"]={"device_uuid":"00012"}
        with self.assertRaises(PulseStopped):f.run(a)
        self.assertEqual(f.calls,[])
    def test_only_120_seconds(self):
        f=Rig();a=f.args();a["payload"]["seconds"]=240
        with self.assertRaises(PulseStopped):f.run(a)
        self.assertEqual(f.calls,[])
    def test_close_is_on_blocks(self):
        f=Rig();f.states[CLOSE]="on"
        with self.assertRaises(PulseStopped):f.run()
        self.assertEqual(f.calls,[])
    def test_open_not_allowed_without_floor_pump(self):
        f=Rig();f.states["switch.sonoff_mini_zb2gs_spinac_5"]="off"
        with self.assertRaises(PulseStopped):f.run()
        self.assertEqual(f.calls,[])
    def test_floor_too_hot_blocks(self):
        f=Rig();f.floor=41
        with self.assertRaises(PulseStopped):f.run()
        self.assertEqual(f.calls,[])
    def test_floor_stale_blocks(self):
        f=Rig();f.sensor_age=900
        with self.assertRaises(PulseStopped):f.run()
        self.assertEqual(f.calls,[])
    def test_temperature_rises_too_high_during_motion_switches_off(self):
        f=Rig()
        def wait(sec):
            f.wait(sec)
            if f.now>=12:f.floor=41
        a=f.args();a["wait"]=wait
        with self.assertRaisesRegex(PulseStopped,"open_on_or_wait_failed"):f.run(a)
        self.assertIn((OPEN,False),f.calls)
        self.assertIn((CLOSE,False),f.calls)
        self.assertEqual(f.states[OPEN],"off")
    def test_on_command_error_attempts_off(self):
        f=Rig()
        def bad_write(e,on):
            f.calls.append((e,on))
            if on:raise OSError("switch API failure")
            f.states[e]="off"
        a=f.args();a["switch"]=bad_write
        with self.assertRaisesRegex(PulseStopped,"open_on_or_wait_failed"):f.run(a)
        self.assertIn((OPEN,False),f.calls)
        self.assertIn((CLOSE,False),f.calls)
    def test_readback_failure_not_success(self):
        f=Rig()
        def bad_write(e,on):
            f.calls.append((e,on))
            if not on:raise OSError("HA failure")
            f.states[e]="on"
        a=f.args();a["switch"]=bad_write
        with self.assertRaises(PulseStopped):f.run(a)
        self.assertNotIn((CLOSE,True),f.calls)

if __name__=="__main__":
    unittest.main()
