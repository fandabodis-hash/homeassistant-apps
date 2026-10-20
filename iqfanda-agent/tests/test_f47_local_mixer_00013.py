"""F47 R161: exact-model HA-only three-point mixing mock tests."""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import f47_local_mixer_00013 as m

class Rig:
    def __init__(self,temp=36.8,age=3):
        self.temp=temp
        self.age=age
        self.pump="on"
        self.states={m.OPEN:"off",m.CLOSE:"off"}
        self.writes=[]
        self.sleeps=[]
    def read(self,e):
        if e==m.FLOOR:
            return {"state":str(self.temp),"last_reported":
                (datetime.now(timezone.utc)-timedelta(seconds=self.age)).isoformat()}
        if e==m.PUMP:
            return {"state":self.pump}
        return {"state":self.states[e]}
    def registry(self,e):return m.REGISTRY
    def devices(self):
        return [{"ieee":m.IEEE,"model":"MINI-ZB2GS","manufacturer":"SONOFF",
                "signature":{"endpoints":{"1":{"input_clusters":[6]},
                                          "2":{"input_clusters":[6]}}}}]
    def switch(self,*,entity_id,desired_on):
        self.writes.append((entity_id,desired_on))
        self.states[entity_id]="on" if desired_on else "off"
    def sleep(self,s):
        self.sleeps.append(s)
    def kwargs(self):
        return {"identity":{"device_uuid":m.UUID},"read":self.read,
                "registry":self.registry,"devices":self.devices,
                "switch":self.switch,"wait":self.sleep}

class LocalMixerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config=patch.object(m,"CONFIG_PATH",Path(self.tmp.name)/"cfg.json")
        self.status=patch.object(m,"STATUS_PATH",Path(self.tmp.name)/"state.json")
        self.pulse=patch.object(m,"LAST_PULSE_PATH",Path(self.tmp.name)/"last.json")
        self.config.start();self.status.start();self.pulse.start()
        self.addCleanup(self.config.stop);self.addCleanup(self.status.stop)
        self.addCleanup(self.pulse.stop)
    def activate(self,rig=None):
        rig=rig or Rig()
        m.enable(identity={"device_uuid":m.UUID},
            payload={"serial":m.SERIAL,"mode":"local_ha_only_v1",
                "target_c":30.0,"pulse_seconds":3.0,"pause_seconds":15.0},
            read=rig.read,registry=rig.registry,devices=rig.devices)
        return rig
    def test_enable_is_read_only_and_exact(self):
        rig=self.activate()
        self.assertEqual(rig.writes,[])
        self.assertTrue(m.is_armed({"device_uuid":m.UUID}))
        self.assertFalse(m.is_armed({"device_uuid":"00012"}))
        cfg=json.loads(m.CONFIG_PATH.read_text())
        self.assertEqual(cfg["pulse_seconds"],3)
        self.assertEqual(cfg["pause_seconds"],15)
    def test_wrong_model_never_arms(self):
        rig=Rig()
        with self.assertRaises(m.LocalSafetyStop):
            m.enable(identity={"device_uuid":"00012"},
                payload={"serial":m.SERIAL,"mode":"local_ha_only_v1",
                    "target_c":30.0,"pulse_seconds":3.0,"pause_seconds":15.0},
                read=rig.read,registry=rig.registry,devices=rig.devices)
        self.assertEqual(rig.writes,[])
    def test_close_three_seconds_and_verify_off(self):
        rig=self.activate()
        result=m.cycle(**rig.kwargs())
        self.assertEqual(result["action"],"close")
        self.assertEqual(result["seconds"],3)
        self.assertIn(3.0,rig.sleeps)
        self.assertIn((m.CLOSE,True),rig.writes)
        self.assertIn((m.OPEN,False),rig.writes)
        self.assertIn((m.CLOSE,False),rig.writes)
        self.assertNotIn((m.OPEN,True),rig.writes)
        self.assertEqual(rig.states[m.CLOSE],"off")
        self.assertEqual(rig.states[m.OPEN],"off")
    def test_next_pulse_requires_new_owon_report(self):
        rig=self.activate()
        first=m.cycle(**rig.kwargs())
        self.assertEqual(first["action"],"close")
        original_on=sum(1 for e in rig.writes if e==(m.CLOSE,True))
        again=m.cycle(**rig.kwargs())
        self.assertEqual(again["action"],"hold_wait_new_owon_report")
        self.assertEqual(sum(1 for e in rig.writes if e==(m.CLOSE,True)),original_on)
    def test_floor_stale_is_safe_hold_in_main_loop(self):
        rig=self.activate(Rig(age=600))
        seen=[]
        def sleep(seconds):
            seen.append(seconds)
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            m.run_forever(read=rig.read,registry=rig.registry,devices=rig.devices,
                switch=rig.switch,sleep=sleep,
                load_identity=lambda:{"device_uuid":m.UUID})
        self.assertEqual(m._load(m.STATUS_PATH)["status"],"hold")
        self.assertNotIn((m.CLOSE,True),rig.writes)
        self.assertEqual(seen,[15.0])
    def test_open_when_too_cold(self):
        rig=self.activate(Rig(temp=28.0))
        result=m.cycle(**rig.kwargs())
        self.assertEqual(result["action"],"open")
        self.assertIn((m.OPEN,True),rig.writes)
        self.assertNotIn((m.CLOSE,True),rig.writes)
    def test_near_target_no_actuation(self):
        rig=self.activate(Rig(temp=30.1))
        self.assertEqual(m.cycle(**rig.kwargs())["action"],"hold_in_band")
        self.assertEqual(rig.writes,[])
    def test_stale_owon_blocks_actuation(self):
        rig=self.activate(Rig(temp=34,age=600))
        with self.assertRaisesRegex(m.LocalSafetyStop,"floor_stale"):
            m.cycle(**rig.kwargs())
        self.assertEqual(rig.writes,[])
    def test_high_temp_stops(self):
        rig=self.activate(Rig(temp=43))
        with self.assertRaises(m.LocalSafetyStop):
            m.cycle(**rig.kwargs())
        self.assertEqual(rig.writes,[])
    def test_pump_off_holds(self):
        rig=self.activate();rig.pump="off"
        self.assertEqual(m.cycle(**rig.kwargs())["action"],"hold_pump_off")
        self.assertEqual(rig.writes,[])
    def test_wrong_registration_blocks(self):
        rig=self.activate()
        args=rig.kwargs()
        args["registry"]=lambda x:"wrong"
        with self.assertRaisesRegex(m.LocalSafetyStop,"servo_registry_drift"):
            m.cycle(**args)
        self.assertEqual(rig.writes,[])
    def test_open_relay_active_no_motor_on(self):
        rig=self.activate();rig.states[m.OPEN]="on"
        with self.assertRaises(m.LocalSafetyStop):
            m.cycle(**rig.kwargs())
        self.assertEqual(rig.writes,[])
    def test_failed_off_latches(self):
        rig=self.activate()
        def fail_off(*,entity_id,desired_on):
            rig.writes.append((entity_id,desired_on))
            if not desired_on:
                rig.states[entity_id]="off"
                raise RuntimeError("ha_off_refused")
            rig.states[entity_id]="on"
        args=rig.kwargs();args["switch"]=fail_off
        with self.assertRaisesRegex(m.LocalSafetyStop,"off_command_failed"):
            m.cycle(**args)
        self.assertNotIn((m.OPEN,True),rig.writes)
    def test_startup_uses_only_local_ha_and_15s_pause(self):
        rig=self.activate()
        sleeps=[]
        def wait(seconds):
            sleeps.append(seconds)
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            m.run_forever(read=rig.read,registry=rig.registry,
                devices=rig.devices,switch=rig.switch,
                sleep=wait,load_identity=lambda:{"device_uuid":m.UUID})
        self.assertEqual(sleeps,[15.0])
        self.assertIn((m.CLOSE,True),rig.writes)
    def test_unfinished_pulse_does_not_resume(self):
        rig=self.activate()
        m._status("pulse_active",direction="close")
        sleeps=[]
        def wait(seconds):
            sleeps.append(seconds)
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            m.run_forever(read=rig.read,registry=rig.registry,
                devices=rig.devices,switch=rig.switch,
                sleep=wait,load_identity=lambda:{"device_uuid":m.UUID})
        self.assertEqual(m._load(m.STATUS_PATH)["status"],"locked")
        self.assertEqual(sleeps,[15.0])
        self.assertNotIn((m.CLOSE,True),rig.writes)

if __name__=="__main__": unittest.main()
