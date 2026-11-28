import tempfile,unittest,copy
from datetime import datetime,timezone,timedelta
from pathlib import Path
import f171_midea_00019_thermostat as m
NOW=datetime(2026,11,28,2,0,tzinfo=timezone.utc)
IDENT={'device_uuid':m.UUID,'site_id':m.SITE,'serial_number':m.SERIAL}
PREG='a'*32;RREG='b'*32
C={'schema':1,'phase':'shadow','commission_id':'c'*32,'revision':1,'mode':'comfort','comfort_c':21,'frost_c':8,'pump_reg':PREG,'room_reg':RREG,'module_id':m.MODULE}
def cloud(c=C):
    return {'device_uuid':m.UUID,'settings':{m.KEY:copy.deepcopy(c)},'module_runtime_configurations':[{'module_key':'individual_project','module_type':'custom_project','module_id':m.MODULE,'configuration':{'schema_entity_bindings_v1':{'midea19-radiator-pump-switch':{'entity_id':m.PUMP,'transport':'zigbee'}}}}]}
class HA:
    def __init__(self):self.on=False;self.room=20;self.stale=False;self.calls=[];self.automation=False;self.wrong=False;self.fail=False
    def get_zha_devices(self):return [{'ieee':m.PUMP_IEEE,'device_reg_id':PREG if not self.wrong else RREG,'available':True,'manufacturer':'SONOFF','model':'ZBMINIR2','signature':{'endpoints':{'1':{'input_clusters':[6]}}}},{'ieee':m.ROOM_IEEE,'device_reg_id':RREG,'available':True,'manufacturer':'_TZE284_9ern5sfh','model':'TS0601'}]
    def get_entity_registry_entries(self):return [{'entity_id':e,'device_id':r,'platform':'zha','disabled_by':None} for e,r in [(m.PUMP,PREG),(m.ROOM,RREG)]]
    def home_assistant_request(self,**k):return [{'entity_id':'automation.conflict','state':'on' if self.automation else 'off'}]
    def get_entity_state(self,e):
        if e==m.PUMP:return {'entity_id':e,'state':'on' if self.on else 'off'}
        return {'entity_id':e,'state':str(self.room),'attributes':{'device_class':'temperature','unit_of_measurement':'°C'},'last_reported':(NOW-timedelta(seconds=1801 if self.stale else 1)).isoformat()}
    def call_binary_power_output_service(self,*,entity_id,desired_on):
        assert entity_id==m.PUMP;self.calls.append(desired_on)
        if not self.fail:self.on=desired_on
