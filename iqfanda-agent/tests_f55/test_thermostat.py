import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import thermostat_engine as e
import thermostat_runtime as r

NOW = datetime(2026, 11, 6, 8, 55, tzinfo=timezone.utc)
REF = 'switch.test_heat'
TID = '00000000-0000-0000-0000-000000000014'

def fixture():
    role = {'status': 'verified', 'role': 'thermostat_permission', 'reference': REF, 'device_reg_id': 'verified_registry_00014'}
    target = {'id': TID, 'type':'generic_load', 'enabled':True,'configuration_status':'verified',
              'output': {'status':'verified','reference':REF}, 'weekly_control_output':role,
              'sensors':[{'role':'temperature','status':'verified','reference':'sensor.room_temperature'}],
              'conditions': {'minimum_temperature_c':0,'maximum_temperature_c':95,'hysteresis_c':0.2}}
    program = {'schema_version':1,'enabled':True,'timezone':'Europe/Prague',
               'levels_c':{'frost':7,'setback':20,'comfort':23.5,'boost':25},
               'week': {d:[{'at':'06:00','level':'comfort'},{'at':'08:00','level':'setback'},
                           {'at':'14:00','level':'comfort'},{'at':'21:00','level':'setback'}] for d in e.DAYS}}
    cfg = {'actuation_enabled':True, 'targets':[target], 'weekly_temperature_programs':{TID:program},
           'surplus_source':{'type':'battery_soc','confirmation_seconds':30,
                             'battery_soc':{'enable_soc_percent':90,'disable_soc_percent':80}}}
    cloud={'device_uuid':'local-test-device','config_version':7,
           'module_runtime_configurations':[{'module_key':'pv_surplus_control','configuration':cfg}]}
    return cloud, {'device_uuid':'local-test-device'}, cfg

def contract():
    cloud, ident, _ = fixture()
    return e.compile_targets(cloud, ident, [TID])[0]

def ready(latch=False, boost=False):
    s=e.initial_state(0);s.update(temperature_demand=latch,boost=boost,confirmation_since=0)
    return s

def decision(temp=19.7, state=None, **kw):
    args={'now':NOW,'monotonic':1000,'temperature':temp,'sensor_fresh':True,'actual_on':False,'soc':12,'soc_fresh':True}
    args.update(kw)
    return e.decide(contract(), ready() if state is None else state, **args)[0]

