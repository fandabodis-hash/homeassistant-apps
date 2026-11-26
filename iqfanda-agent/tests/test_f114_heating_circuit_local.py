import copy, json, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import f114_heating_circuit_local as r
NOW=datetime(2026,11,25,20,30,tzinfo=timezone.utc)
IDENT={'device_uuid':r.UUID,'site_id':r.SITE,'serial_number':r.SERIAL}
CID='a'*32

def fixture():
    owner={'version':1,'phase':'shadow','identity':dict(r.IDENTITY),'commission_id':CID,'pump':{'entity_id':r.PUMP,'ieee':r.PUMP_IEEE,'device_reg_id':r.PUMP_REG},'room':{'entity_id':r.ROOM,'ieee':r.ROOM_IEEE,'device_reg_id':r.ROOM_REG},'weather':{'source':'open_meteo','latitude':50.0,'longitude':14.0},'secondary_circuit_verified':True,'exclusive_ownership_confirmed':True}
    d={'schema_version':1,'enabled':False,'timezone':'Europe/Prague','pump_mode':'auto','thermostat_mode':'schedule','levels_c':{'frost':7,'setback':21,'comfort':22,'boost':25},'season_mode':'auto','outdoor_threshold_c':17,'week':{'workdays':[{'at':'00:00','temperature_c':21},{'at':'02:30','temperature_c':22},{'at':'21:00','temperature_c':21.5},{'at':'22:00','temperature_c':21},{'at':'23:59','temperature_c':21}], 'weekend':[{'at':'00:00','temperature_c':21},{'at':'02:30','temperature_c':22},{'at':'21:00','temperature_c':21.5},{'at':'22:00','temperature_c':21},{'at':'23:59','temperature_c':21}]}}
    pts={'heating_pump_switch':{'mode':'entity','entity_id':r.PUMP},'indoor_temperature':{'mode':'entity','entity_id':r.ROOM},'heating_switch_2':{'mode':'none','entity_id':None},'heating_switch':{'mode':'entity','entity_id':'switch.sonoff_mini_zbd'}}
    binds={'heating-circulation-pump-state':{'entity_id':r.PUMP,'transport':'zigbee'},'hp-heating-switch-2':{'entity_id':r.PUMP,'transport':'zigbee'},'indoor-temperature':{'entity_id':r.ROOM,'transport':'zigbee'}}
    cfg={'generation':1,'client_visibility':True,'f49_r28_optional_points_v1':pts,'schema_entity_bindings_v1':binds,r.DRAFT:{'identity':dict(r.IDENTITY),'draft':d}}
    c={'device_uuid':r.UUID,'timezone':'Europe/Prague','settings':{r.KEY:owner},'module_runtime_configurations':[{'module_key':'heat_pump','module_type':'heat_pump','status':'configuration','configuration':cfg},{'module_key':'building','configuration':{}},{'module_key':'pv_surplus_control','configuration':{'targets':[{'output':{'reference':'switch.boiler'}}]}}]}
    return c,owner,d,cfg

class HA:
    def __init__(self): self.on=True;self.room=22.1;self.room_online=True;self.calls=[];self.automations=[];self.wrong=False;self.fail=False;self.stuck=False;self.stale=False;self.read_error=False
    def get_zha_devices(self):
        return [{'ieee':'bad' if self.wrong else r.PUMP_IEEE,'device_reg_id':r.PUMP_REG,'available':True,'manufacturer':'SONOFF','model':'ZBMINIR2','signature':{'endpoints':{'1':{'input_clusters':['0x0006']}}}},{'ieee':r.ROOM_IEEE,'device_reg_id':r.ROOM_REG,'available':self.room_online,'manufacturer':'eWeLink','model':'SNZB-02P','signature':{}}]
    def get_entity_registry_entries(self):return [{'entity_id':r.PUMP,'device_id':r.PUMP_REG,'platform':'zha','disabled_by':None},{'entity_id':r.ROOM,'device_id':r.ROOM_REG,'platform':'zha','disabled_by':None}]
    def home_assistant_request(self,*,path):return self.automations
    def get_entity_state(self,entity):
        if entity==r.PUMP:return {'entity_id':entity,'state':'on' if self.on else 'off'}
        if self.read_error:raise RuntimeError('sensor missing')
        return {'entity_id':entity,'state':str(self.room),'attributes':{'device_class':'temperature','unit_of_measurement':'°C'},'last_reported':(NOW-timedelta(hours=1) if self.stale else NOW-timedelta(seconds=30)).isoformat()}
    def call_binary_power_output_service(self,*,entity_id,desired_on):
        self.calls.append((entity_id,desired_on))
        if self.fail:raise RuntimeError('HA timeout')
        if not self.stuck:self.on=desired_on

class Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)/'status.json';self.c,self.o,self.d,self.cfg=fixture();self.h=HA();self.out=0.8;self.out_fresh=True
    def cycle(self):return r.cycle(IDENT,self.c,self.h,now=NOW,state_path=self.p,weather=lambda o,n:(self.out,self.out_fresh,0 if self.out_fresh else None),wait=lambda x:None)
    def shadow(self):
        x=self.cycle();self.assertEqual(x['status'],'shadow',x);self.assertTrue(x['shadow_ready'],x);self.assertEqual(self.h.calls,[])
    def arm(self):self.shadow();self.o['phase']='armed'
    def test_01_no_owner_no_actuation(self):self.c['settings']={};self.assertEqual(self.cycle()['status'],'not_commissioned');self.assertEqual(self.h.calls,[])
    def test_02_foreign(self):self.assertIsNone(r.cycle({'device_uuid':'other'},self.c,self.h,now=NOW,state_path=self.p));self.assertFalse(self.p.exists());self.assertEqual(self.h.calls,[])
    def test_03_wrong_ieee(self):self.h.wrong=True;self.assertEqual(self.cycle()['reason'],'zigbee_device_identity_changed');self.assertEqual(self.h.calls,[])
    def test_04_shadow_read_only(self):self.shadow()
    def test_05_arm_without_shadow(self):self.o['phase']='armed';self.assertEqual(self.cycle()['reason'],'no_matching_shadow_commissioning');self.assertEqual(self.h.calls,[])
    def test_06_screenshot_above_target(self):self.arm();s=self.cycle();self.assertEqual(s['target_c'],21.5);self.assertFalse(s['relay_on']);self.assertTrue(s['physical_control_active']);self.assertEqual(self.h.calls,[(r.PUMP,False)])
    def test_07_below_turn_on(self):self.h.on=False;self.h.room=21.2;self.arm();s=self.cycle();self.assertTrue(s['relay_on']);self.assertEqual(self.h.calls,[(r.PUMP,True)])
    def test_08_hysteresis_hold_off(self):self.h.on=False;self.h.room=21.4;self.arm();s=self.cycle();self.assertFalse(s['relay_on']);self.assertEqual(self.h.calls,[])
    def test_09_hysteresis_hold_on(self):self.h.room=21.4;self.arm();s=self.cycle();self.assertTrue(s['relay_on']);self.assertEqual(self.h.calls,[])
    def test_10_manual_over_threshold(self):self.arm();self.d['pump_mode']='manual';self.o['phase']='shadow';self.shadow();self.o['phase']='armed';self.h.on=False;self.out=25;s=self.cycle();self.assertTrue(s['relay_on']);self.assertEqual(self.h.calls,[(r.PUMP,True)])
    def test_11_off_without_weather_after_arm(self):self.arm();self.cycle();self.h.calls.clear();self.d['pump_mode']='off';self.out_fresh=False;self.h.room_online=False;self.h.read_error=True;s=self.cycle();self.assertTrue(s['physical_control_active']);self.assertFalse(s['relay_on']);self.assertEqual(self.h.calls,[])
    def test_12_manual_without_room_after_arm(self):self.arm();self.cycle();self.h.calls.clear();self.d['pump_mode']='manual';self.h.room_online=False;self.h.read_error=True;self.out_fresh=False;s=self.cycle();self.assertTrue(s['relay_on']);self.assertEqual(self.h.calls,[(r.PUMP,True)])
    def test_13_outdoor_equal_off(self):self.h.room=20;self.out=17;self.arm();s=self.cycle();self.assertFalse(s['relay_on']);self.assertEqual(s['reason'],'outdoor_threshold_auto_block')
    def test_14_weather_stale_first_arm(self):self.arm();self.out_fresh=False;s=self.cycle();self.assertFalse(s['physical_control_active']);self.assertEqual(self.h.calls,[])
    def test_15_stale_room_after_arm(self):self.h.room=20;self.arm();self.cycle();self.h.stale=True;s=self.cycle();self.assertFalse(s['relay_on']);self.assertEqual(s['reason'],'room_temperature_unavailable')
    def test_16_stale_weather_after_arm(self):self.h.room=20;self.arm();self.cycle();self.out_fresh=False;s=self.cycle();self.assertFalse(s['relay_on']);self.assertEqual(s['reason'],'outdoor_temperature_unavailable')
    def test_17_conflict_map(self):self.cfg['f49_r28_optional_points_v1']['heating_switch_2']={'mode':'entity','entity_id':r.PUMP};self.assertEqual(self.cycle()['reason'],'legacy_heating_2_not_disabled');self.assertEqual(self.h.calls,[])
    def test_18_other_module(self):self.c['module_runtime_configurations'][1]['configuration']={'bad':r.PUMP};self.assertEqual(self.cycle()['reason'],'other_module_references_pump')
    def test_19_local_automation(self):self.h.automations=[{'entity_id':'automation.test','state':'on'}];self.assertEqual(self.cycle()['reason'],'enabled_or_unknown_ha_automation_owner');self.assertEqual(self.h.calls,[])
    def test_20_readback_failure_latches(self):self.arm();self.h.stuck=True;s=self.cycle();self.assertTrue(s['locked']);n=len(self.h.calls);self.cycle();self.assertEqual(len(self.h.calls),n)
    def test_21_service_timeout_latches(self):self.arm();self.h.fail=True;self.assertTrue(self.cycle()['locked']);self.assertEqual(len(self.h.calls),1);self.cycle();self.assertEqual(len(self.h.calls),1)
    def test_22_external_override_latches(self):self.arm();self.cycle();self.h.on=True;s=self.cycle();self.assertEqual(s['reason'],'unexpected_external_relay_change');self.assertTrue(s['locked'])
    def test_23_repeated_cycles_no_duplicate(self):self.arm();self.cycle();self.cycle();self.cycle();self.assertEqual(len(self.h.calls),1)
    def test_24_changed_draft_before_arm(self):self.arm();self.d['levels_c']['comfort']=23;self.assertEqual(self.cycle()['reason'],'no_matching_shadow_commissioning');self.assertEqual(self.h.calls,[])
    def test_25_other_owner_identity(self):self.o['identity']['device_uuid']='other';self.assertEqual(self.cycle()['reason'],'owner_identity_invalid')
    def test_26_client_cannot_arm(self):self.d['enabled']=True;self.assertEqual(self.cycle()['reason'],'draft_must_not_arm')
    def test_27_disarm_no_write(self):self.arm();self.cycle();self.h.calls.clear();self.o['phase']='disarmed';s=self.cycle();self.assertFalse(s['owns_relay']);self.assertEqual(self.h.calls,[])
    def test_28_boost_explicit(self):self.d['thermostat_mode']='boost';self.h.on=False;self.h.room=22;self.arm();s=self.cycle();self.assertEqual(s['target_c'],25);self.assertTrue(s['relay_on'])
    def test_29_weather_cache_offline(self):
        q=Path(self.tmp.name)/'weather.json';data={'utc_offset_seconds':0,'current_units':{'temperature_2m':'°C'},'current':{'temperature_2m':0.8,'time':NOW.isoformat()}}
        with patch.object(r,'_weather_attempt',0.0):
            self.assertEqual(r.weather_value(self.o,NOW,fetch=lambda w:data,cache_path=q,monotonic=lambda:1000),(0.8,True,0));self.assertTrue(r.weather_value(self.o,NOW+timedelta(minutes=10),fetch=lambda w:(_ for _ in ()).throw(OSError()),cache_path=q,monotonic=lambda:1600)[1]);self.assertFalse(r.weather_value(self.o,NOW+timedelta(hours=3),fetch=lambda w:(_ for _ in ()).throw(OSError()),cache_path=q,monotonic=lambda:18000)[1])
    def test_30_guard_external_pump_only(self):
        calls=[]
        @r.guarded_ha_request
        def fn(**kwargs):calls.append(kwargs);return {}
        with patch.object(r,'owner_claimed',return_value=True):
            with self.assertRaises(r.Hold):fn(path='/services/switch/turn_on',method='POST',payload={'entity_id':r.PUMP})
            fn(path='/services/switch/turn_on',method='POST',payload={'entity_id':'switch.boiler'})
            with self.assertRaises(r.Hold):fn(path='/services/homeassistant/turn_off',method='POST',payload={'entity_id':'all'})
            r._LOCAL.pump_write=True
            try:fn(path='/services/switch/turn_off',method='POST',payload={'entity_id':r.PUMP})
            finally:r._LOCAL.pump_write=False
        self.assertEqual(len(calls),2)
    def test_31_no_owner_no_service_change(self):
        calls=[]
        @r.guarded_ha_request
        def fn(**kwargs):calls.append(kwargs)
        with patch.object(r,'owner_claimed',return_value=False):fn(path='/services/switch/turn_on',method='POST',payload={'entity_id':r.PUMP})
        self.assertEqual(len(calls),1)
    def test_32_runtime_restart_uses_persisted_state(self):self.arm();a=self.cycle();self.assertTrue(a['physical_control_active']);self.assertTrue(self.cycle()['physical_control_active']);self.assertEqual(len(self.h.calls),1)

    def test_33_shadow_without_approval_cannot_arm(self):
        self.o['secondary_circuit_verified']=False
        self.o['exclusive_ownership_confirmed']=False
        self.shadow()
        self.o['phase']='armed'
        s=self.cycle()
        self.assertEqual(s['reason'],'commissioning_approval_missing')
        self.assertFalse(s['physical_control_active'])
        self.assertEqual(self.h.calls,[])
    def test_34_pilot_prearm_write_cannot_bypass_lock(self):
        import threading
        entered=threading.Event(); called=threading.Event(); errors=[]
        @r.guarded_ha_request
        def fn(**kwargs):called.set()
        def worker():
            entered.set()
            try:fn(path='/services/switch/turn_on',method='POST',payload={'entity_id':r.PUMP})
            except r.Hold:errors.append('protected')
        owner=[False]; checked=threading.Event()
        def pilot():checked.set();return True
        with patch.object(r,'pilot_lock_required',side_effect=pilot),patch.object(r,'owner_claimed',side_effect=lambda:owner[0]):
            with r._LOCK:
                t=threading.Thread(target=worker);t.start();self.assertTrue(entered.wait(1));self.assertTrue(checked.wait(1));self.assertFalse(called.wait(.05));owner[0]=True
            t.join(2);self.assertFalse(t.is_alive())
        self.assertFalse(called.is_set());self.assertEqual(errors,['protected'])
    def test_35_version_status_matches_candidate(self):
        self.assertEqual(r.VERSION,'0.1.182')

if __name__=='__main__':unittest.main()
