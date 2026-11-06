"""Read-only commissioning tests; HA reads are synthetic, all writes forbidden."""
import copy, sys, types, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import thermostat_engine as e
import thermostat_runtime as r
import thermostat_preflight as p
from test_thermostat import fixture, TID, REF

UID='00000000-0000-0000-0000-000000000114'
REQ='00000000-0000-0000-0000-000000000271'
class ReadOnlyPreflight(unittest.TestCase):
    def setUp(self):
        self.cloud,self.ident,self.cfg=fixture();self.cloud['device_uuid']=UID;self.ident['device_uuid']=UID
        self.payload={'device_uuid':UID,'request_id':REQ,'target_ids':[TID],
                      'expected_config_version':7,'expected_sensor_devices':{TID:'sensor_registry_14'}}
        self.when=datetime.now(timezone.utc)
        self.sensor={'state':'19.7','last_reported':self.when.isoformat(),
                     'last_updated':(self.when-timedelta(days=1)).isoformat(),
                     'attributes':{'unit_of_measurement':'°C'}}
        self.output='off';self.sensor_reg='sensor_registry_14';self.output_reg='verified_registry_00014'
        self.reads=[];self.writes=[]
        if 'zigbee_manager' not in sys.modules:
            m=types.ModuleType('zigbee_manager')
            m.get_entity_device_id=lambda **kw:None;m.get_entity_state=lambda x:{}
            pm=patch.dict(sys.modules,{'zigbee_manager':m});pm.start();self.addCleanup(pm.stop)
        import zigbee_manager as ha
        def registry(*,entity_id):
            self.reads.append(('registry',entity_id))
            return self.output_reg if entity_id==REF else self.sensor_reg
        def state(ref):
            self.reads.append(('state',ref))
            if ref==REF:return {'state':self.output}
            if ref=='sensor.room_temperature':return copy.deepcopy(self.sensor)
            raise AssertionError('unexpected_entity')
        def forbidden(*a,**kw):self.writes.append('write');raise AssertionError('PHYSICAL_OR_CONFIG_WRITE_FORBIDDEN')
        patches=[patch.object(r,'identity',lambda:self.ident),
                 patch.object(r,'cached_contracts',lambda selected:(e.compile_targets(self.cloud,self.ident,selected),UID,self.cloud['config_version'])),
                 patch.object(r,'options',lambda:('disabled',[],1800)),
                 patch.object(r,'reserve',forbidden),patch.object(r,'set_outputs',forbidden),
                 patch.object(r,'atomic_json',forbidden),patch.object(r,'install',forbidden),
                 patch.object(ha,'get_entity_device_id',registry),patch.object(ha,'get_entity_state',state),
                 patch.object(p.platform,'machine',lambda:'aarch64'),
                 patch.object(Path,'read_text',lambda *a,**kw:'0.1.171\n')]
        for x in patches:x.start();self.addCleanup(x.stop)
    def inspect(self):
        result=p.inspect(identity=self.ident,payload=self.payload)
        self.assertEqual(self.writes,[]);self.assertFalse(result['actuation']);self.assertFalse(result['handover_authorized'])
        return result
    def test_valid_read_only(self):self.assertTrue(self.inspect()['checks_passed'])
    def test_unchanged_temperature_last_reported_used(self):self.assertEqual(self.inspect()['targets'][0]['observation_timestamp_field'],'last_reported')
    def test_stale_actual_observation_rejected(self):
        self.sensor['last_reported']=(self.when-timedelta(hours=1)).isoformat();self.assertFalse(self.inspect()['checks_passed'])
    def test_fallback_stale_last_updated_not_freshened(self):
        self.sensor.pop('last_reported');self.assertFalse(self.inspect()['checks_passed'])
    def test_future_timestamp_rejected(self):
        self.sensor['last_reported']=(self.when+timedelta(hours=1)).isoformat();self.assertFalse(self.inspect()['checks_passed'])
    def test_no_timestamp_rejected(self):
        self.sensor.pop('last_reported');self.sensor.pop('last_updated');self.assertFalse(self.inspect()['checks_passed'])
    def test_sensor_hardware_mismatch_rejected(self):
        self.sensor_reg='replaced_sensor';self.assertFalse(self.inspect()['checks_passed'])
    def test_output_hardware_mismatch_rejected(self):
        self.output_reg='replaced_relay';self.assertFalse(self.inspect()['checks_passed'])
    def test_output_unknown_rejected(self):
        self.output='unavailable';self.assertFalse(self.inspect()['checks_passed'])
    def test_on_state_observed_not_forced_off(self):
        self.output='on';result=self.inspect();self.assertTrue(result['checks_passed']);self.assertFalse(result['targets'][0]['all_outputs_currently_off'])
    def test_temperature_unavailable_rejected(self):
        self.sensor['state']='unavailable';self.assertFalse(self.inspect()['checks_passed'])
    def test_fahrenheit_not_silently_converted(self):
        self.sensor['attributes']['unit_of_measurement']='°F';self.assertFalse(self.inspect()['checks_passed'])
    def test_old_revision_rejected_before_local_reads(self):
        self.payload['expected_config_version']=8
        with self.assertRaises(p.PreflightError):self.inspect()
        self.assertFalse(self.reads)
    def test_foreign_device_rejected(self):
        self.payload['device_uuid']=REQ
        with self.assertRaises(p.PreflightError):self.inspect()
        self.assertFalse(self.reads)
    def test_extra_activation_key_rejected(self):
        self.payload['activate']=True
        with self.assertRaises(p.PreflightError):self.inspect()
        self.assertFalse(self.reads)
    def test_duplicate_selection_rejected(self):
        self.payload['target_ids']=[TID,TID]
        with self.assertRaises(p.PreflightError):self.inspect()
    def test_sensor_binding_required(self):
        self.payload['expected_sensor_devices']={}
        with self.assertRaises(p.PreflightError):self.inspect()
    def test_amd64_not_native_arm(self):
        with patch.object(p.platform,'machine',lambda:'x86_64'):self.assertFalse(self.inspect()['checks_passed'])
    def test_wrong_installed_version_rejected(self):
        with patch.object(Path,'read_text',lambda *a,**kw:'0.1.168'):self.assertFalse(self.inspect()['checks_passed'])
    def test_command_success_does_not_authorize_activation(self):
        result=[];p.execute(identity=self.ident,command_id='cmd',command_payload=self.payload,submit_result=lambda **kw:result.append(kw))
        self.assertEqual(result[0]['status'],'succeeded');self.assertFalse(result[0]['result']['preflight']['handover_authorized'])
    def test_command_exception_redacted(self):
        result=[]
        with patch.object(p,'inspect',side_effect=RuntimeError('Authorization SECRET_TOKEN')):
            p.execute(identity=self.ident,command_id='cmd',command_payload=self.payload,submit_result=lambda **kw:result.append(kw))
        self.assertEqual(result[0]['status'],'failed');self.assertNotIn('SECRET_TOKEN',str(result));self.assertFalse(result[0]['result']['actuation'])
    def test_input_not_mutated(self):
        before=copy.deepcopy(self.payload);self.inspect();self.assertEqual(self.payload,before)