class Contracts(unittest.TestCase):
    def test_valid_actual_shape(self): self.assertEqual(contract()['outputs'][0]['reference'],REF)
    def test_foreign_device(self):
        c,i,_=fixture();i['device_uuid']='foreign'
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_explicit_selection_required(self):
        c,i,_=fixture()
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[])
    def test_duplicate_target_selection(self):
        c,i,_=fixture()
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID,TID])
    def test_unknown_target(self):
        c,i,_=fixture()
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,['unknown'])
    def test_disabled_actuation(self):
        c,i,g=fixture();g['actuation_enabled']=False
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_unverified_target(self):
        c,i,g=fixture();g['targets'][0]['configuration_status']='configuration'
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_non_generic_not_auto_migrated(self):
        c,i,g=fixture();g['targets'][0]['type']='domestic_hot_water'
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_disabled_profile(self):
        c,i,g=fixture();g['weekly_temperature_programs'][TID]['enabled']=False
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_orphan_does_not_replace_current_profile(self):
        c,i,g=fixture();g['weekly_temperature_programs']['orphan']=copy.deepcopy(g['weekly_temperature_programs'][TID]);g['weekly_temperature_programs']['orphan']['levels_c']['setback']=30
        self.assertEqual(e.compile_targets(c,i,[TID])[0]['program']['levels_c']['setback'],20)
    def test_shared_output(self):
        c,i,g=fixture();x=copy.deepcopy(g['targets'][0]);x['id']='other';g['targets'].append(x)
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_mismatched_binding(self):
        c,i,g=fixture();g['targets'][0]['weekly_control_output']['reference']='switch.other'
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_missing_hardware_identity(self):
        c,i,g=fixture();g['targets'][0]['weekly_control_output']['device_reg_id']=''
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_three_outputs(self):
        c,i,g=fixture();t=g['targets'][0];t['weekly_control_outputs']=[copy.deepcopy(t['weekly_control_output']) for _ in range(3)]
        for n in (1,2):t['weekly_control_outputs'][n]['reference']='switch.extra_'+str(n)
        self.assertEqual(len(e.compile_targets(c,i,[TID])[0]['outputs']),3)
    def test_four_outputs_rejected(self):
        c,i,g=fixture();t=g['targets'][0];t['weekly_control_outputs']=[t['weekly_control_output']]*4
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_duplicate_output_rejected(self):
        c,i,g=fixture();t=g['targets'][0];t['weekly_control_outputs']=[t['weekly_control_output']]*2
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_safety_bounds_preserved(self):
        c,i,g=fixture();g['targets'][0]['conditions']['maximum_temperature_c']=22
        with self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_invalid_hysteresis(self):
        for bad in (True,0,-1,2,float('nan')):
            c,i,g=fixture();g['targets'][0]['conditions']['hysteresis_c']=bad
            with self.subTest(bad=bad),self.assertRaises(e.ContractError):e.compile_targets(c,i,[TID])
    def test_per_target_soc(self):
        c,i,g=fixture();g['weekly_temperature_programs'][TID]['boost_soc']={'enable_percent':70,'disable_percent':60}
        self.assertEqual(e.compile_targets(c,i,[TID])[0]['soc_on'],70)
    def test_no_input_mutation(self):
        c,i,_=fixture();before=copy.deepcopy(c);e.compile_targets(c,i,[TID]);self.assertEqual(before,c)