class T(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name)/'s.json';self.h=HA();self.c=copy.deepcopy(C)
    def tearDown(self):self.tmp.cleanup()
    def run_cycle(self):return m.cycle(IDENT,cloud(self.c),self.h,now=NOW,state_path=self.p,wait=lambda _:None)
    def arm(self):self.assertTrue(self.run_cycle()['shadow_ready']);self.assertEqual(self.h.calls,[]);self.c['phase']='armed';return self.run_cycle()
    def test_boundaries(self):
        for mode,target in [('comfort',21),('frost',8)]:
            for room,on,desired in [(target-.31,False,True),(target-.3,False,True),(target-.2,False,False),(target-.2,True,True),(target,True,False),(target+.1,True,False)]:self.assertIs(m.decide(mode,21,8,round(room,2),on)['desired_on'],desired)
    def test_invalid_targets(self):
        for a in [('comfort',21,22,20,False),('comfort',float('nan'),8,20,False),('frost',21,4,20,False),('comfort',21.05,8,20,False),('comfort',21,8,20,None)]:
            with self.assertRaises(m.Hold):m.decide(*a)
    def test_shadow_no_write(self):self.assertEqual(self.run_cycle()['status'],'shadow');self.assertEqual(self.h.calls,[])
    def test_arm_without_shadow(self):self.c['phase']='armed';self.assertFalse(self.run_cycle()['physical_control_active']);self.assertEqual(self.h.calls,[])
    def test_active_transitions(self):
        self.assertTrue(self.arm()['physical_control_active']);self.assertEqual(self.h.calls,[True]);self.h.room=20.8;self.run_cycle();self.assertEqual(self.h.calls,[True]);self.h.room=21;self.run_cycle();self.assertEqual(self.h.calls,[True,False]);self.h.room=20.7;self.run_cycle();self.assertEqual(self.h.calls,[True,False,True])
    def test_mode_change(self):self.arm();self.c.update(mode='frost',revision=2);self.assertFalse(self.run_cycle()['desired_on']);self.assertEqual(self.h.calls,[True,False])
    def test_stale_sensor_holds(self):self.arm();self.h.stale=True;self.h.room=25;self.assertEqual(self.run_cycle()['reason'],'room_measurement_stale');self.assertEqual(self.h.calls,[True])
    def test_foreign(self):self.assertIsNone(m.cycle({**IDENT,'device_uuid':'wrong'},cloud(),self.h,now=NOW,state_path=self.p));self.assertEqual(self.h.calls,[])
    def test_wrong_hardware(self):self.h.wrong=True;self.assertFalse(self.run_cycle()['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_competing_automation(self):self.h.automation=True;self.assertFalse(self.run_cycle()['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_readback_latch(self):self.h.fail=True;self.assertTrue(self.arm()['locked']);self.run_cycle();self.assertEqual(self.h.calls,[True])
    def test_disarm(self):self.arm();self.c['phase']='disarmed';self.assertFalse(self.run_cycle()['owns_relay']);self.assertEqual(self.h.calls,[True])
    def test_competing_binding(self):
        x=cloud();x['module_runtime_configurations'].append({'configuration':{'pump':m.PUMP}});self.assertFalse(m.cycle(IDENT,x,self.h,now=NOW,state_path=self.p)['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_runtime_id_omitted_with_exact_owner(self):
        x=cloud();del x['module_runtime_configurations'][0]['module_id']
        self.assertTrue(m.cycle(IDENT,x,self.h,now=NOW,state_path=self.p)['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_runtime_id_conflict(self):
        x=cloud();x['module_runtime_configurations'][0]['installation_module_id']='foreign'
        self.assertFalse(m.cycle(IDENT,x,self.h,now=NOW,state_path=self.p)['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_owner_module_id_missing_or_wrong(self):
        for missing in (True,False):
            x=cloud();c=x['settings'][m.KEY]
            if missing:del c['module_id']
            else:c['module_id']='foreign'
            self.assertFalse(m.cycle(IDENT,x,self.h,now=NOW,state_path=self.p)['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_f108_passive_radiator_metadata(self):
        x=cloud();x['module_runtime_configurations'][0]['configuration']['radiator_pump']={'entity_id':m.PUMP,'command_enabled':False,'autostart':False,'transport':'zigbee'}
        self.assertTrue(m.cycle(IDENT,x,self.h,now=NOW,state_path=self.p)['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_f108_reject_active_unknown_or_changed_metadata(self):
        base={'entity_id':m.PUMP,'command_enabled':False,'autostart':False}
        variants=[{**base,'command_enabled':True},{**base,'autostart':True},{**base,'enabled':True},{**base,'command_enabled':0},{**base,'autostart':'false'},{**base,'rules':[]},{**base,'ieee':'wrong'},{**base,'switch_entity_id':'switch.other'},{**base,'control_enabled':True},{k:v for k,v in base.items() if k!='autostart'}]
        for item in variants:
            x=cloud();x['module_runtime_configurations'][0]['configuration']['radiator_pump']=item
            with self.assertRaises(m.Hold):m.config(IDENT,x)
        self.assertEqual(self.h.calls,[])
    def test_f108_active_implicit_relay_owner_is_rejected(self):
        x=cloud();x['module_runtime_configurations'][0]['configuration']['radiator_pump']={'command_enabled':True,'autostart':False}
        with self.assertRaisesRegex(m.Hold,'legacy_radiator_pump_not_proven_passive'):m.config(IDENT,x)
    def test_f108_other_module_not_exempted(self):
        x=cloud();x['module_runtime_configurations'].append({'module_key':'other','configuration':{'radiator_pump':{'entity_id':m.PUMP,'command_enabled':False,'autostart':False}}})
        with self.assertRaisesRegex(m.Hold,'competing_module_configuration'):m.config(IDENT,x)
    def test_f108_other_reference_same_module_not_exempted(self):
        x=cloud();cfg=x['module_runtime_configurations'][0]['configuration'];cfg['radiator_pump']={'entity_id':m.PUMP,'command_enabled':False,'autostart':False};cfg['heating_rule']={'target':m.PUMP}
        with self.assertRaisesRegex(m.Hold,'competing_module_configuration'):m.config(IDENT,x)
    def test_f108_passive_alias_hardware_still_required(self):
        x=cloud();x['module_runtime_configurations'][0]['configuration']['radiator_pump']={'entity_id':m.PUMP,'command_enabled':False,'autostart':False};self.h.wrong=True
        self.assertFalse(m.cycle(IDENT,x,self.h,now=NOW,state_path=self.p)['shadow_ready']);self.assertEqual(self.h.calls,[])
    def nested_fixture(self):
        x=cloud();cfg=x['module_runtime_configurations'][0]['configuration']
        # Exact path/alias reported by R3; safety flags from F108 documentation.
        cfg['midea_00019_v1']={'radiator_pump':{'confirmed_entity_id':m.PUMP,'command_enabled':False,'autostart':False},'modbus':{'write_enabled':False},'room_control':{'command_enabled':False}}
        return x,cfg['midea_00019_v1']
    def test_r4_nested_passive_alias(self):
        x,project=self.nested_fixture();self.assertTrue(m.cycle(IDENT,x,self.h,now=NOW,state_path=self.p)['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_r4_nested_active_or_missing_safety_flags(self):
        for flag in ('command_enabled','autostart'):
            for mode in ('true','missing','string','integer'):
                x,project=self.nested_fixture();b=project['radiator_pump']
                if mode=='missing':del b[flag]
                else:b[flag]={'true':True,'string':'false','integer':0}[mode]
                with self.assertRaisesRegex(m.Hold,'nested_f108_radiator_pump_not_proven_passive'):m.config(IDENT,x)
    def test_r4_nested_wrong_or_missing_confirmed_entity(self):
        for entity in (None,'switch.other'):
            x,project=self.nested_fixture();project['radiator_pump']['confirmed_entity_id']=entity
            with self.assertRaisesRegex(m.Hold,'nested_f108_radiator_pump_not_proven_passive'):m.config(IDENT,x)
    def test_r4_nested_unknown_or_active_rule(self):
        for extra in ({'schedule':[]},{'enabled':True},{'control_enabled':True},{'target':m.PUMP},{'rules':{'enabled':False}}):
            x,project=self.nested_fixture();project['radiator_pump'].update(extra)
            with self.assertRaisesRegex(m.Hold,'nested_f108_radiator_pump_not_proven_passive'):m.config(IDENT,x)
    def test_r4_nested_sibling_reference_still_blocked(self):
        x,project=self.nested_fixture();project['room_control']['target']=m.PUMP
        with self.assertRaisesRegex(m.Hold,'competing_module_configuration'):m.config(IDENT,x)
    def test_r4_nested_other_module_still_blocked(self):
        x,project=self.nested_fixture();x['module_runtime_configurations'].append({'module_key':'other','configuration':{'midea_00019_v1':copy.deepcopy(project)}})
        with self.assertRaisesRegex(m.Hold,'competing_module_configuration'):m.config(IDENT,x)
    def test_r4_nested_hardware_and_automation_still_required(self):
        x,project=self.nested_fixture();self.h.automation=True
        self.assertFalse(m.cycle(IDENT,x,self.h,now=NOW,state_path=self.p)['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_r4_preserve_original_configuration(self):
        x,_=self.nested_fixture();before=copy.deepcopy(x);m.config(IDENT,x);self.assertEqual(x,before)
    def test_r5_complete_r4_production_metadata_shape(self):
        x,project=self.nested_fixture()
        project['radiator_pump'].update(transport='zigbee',verified_by_installer=True,binding_verified=True)
        self.assertEqual(set(project['radiator_pump']),{'transport','confirmed_entity_id','verified_by_installer','binding_verified','command_enabled','autostart'})
        self.assertTrue(m.cycle(IDENT,x,self.h,now=NOW,state_path=self.p)['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_r5_metadata_cannot_authorize_control(self):
        x,project=self.nested_fixture();project['radiator_pump'].update(transport='zigbee',verified_by_installer=True,binding_verified=True,command_enabled=True)
        with self.assertRaisesRegex(m.Hold,'nested_f108_radiator_pump_not_proven_passive'):m.config(IDENT,x)
    def test_r5_installer_annotation_is_not_authority(self):
        x,project=self.nested_fixture();project['radiator_pump'].update(transport='zigbee',verified_by_installer='2026-11-22',binding_verified=False)
        self.assertTrue(m.cycle(IDENT,x,self.h,now=NOW,state_path=self.p)['shadow_ready']);self.assertEqual(self.h.calls,[])
    def test_r5_reject_nested_or_foreign_metadata(self):
        for extra in ({'verified_by_installer':{'target':m.PUMP}}, {'verified_by_installer':m.PUMP}, {'binding_verified':'true'}, {'binding_verified':1}, {'binding_verified':{'enabled':True}}, {'transport':'wifi'}, {'unrecognized':True}):
            x,project=self.nested_fixture();project['radiator_pump'].update(extra)
            with self.assertRaisesRegex(m.Hold,'nested_f108_radiator_pump_not_proven_passive'):m.config(IDENT,x)
    def test_r5_no_relaxation_of_competing_owner(self):
        x,project=self.nested_fixture();project['radiator_pump'].update(verified_by_installer=True,binding_verified=True);project['room_control']['target']=m.PUMP
        with self.assertRaisesRegex(m.Hold,'competing_module_configuration'):m.config(IDENT,x)
    def test_external_change(self):self.arm();self.h.on=False;self.assertTrue(self.run_cycle()['locked']);self.assertEqual(self.h.calls,[True])
if __name__=='__main__':unittest.main(verbosity=2)
