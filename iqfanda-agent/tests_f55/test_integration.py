"""F55 R3 full-runtime integration tests. All physical I/O is simulated.
The production module imports and common HA service adapter chain are real in
an image test. These tests never connect to HA, a field device or the Internet.
"""
import ast
import copy
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import thermostat_engine as e
import thermostat_runtime as r
import zigbee_manager as zm
import heartbeat as hb
import telemetrie_modulu as tm
import command_worker as cw
import startup
from test_thermostat import fixture, TID, REF, NOW

# Capture aliases before r.install(), as existing workers do during imports.
EARLY_BINARY_ALIAS = zm.call_binary_power_output_service

class HaltTestLoop(Exception):
    pass

class FullRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cloud, self.ident, self.cfg = fixture()
        self.identpath = self.root/'identity.json'
        self.cloudpath = self.root/'cloud.json'
        self.identpath.write_text(json.dumps(self.ident))
        self.cloudpath.write_text(json.dumps(self.cloud))
        self.mode = 'disabled'
        self.selected = []
        self.mono = 0.0
        self.temperature = 19.7
        self.sensor_age = 0.0
        self.states = {REF:'off', 'switch.qa_unrelated':'off'}
        self.writes = []
        self.reads = []
        self.reader_calls = 0
        self.clock = lambda: NOW + timedelta(seconds=self.mono)
        case = self
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return case.clock().astimezone(tz or timezone.utc)
        patches = [
            patch.dict(os.environ, {'IQF_DEVICE_CONFIG_PATH':str(self.identpath),
                                   'IQF_CLOUD_CONFIG_PATH':str(self.cloudpath)}),
            patch.object(r,'ROOT',self.root/'owners'),
            patch.object(r,'options',lambda:(self.mode,self.selected,1800.0)),
            patch.object(r,'OWNERS',{}), patch.object(r,'STATES',{}),
            patch.object(r,'BOOTSTRAPPED',set()),patch.object(r,'INSTALLED',False),
            patch.object(r,'BOOT_MODE',None),patch.object(r,'LAST_SAVE',0.0),
            patch.object(r,'STATUS',{'schema_version':1,'executor':'addon',
                'component':'F55_LOCAL_THERMOSTAT_V1','mode':'disabled',
                'targets':[],'physical_control_active':False,'candidate':True}),
            patch.object(r,'SOC',{'value':None,'received_mono':None}),
            patch.object(r,'datetime',Clock),
            patch.object(r,'time',types.SimpleNamespace(monotonic=lambda:self.mono,sleep=lambda _:None)),
            patch.object(zm,'home_assistant_request',self.fake_http),
            patch.object(hb,'build_communication_state',lambda:{'existing_telemetry':'preserved'}),
            patch.object(tm,'nacti_fve_telemetrii',self.fake_fve),
        ]
        for p in patches:
            p.start();self.addCleanup(p.stop)

    def fake_http(self, *, path, method='GET', payload=None):
        self.reads.append((method,path))
        if method == 'POST' and path == '/template':
            if 'device_id(' in payload.get('template',''):
                return 'verified_registry_00014'
            raise AssertionError('unexpected template')
        if method == 'GET' and path.startswith('/states/'):
            entity = path.split('/states/',1)[1]
            if entity == 'sensor.room_temperature':
                return {'entity_id':entity,'state':str(self.temperature),
                        'last_reported':(self.clock()-timedelta(seconds=self.sensor_age)).isoformat(),
                        'last_updated':(self.clock()-timedelta(days=1)).isoformat(),
                        'attributes':{'unit_of_measurement':'°C'}}
            if entity in self.states:
                return {'entity_id':entity,'state':self.states[entity],
                        'last_updated':self.clock().isoformat(),'attributes':{}}
        if method == 'POST' and path in ('/services/switch/turn_on','/services/switch/turn_off'):
            ref=payload['entity_id']
            if ref not in self.states:raise AssertionError('unexpected test output')
            self.states[ref] = 'on' if path.endswith('turn_on') else 'off'
            self.writes.append((ref,self.states[ref]));return []
        raise AssertionError('unexpected simulated request: '+method+' '+path)

    def fake_fve(self,*a,**kw):
        self.reader_calls+=1
        return ({},[{'entity_key':'baterie.soc','quality':'good','value':12}], 'fixture','universal')

    def start_active(self):
        self.mode='active';self.selected=[TID];r.install();r.tick()

    def heat(self):
        self.start_active();self.mono=301;r.tick()
        self.assertEqual(self.states[REF],'on')

    def test_default_disabled_has_no_identity_dependency_or_owner(self):
        self.identpath.unlink();self.cloudpath.unlink();r.install();r.tick()
        self.assertFalse(r.OWNERS);self.assertFalse(self.writes)
        self.assertFalse((r.ROOT/'ownership.json').exists())

    def test_install_is_idempotent(self):
        r.install();wrapped=zm.home_assistant_request;r.install()
        self.assertIs(zm.home_assistant_request,wrapped)

    def test_shadow_evaluates_without_relay_write(self):
        self.mode='shadow';self.selected=[TID];r.install();r.tick();self.mono=301;r.tick()
        self.assertFalse(self.writes);self.assertFalse(r.OWNERS)
        self.assertTrue(r.snapshot()['targets'][0]['desired_on'])
        self.assertFalse(r.snapshot()['targets'][0]['actuation_verified'])

    def test_claim_is_durable_before_first_service(self):
        self.mode='active';self.selected=[TID];r.install()
        doc=json.loads((r.ROOT/'ownership.json').read_text())
        self.assertEqual(doc['outputs'],r.OWNERS);self.assertFalse(self.writes)

    def test_common_adapter_alias_cannot_override_local_on(self):
        self.start_active()
        with self.assertRaises(e.ContractError):
            EARLY_BINARY_ALIAS(entity_id=REF,desired_on=True)
        self.assertFalse(self.writes)

    def test_common_adapter_alias_cannot_override_local_off(self):
        self.heat();before=list(self.writes)
        with self.assertRaises(e.ContractError):
            EARLY_BINARY_ALIAS(entity_id=REF,desired_on=False)
        self.assertEqual(self.writes,before);self.assertEqual(self.states[REF],'on')

    def test_unrelated_output_keeps_common_adapter(self):
        self.start_active();EARLY_BINARY_ALIAS(entity_id='switch.qa_unrelated',desired_on=True)
        self.assertEqual(self.states['switch.qa_unrelated'],'on')

    def test_heat_with_low_soc_uses_local_cycle(self):
        self.start_active();tm.nacti_fve_telemetrii({});self.mono=301;r.tick()
        self.assertEqual(self.states[REF],'on');self.assertEqual(self.reader_calls,1)
        self.assertFalse(r.snapshot()['targets'][0]['boost'])

    def test_heat_without_soc_uses_cached_configuration(self):
        self.heat();self.assertIsNone(r.SOC['value'])
        self.assertTrue((r.ROOT/'last-good.json').exists())

    def test_setpoint_off_and_minimum_off_are_enforced_in_tick(self):
        self.heat();self.mono=302;self.temperature=20;r.tick()
        self.assertEqual(self.states[REF],'off')
        self.mono=303;self.temperature=19.7;r.tick()
        self.assertEqual(self.states[REF],'off')
        self.mono=604;r.tick();self.assertEqual(self.states[REF],'on')

    def test_stale_sensor_switches_off(self):
        self.heat();self.sensor_age=1801;self.mono=302;r.tick()
        self.assertEqual(self.states[REF],'off')
        self.assertIn('stale',r.snapshot()['targets'][0]['reason'])

    def test_heartbeat_preserves_existing_data_and_includes_actual_runtime(self):
        self.heat();state=hb.build_communication_state()
        self.assertEqual(state['existing_telemetry'],'preserved')
        detail=state['local_thermostat_runtime']['targets'][0]
        self.assertTrue(detail['actuation_verified']);self.assertEqual(detail['output_feedback'][REF],'on')

    def test_disabled_after_takeover_does_not_release_cloud_authority(self):
        self.heat();self.mode='disabled';r.tick()
        self.assertEqual(self.states[REF],'off');self.assertIn(REF,r.OWNERS)
        with self.assertRaises(e.ContractError):
            EARLY_BINARY_ALIAS(entity_id=REF,desired_on=True)

    def test_process_restart_recovers_ownership_and_restarts_off_timer(self):
        self.heat();r.OWNERS.clear();r.STATES.clear();r.BOOTSTRAPPED.clear()
        r.INSTALLED=False;r.BOOT_MODE=None;self.mono=0;r.install();r.tick()
        self.assertIn(REF,r.OWNERS);self.assertEqual(self.states[REF],'off')
        self.mono=299;r.tick();self.assertEqual(self.states[REF],'off')
        self.mono=301;r.tick();self.assertEqual(self.states[REF],'on')

    def test_bad_cache_retains_owner_and_outer_loop_fails_off(self):
        self.heat();self.cloudpath.write_text('{')
        with patch.object(r.time,'sleep',side_effect=HaltTestLoop):
            with self.assertRaises(HaltTestLoop):r.run_forever()
        self.assertIn(REF,r.OWNERS);self.assertEqual(self.states[REF],'off')

    def test_foreign_configuration_never_rebinds(self):
        self.heat();bad=copy.deepcopy(self.cloud);bad['device_uuid']='other-device'
        self.cloudpath.write_text(json.dumps(bad))
        with patch.object(r.time,'sleep',side_effect=HaltTestLoop):
            with self.assertRaises(HaltTestLoop):r.run_forever()
        self.assertEqual(self.states[REF],'off')
        self.assertEqual(r.OWNERS[REF]['device_reg_id'],'verified_registry_00014')

    def test_soc_observer_does_not_do_an_extra_read(self):
        r.install();tm.nacti_fve_telemetrii({})
        self.assertEqual(self.reader_calls,1);self.assertEqual(r.SOC['value'],12)
        self.assertFalse(self.reads)

    def test_startup_initializes_guard_before_starting_any_worker(self):
        tree=ast.parse(Path(startup.__file__).read_text('utf-8-sig'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call)]
        installs=[n.lineno for n in calls if isinstance(n.func,ast.Name) and n.func.id=='f55_thermostat_install']
        starts=[n.lineno for n in calls if isinstance(n.func,ast.Attribute) and n.func.attr=='start']
        self.assertEqual(len(installs),1);self.assertTrue(starts);self.assertLess(installs[0],min(starts))

if __name__=='__main__':unittest.main(verbosity=2)