class Decisions(unittest.TestCase):
    def test_low_soc_does_not_block_heating(self):self.assertTrue(decision()['desired_on'])
    def test_no_soc_does_not_block_heating(self):self.assertTrue(decision(soc=None,soc_fresh=False)['desired_on'])
    def test_198_starts(self):self.assertTrue(decision(19.8)['desired_on'])
    def test_199_off_latch_holds(self):self.assertFalse(decision(19.9)['desired_on'])
    def test_199_on_latch_holds(self):self.assertTrue(decision(19.9,ready(True))['desired_on'])
    def test_200_stops(self):self.assertFalse(decision(20,ready(True),actual_on=True)['desired_on'])
    def test_safety_overheat(self):self.assertEqual(decision(95,actual_on=True)['reason'],'safety_maximum_reached')
    def test_stale_temperature(self):self.assertFalse(decision(sensor_fresh=False,actual_on=True)['desired_on'])
    def test_missing_temperature(self):self.assertFalse(decision(None,actual_on=True)['desired_on'])
    def test_invalid_temperature(self):self.assertFalse(decision(float('nan'),actual_on=True)['desired_on'])
    def test_safety_interlock(self):self.assertFalse(decision(safety_ok=False,actual_on=True)['desired_on'])
    def test_soc_boost(self):self.assertEqual(decision(21,soc=90)['active_level'],'boost')
    def test_boost_hold(self):self.assertTrue(decision(21,ready(boost=True),soc=85)['boost'])
    def test_boost_stop(self):self.assertFalse(decision(21,ready(boost=True),soc=80)['boost'])
    def test_stale_soc_removes_boost_only(self):self.assertFalse(decision(19.7,ready(boost=True),soc=99,soc_fresh=False)['boost'])
    def test_restart_minimum_off(self):self.assertFalse(decision(state=e.initial_state(1000))['desired_on'])
    def test_minimum_off_not_bypassed_by_frost(self):self.assertFalse(decision(0,state=e.initial_state(1000))['desired_on'])
    def test_confirmation_pending(self):
        s=ready();s['confirmation_since']=None
        self.assertEqual(decision(state=s)['reason'],'confirmation_pending')
    def test_confirmed_after_30(self):
        c=contract();s=ready();s['confirmation_since']=None
        _,s=e.decide(c,s,now=NOW,monotonic=1000,temperature=19,sensor_fresh=True,actual_on=False)
        self.assertTrue(e.decide(c,s,now=NOW,monotonic=1030,temperature=19,sensor_fresh=True,actual_on=False)[0]['desired_on'])
    def test_monotonic_rollback_resets(self):
        s=ready(True);s['last_mono']=2000
        self.assertFalse(decision(state=s)['desired_on'])
    def test_clock_timezone_required(self):
        with self.assertRaises(e.ContractError):decision(now=datetime(2026,11,6))
    def test_off_timer_observed(self):
        s=ready(True);s['actual_on']=True
        self.assertFalse(decision(state=s,actual_on=False)['desired_on'])
    def test_time_program_14(self):self.assertEqual(decision(now=NOW.replace(hour=13,minute=0))['requested_c'],23.5)
    def test_previous_day_carry(self):self.assertEqual(decision(now=NOW.replace(hour=0,minute=0))['requested_c'],20)
    def test_spring_dst(self):self.assertEqual(e.schedule_level(contract()['program'],datetime(2026,3,29,1,30,tzinfo=timezone.utc)),'setback')
    def test_autumn_dst_repeated_hour(self):
        for h in (0,1):self.assertEqual(e.schedule_level(contract()['program'],datetime(2026,10,25,h,30,tzinfo=timezone.utc)),'setback')
    def test_duplicate_schedule_times(self):
        p=contract()['program'];p['week']['mon'].append(copy.deepcopy(p['week']['mon'][0]))
        with self.assertRaises(e.ContractError):e.validate_program(p)
    def test_empty_week(self):
        p=contract()['program'];p['week']={d:[] for d in e.DAYS}
        with self.assertRaises(e.ContractError):e.validate_program(p)
    def test_unknown_output_blocks_all_on(self):
        plan=e.action_plan(contract(),{'desired_on':True},{REF:'unavailable'})
        self.assertEqual(plan[0]['target_state'],'off')
    def test_state_not_mutated(self):
        s=ready();before=copy.deepcopy(s);decision(state=s);self.assertEqual(s,before)

