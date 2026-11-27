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
        return {'entity_id':entity,'state':str(self.room),'attributes':{'device_class':'temperature','unit_of_measurement':'°C'},'last_reported':(NOW-timedelta(seconds=3601) if self.stale else NOW-timedelta(seconds=30)).isoformat()}
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
    def test_15_stale_room_after_arm(self):
        self.h.room=20;self.arm();self.cycle();self.h.stale=True;s=self.cycle()
        self.assertTrue(s['relay_on']);self.assertTrue(s['room_error']);self.assertEqual(s['room_display'],'ER');self.assertEqual(s['room_c'],20)
        self.assertTrue(s['room_using_last_value'])
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
        self.assertEqual(r.VERSION,'0.1.183')

    def report(self,value=22.5,age=0,entity=None):
        return {'entity_id':entity or r.ROOM,'state':str(value),'attributes':{'device_class':'temperature','unit_of_measurement':'°C'},'last_reported':(NOW-timedelta(seconds=age)).isoformat()}
    def test_36_exact_60_min_is_numeric(self):
        x=r.room_observation(self.report(age=3600),NOW,{})
        self.assertFalse(x['room_error']);self.assertEqual(x['room_display'],22.5)
    def test_37_60_min_plus_1_is_er_and_last_numeric(self):
        x=r.room_observation(self.report(age=3601),NOW,{})
        self.assertTrue(x['room_error']);self.assertEqual(x['room_display'],'ER');self.assertEqual(x['room_c'],22.5)
        self.assertTrue(x['room_using_last_value']);self.assertEqual(x['room_age_s'],3601)
    def test_38_no_data_no_synthetic_value(self):
        x=r.room_observation(None,NOW,{})
        self.assertEqual(x['room_display'],'ER');self.assertIsNone(x['room_c']);self.assertFalse(x['room_using_last_value'])
    def test_39_cached_value_survives_missing_read(self):
        p=r.room_observation(self.report(20,60),NOW,{})
        x=r.room_observation(None,NOW+timedelta(hours=2),dict(p,identity=r.IDENTITY))
        self.assertEqual(x['room_c'],20);self.assertEqual(x['room_display'],'ER');self.assertEqual(x['room_age_s'],7260)
        self.assertEqual(x['room_last_valid_utc'],p['room_last_valid_utc'])
    def test_40_new_measurement_automatically_clears_er(self):
        p=r.room_observation(self.report(20,7200),NOW,{})
        x=r.room_observation(self.report(22.5,0),NOW,dict(p,identity=r.IDENTITY))
        self.assertFalse(x['room_error']);self.assertEqual(x['room_display'],22.5);self.assertFalse(x['room_using_last_value'])
    def test_41_other_device_cache_never_used(self):
        p=r.room_observation(self.report(),NOW,{})
        x=r.room_observation(None,NOW,dict(p,identity={'serial_number':'foreign'}))
        self.assertIsNone(x['room_c']);self.assertEqual(x['room_display'],'ER')
    def test_42_wrong_entity_invalid_value_or_future_never_seed(self):
        for obj in (self.report(entity='sensor.other'),self.report('nan'),self.report(age=-3600),self.report('unknown')):
            self.assertIsNone(r.room_observation(obj,NOW,{})['room_c'])
    def test_43_last_seen_or_heartbeat_never_substitutes_for_measurement(self):
        p=self.report();p.pop('last_reported');p['last_seen']=NOW.isoformat()
        self.assertIsNone(r.room_observation(p,NOW,{})['room_c'])
    def test_44_older_or_contradictory_measurement_never_overwrites(self):
        p=dict(r.room_observation(self.report(20,20),NOW,{}),identity=r.IDENTITY)
        for obj in (self.report(30,30),self.report(30,20)):
            x=r.room_observation(obj,NOW,p);self.assertEqual(x['room_c'],20);self.assertTrue(x['room_error'])
    def test_45_stale_first_shadow_computes_but_never_arms(self):
        self.h.room=22.5;self.h.stale=True;s=self.cycle()
        self.assertEqual(s['room_display'],'ER');self.assertEqual(s['room_c'],22.5)
        self.assertEqual(s['reason'],'target_reached');self.assertFalse(s['shadow_ready']);self.assertEqual(self.h.calls,[])
        self.o['phase']='armed';s=self.cycle();self.assertFalse(s['physical_control_active']);self.assertEqual(self.h.calls,[])
    def test_46_owned_cached_value_controls_new_target_with_er(self):
        self.h.room=22.5;self.arm();self.cycle();self.h.calls.clear();self.h.read_error=True
        self.d['thermostat_mode']='comfort';self.d['levels_c']['comfort']=24
        s=self.cycle();self.assertEqual(s['room_display'],'ER');self.assertEqual(s['room_c'],22.5)
        self.assertTrue(s['relay_on']);self.assertEqual(self.h.calls,[(r.PUMP,True)])
    def test_47_weather_loss_policy_unchanged_even_with_room_cache(self):
        self.h.room=20;self.arm();self.cycle();self.h.read_error=True;self.out_fresh=False;s=self.cycle()
        self.assertEqual(s['room_display'],'ER');self.assertFalse(s['relay_on']);self.assertEqual(s['reason'],'outdoor_temperature_unavailable')
    def test_48_cached_reading_preserved_on_hold_not_used_for_actuation(self):
        self.h.room=20;self.shadow();self.h.wrong=True;s=self.cycle()
        self.assertEqual(s['room_last_valid_c'],20);self.assertEqual(s['room_display'],'ER');self.assertEqual(self.h.calls,[])
    def test_49_ui_status_exports_error_and_last_measurement(self):
        self.h.stale=True;s=self.cycle()
        with patch.object(r,'STATE_PATH',self.p):v=r.get_local_status(IDENT)
        self.assertEqual(v['room_display'],'ER');self.assertEqual(v['room_c'],s['room_c']);self.assertTrue(v['room_error']);self.assertIn('room_last_valid_utc',v)

    def expired_weather_cycle(self,age=7201):
        return r.cycle(IDENT,self.c,self.h,now=NOW,state_path=self.p,weather=lambda o,n:(None,False,age),wait=lambda x:None)
    def test_50_cache_exact_two_hours_and_expired_age(self):
        q=Path(self.tmp.name)/'weather-r32.json'
        r.atomic(q,{'key':r.revision(self.o['weather']),'temperature_c':18,'observed_utc':(NOW-timedelta(seconds=7200)).isoformat()})
        with patch.object(r,'_weather_attempt',1000.0):
            self.assertEqual(r.weather_value(self.o,NOW,cache_path=q,monotonic=lambda:1001),(18,True,7200))
            self.assertEqual(r.weather_value(self.o,NOW+timedelta(microseconds=1),cache_path=q,monotonic=lambda:1001),(None,False,7201))
    def test_51_no_cache_or_wrong_site_or_invalid_reading_not_expired_proof(self):
        q=Path(self.tmp.name)/'weather-r32.json'
        with patch.object(r,'_weather_attempt',1000.0):
            self.assertEqual(r.weather_value(self.o,NOW,cache_path=q,monotonic=lambda:1001),(None,False,None))
            for key,temp in [('foreign',18),(r.revision(self.o['weather']),999)]:
                r.atomic(q,{'key':key,'temperature_c':temp,'observed_utc':(NOW-timedelta(hours=3)).isoformat()})
                self.assertEqual(r.weather_value(self.o,NOW,cache_path=q,monotonic=lambda:1001),(None,False,None))
    def test_52_expired_outdoor_after_arm_fresh_indoor_turn_on(self):
        self.h.on=False;self.h.room=20;self.out=18;self.arm();self.cycle();self.h.calls.clear()
        s=self.expired_weather_cycle();self.assertTrue(s['relay_on']);self.assertTrue(s['outdoor_fallback_active']);self.assertEqual(self.h.calls,[(r.PUMP,True)])
        self.assertEqual(s['outdoor_policy'],'expired_cache');self.assertIsNone(s['outdoor_c'])
    def test_53_expired_outdoor_above_target_no_heat(self):
        self.h.room=22.5;self.arm();self.cycle();self.h.calls.clear();s=self.expired_weather_cycle()
        self.assertTrue(s['outdoor_fallback_active']);self.assertFalse(s['relay_on']);self.assertEqual(self.h.calls,[])
    def test_54_weather_recovers_threshold_applied(self):
        self.h.room=20;self.arm();self.cycle();self.expired_weather_cycle();self.out=18;self.h.calls.clear();s=self.cycle()
        self.assertFalse(s['relay_on']);self.assertFalse(s['outdoor_fallback_active']);self.assertEqual(self.h.calls,[(r.PUMP,False)])
    def test_55_both_inputs_stale_no_fallback(self):
        self.h.room=20;self.arm();self.cycle();self.h.read_error=True;s=self.expired_weather_cycle()
        self.assertEqual(s['room_display'],'ER');self.assertFalse(s['outdoor_fallback_active']);self.assertFalse(s['relay_on'])
        self.assertEqual(s['reason'],'outdoor_temperature_unavailable')
    def test_56_expired_weather_cannot_authorize_first_arm(self):
        self.h.room=20;s=self.expired_weather_cycle();self.assertTrue(s['outdoor_fallback_active']);self.assertFalse(s['shadow_ready']);self.assertEqual(self.h.calls,[])
        self.o['phase']='armed';s=self.expired_weather_cycle();self.assertFalse(s['physical_control_active']);self.assertEqual(self.h.calls,[])
    def test_57_weather_expires_between_shadow_and_arm(self):
        self.h.room=20;self.arm();s=self.expired_weather_cycle();self.assertFalse(s['physical_control_active']);self.assertEqual(self.h.calls,[])
    def test_58_expired_weather_explicit_summer(self):
        self.h.room=20;self.arm();self.cycle();self.d['season_mode']='summer';s=self.expired_weather_cycle()
        self.assertFalse(s['relay_on']);self.assertFalse(s['outdoor_fallback_active']);self.assertEqual(s['reason'],'summer_auto_block')
    def test_59_expired_weather_status_export(self):
        self.h.room=20;self.arm();self.cycle();self.expired_weather_cycle()
        with patch.object(r,'STATE_PATH',self.p):s=r.get_local_status(IDENT)
        self.assertTrue(s['outdoor_fallback_active']);self.assertEqual(s['outdoor_age_s'],7201);self.assertEqual(s['outdoor_policy'],'expired_cache')
    def test_60_weather_unknown_age_never_invents_fallback(self):
        self.h.room=20;self.arm();self.cycle();s=self.expired_weather_cycle(None)
        self.assertFalse(s['outdoor_fallback_active']);self.assertFalse(s['relay_on'])

if __name__=='__main__':unittest.main()