class RuntimeSafety(unittest.TestCase):
    def setUp(self):
        self.old=copy.deepcopy(r.OWNERS);r.OWNERS.clear();r.OWNERS[REF]={'device_reg_id':'verified_registry_00014','target_id':TID}
        self.calls=[]
        self.guarded=r.request_guard(lambda **k:self.calls.append(k))
    def tearDown(self):r.OWNERS.clear();r.OWNERS.update(self.old)
    def test_legacy_on_rejected(self):
        with self.assertRaises(e.ContractError):self.guarded(path='/services/switch/turn_on',method='POST',payload={'entity_id':REF})
        self.assertFalse(self.calls)
    def test_legacy_off_rejected(self):
        with self.assertRaises(e.ContractError):self.guarded(path='/services/switch/turn_off',method='POST',payload={'entity_id':REF})
    def test_broad_request_rejected(self):
        with self.assertRaises(e.ContractError):self.guarded(path='/services/homeassistant/turn_off',method='POST',payload={'area_id':'room'})
    def test_other_output_unchanged(self):
        self.guarded(path='/services/switch/turn_on',method='POST',payload={'entity_id':'switch.other'});self.assertEqual(len(self.calls),1)
    def test_sensor_reads_allowed(self):
        self.guarded(path='/states/'+REF);self.assertEqual(len(self.calls),1)
    def test_local_writer_allowed(self):
        token=r.LOCAL_CALL.set(True)
        try:self.guarded(path='/services/switch/turn_on',method='POST',payload={'entity_id':REF})
        finally:r.LOCAL_CALL.reset(token)
        self.assertEqual(len(self.calls),1)
    def test_source_timestamp_unchanged_value(self):
        state={'state':'19.9','last_updated':(NOW-timedelta(days=1)).isoformat(),'last_reported':NOW.isoformat(),'attributes':{'unit_of_measurement':'°C'}}
        self.assertEqual(r.sensor_value(state,1800,NOW),19.9)
    def test_old_measurement_rejected(self):
        state={'state':'19.9','last_updated':(NOW-timedelta(hours=2)).isoformat(),'attributes':{'unit_of_measurement':'°C'}}
        with self.assertRaises(e.ContractError):r.sensor_value(state,1800,NOW)
    def test_future_measurement_rejected(self):
        state={'state':'19.9','last_reported':(NOW+timedelta(hours=1)).isoformat(),'attributes':{'unit_of_measurement':'°C'}}
        with self.assertRaises(e.ContractError):r.sensor_value(state,1800,NOW)
    def test_non_celsius_rejected(self):
        state={'state':'70','last_reported':NOW.isoformat(),'attributes':{'unit_of_measurement':'°F'}}
        with self.assertRaises(e.ContractError):r.sensor_value(state,1800,NOW)
    def test_atomic_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'snapshot.json';r.atomic_json(p,{'version':1});r.atomic_json(p,{'version':2});self.assertEqual(r.read_json(p),{'version':2});self.assertEqual(p.stat().st_mode&0o777,0o600)
    def test_network_outage_does_not_enter_engine(self):
        with patch('urllib.request.urlopen', side_effect=OSError('internet disconnected')):
            self.assertTrue(decision()['desired_on'])
    def test_no_bus_read_for_soc(self):
        r.observe_soc([{'entity_key':'baterie.soc','quality':'good','value':55}]);self.assertEqual(r.SOC['value'],55)
    def test_invalid_soc_not_cached(self):
        r.SOC['value']=55;r.observe_soc([{'entity_key':'baterie.soc','quality':'bad','value':99}]);self.assertEqual(r.SOC['value'],55)

if __name__=='__main__':unittest.main(verbosity=2)

# Regression tests for the runtime adapter (all hardware functions are mocked).
class ActuationSafety(unittest.TestCase):
    def setUp(self):
        import sys, types
        self.states = {REF: 'off', 'switch.test_aux': 'off'}
        self.calls = []
        self.fail_on = None
        self.bad_registry = None
        self.zm = types.SimpleNamespace()
        self.zm.get_entity_device_id = lambda *, entity_id: ('WRONG' if entity_id == self.bad_registry else 'verified_registry_00014')
        self.zm.get_entity_state = lambda ref: {'state': self.states[ref]}
        def call(*, entity_id, desired_on):
            self.calls.append((entity_id, desired_on))
            if entity_id == self.fail_on and desired_on:
                raise RuntimeError('mock relay timeout')
            self.states[entity_id] = 'on' if desired_on else 'off'
        self.zm.call_binary_power_output_service = call
        self.mp = patch.dict(sys.modules, {'zigbee_manager': self.zm}); self.mp.start()
    def tearDown(self): self.mp.stop()
    def dual(self):
        c=contract();c['outputs'].append(dict(c['outputs'][0],reference='switch.test_aux'));return c
    def test_verified_on(self):
        r.set_outputs(contract(), True);self.assertEqual(self.states[REF], 'on')
    def test_no_duplicate_service_if_confirmed_on(self):
        self.states[REF]='on';r.set_outputs(contract(), True);self.assertEqual(self.calls, [])
    def test_off_readback(self):
        self.states[REF]='on';r.set_outputs(contract(), False);self.assertEqual(self.states[REF], 'off')
    def test_partial_fanout_reverted_off(self):
        self.fail_on='switch.test_aux'
        with self.assertRaises(e.ContractError):r.set_outputs(self.dual(),True)
        self.assertEqual(self.states[REF],'off');self.assertEqual(self.states['switch.test_aux'],'off')
    def test_registry_mismatch_never_actuated(self):
        self.bad_registry=REF
        with self.assertRaises(e.ContractError):r.set_outputs(contract(),True)
        self.assertEqual(self.calls, [])
    def test_local_context_reset_on_exception(self):
        self.fail_on=REF
        with self.assertRaises(e.ContractError):r.set_outputs(contract(),True)
        self.assertFalse(r.LOCAL_CALL.get())
    def test_unconfirmed_on_not_reported_success(self):
        self.zm.call_binary_power_output_service=lambda **kw:None
        with patch.object(r.time,'sleep',return_value=None):
            with self.assertRaises(e.ContractError):r.set_outputs(contract(),True)
    def test_input_boolean_binding_not_supported(self):
        cloud,ident,cfg=fixture()
        cfg['targets'][0]['output']['reference']='input_boolean.test'
        cfg['targets'][0]['weekly_control_output']['reference']='input_boolean.test'
        with self.assertRaises(e.ContractError):e.compile_targets(cloud,ident,[TID])

class OwnershipPersistence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=patch.object(r,'ROOT',Path(self.temp.name));self.root.start()
        self.before=dict(r.OWNERS);r.OWNERS.clear()
    def tearDown(self):
        r.OWNERS.clear();r.OWNERS.update(self.before);self.root.stop();self.temp.cleanup()
    def test_claim_persisted_before_use(self):
        r.reserve([contract()],'deviceA')
        saved=json.loads((r.ROOT/'ownership.json').read_text())
        self.assertEqual(saved['outputs'],r.OWNERS)
    def test_disk_failure_does_not_partially_claim(self):
        with patch.object(r,'atomic_json',side_effect=OSError('mock full disk')):
            with self.assertRaises(OSError):r.reserve([contract()],'deviceA')
        self.assertEqual(r.OWNERS,{})
    def test_rebind_refused(self):
        r.reserve([contract()],'deviceA');c=contract();c['outputs'][0]['device_reg_id']='different_registry_00014'
        with self.assertRaises(e.ContractError):r.reserve([c],'deviceA')
    def test_restart_recovers_ownership(self):
        r.reserve([contract()],'deviceA');r.OWNERS.clear()
        with patch.object(r,'identity',return_value={'device_uuid':'deviceA'}):r.load_reservations()
        self.assertIn(REF,r.OWNERS)
    def test_foreign_identity_refused(self):
        r.reserve([contract()],'deviceA');r.OWNERS.clear()
        with patch.object(r,'identity',return_value={'device_uuid':'deviceB'}):
            with self.assertRaises(e.ContractError):r.load_reservations()
        self.assertFalse(r.OWNERS)
    def test_corrupt_claim_does_not_allow_fallthrough(self):
        (r.ROOT/'ownership.json').write_text('{')
        with self.assertRaises(ValueError):r.load_reservations()

class AdditionalRegressions(unittest.TestCase):
    def test_invalid_soc_disables_only_boost(self):
        d=decision(soc=float('nan'),soc_fresh=True)
        self.assertFalse(d['boost']);self.assertFalse(d['soc_available']);self.assertTrue(d['desired_on'])
    def test_no_claim_requests_are_not_serialized(self):
        old=dict(r.OWNERS);r.OWNERS.clear()
        class ForbiddenLock:
            def __enter__(self):raise AssertionError('unused thermostat must not serialize other services')
            def __exit__(self,*a):pass
        try:
            with patch.object(r,'LOCK',ForbiddenLock()):
                self.assertEqual(r.request_guard(lambda **kw:'ok')(path='/services/switch/turn_off',method='POST',payload={'entity_id':'switch.other'}),'ok')
        finally:
            r.OWNERS.clear();r.OWNERS.update(old)
    def test_post_boot_new_output_claim_requires_restart(self):
        old=dict(r.OWNERS);r.OWNERS.clear()
        try:
            with self.assertRaises(e.ContractError):r.reserve_if_changed([contract()],'x')
        finally:
            r.OWNERS.clear();r.OWNERS.update(old)
