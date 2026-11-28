"""F171 development candidate, exact Midea 00019. Local secondary pump only.
No start on import. No default temperatures. No physical writes without shadow/arm.
"""
from __future__ import annotations
import functools
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

SERIAL='F800711-TNG-00019'
UUID='648d4229-7c17-400f-bbed-ffe5ab04b844'
SITE='980e5e5e-76b4-4cac-9b6c-1f31fa5fd6e1'
MODULE='059ab88f-4e85-465b-bdf6-6aee4729c06d'
ROOM='sensor.tze284_9ern5sfh_ts0601_teplota'
ROOM_IEEE='a4:c1:38:12:7d:73:ea:39'
PUMP='switch.sonoff_zbminir2'
PUMP_IEEE='c0:2c:ed:ff:fe:bf:86:7b'
KEY='f171_midea_00019_thermostat_v1'
STATUS_KEY=KEY
STATE_PATH=Path('/config/f171-midea-00019-thermostat.json')
ROOM_MAX_AGE=1800
POLL_SECONDS=15
_LOCK=threading.RLock()
_LOCAL=threading.local()

class Hold(RuntimeError): pass

def require(ok,reason):
    if not ok: raise Hold(reason)

def matches(identity):
    return isinstance(identity,dict) and str(identity.get('device_uuid'))==UUID and str(identity.get('site_id'))==SITE and all(not identity.get(k) or identity[k]==SERIAL for k in ('serial_number','device_serial_number'))

def number(v,lo,hi,*,tenths=False):
    require(type(v) in (int,float,str),'invalid_number')
    try: d=Decimal(str(v))
    except InvalidOperation: raise Hold('invalid_number') from None
    require(d.is_finite() and Decimal(str(lo))<=d<=Decimal(str(hi)),'invalid_number')
    if tenths: require(d*10==(d*10).to_integral_value(),'invalid_temperature_step')
    return d

def targets(mode,comfort_c,frost_c):
    require(mode in ('comfort','frost'),'invalid_mode')
    comfort=number(comfort_c,5,35,tenths=True);frost=number(frost_c,5,20,tenths=True)
    require(frost<=comfort,'frost_above_comfort')
    return comfort if mode=='comfort' else frost

def decide(mode,comfort_c,frost_c,room_c,pump_on):
    target=targets(mode,comfort_c,frost_c)
    require(type(pump_on) is bool,'unknown_relay_state')
    room=number(room_c,-20,60)
    desired=True if room<=target-Decimal('0.3') else False if room>=target else pump_on
    return {'target_c':float(target),'on_at_c':float(target-Decimal('0.3')),'off_at_c':float(target),'desired_on':desired}

def load_state(path):
    try:
        s=json.loads(path.read_text(encoding='utf-8'))
        return s if isinstance(s,dict) and s.get('device_uuid')==UUID and s.get('site_id')==SITE else {}
    except (OSError,ValueError,TypeError): return {}

def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,allow_nan=False,ensure_ascii=True); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def config(identity,cloud):
    require(matches(identity),'foreign_device')
    require(isinstance(cloud,dict) and cloud.get('device_uuid')==UUID,'foreign_or_missing_config')
    settings=cloud.get('settings'); c=settings.get(KEY) if isinstance(settings,dict) else None
    if c is None: return None
    required={'schema','phase','commission_id','revision','mode','comfort_c','frost_c','pump_reg','room_reg','module_id'}
    require(isinstance(c,dict) and set(c)==required and c['schema']==1 and type(c['schema']) is int,'invalid_config_contract')
    require(c['phase'] in ('shadow','armed','disarmed'),'invalid_phase')
    require(isinstance(c['commission_id'],str) and re.fullmatch('[0-9a-f]{32}',c['commission_id']),'invalid_commission_id')
    require(type(c['revision']) is int and c['revision']>0,'invalid_revision')
    for k in ('pump_reg','room_reg'): require(isinstance(c[k],str) and re.fullmatch('[0-9a-f]{32}',c[k]),'invalid_registry_id')
    require(c['pump_reg']!=c['room_reg'],'duplicate_registry_id')
    require(c['module_id']==MODULE,'owner_module_identity_changed')
    targets(c['mode'],c['comfort_c'],c['frost_c'])
    if c['phase']=='disarmed': return c
    modules=cloud.get('module_runtime_configurations')
    require(isinstance(modules,list),'runtime_modules_missing')
    exact=[m for m in modules if isinstance(m,dict) and m.get('module_key')=='individual_project']
    # DeviceModuleRuntimeConfiguration may omit the database module ID.
    # Cloud proves that exact ID in its scoped owner contract before saving.
    # If the runtime serializer supplies IDs, every supplied ID must agree.
    require(len(exact)==1 and exact[0].get('module_type')=='custom_project','individual_module_identity_changed')
    require(all(str(exact[0][k])==MODULE for k in ('module_id','installation_module_id','id') if exact[0].get(k) is not None),'individual_module_identity_changed')
    cfg=exact[0].get('configuration');require(isinstance(cfg,dict),'module_config_missing')
    mappings=cfg.get('schema_entity_bindings_v1');require(isinstance(mappings,dict),'schema_bindings_missing')
    pump=mappings.get('midea19-radiator-pump-switch');require(isinstance(pump,dict) and pump.get('entity_id')==PUMP and pump.get('transport')=='zigbee','pump_binding_changed')
    room=mappings.get('midea19-room-temperature');require(room is None or (isinstance(room,dict) and room.get('entity_id')==ROOM and room.get('transport')=='zigbee'),'room_binding_changed')
    refs={PUMP,PUMP_IEEE,c['pump_reg']}
    require(not has_ref({k:v for k,v in mappings.items() if k!='midea19-radiator-pump-switch'},refs),'competing_schema_binding')
    # F108 declared a passive duplicate radiator_pump record, separate from
    # the schema binding. Accept it ONLY with exact disabled control flags,
    # recognized scalar metadata and the same physical relay. Nothing else is
    # excluded from the competing-owner scan.
    extra={k:v for k,v in cfg.items() if k!='schema_entity_bindings_v1'}
    legacy=extra.get('radiator_pump')
    if 'radiator_pump' in extra:
        require(passive_radiator_binding(legacy,c),'legacy_radiator_pump_not_proven_passive')
        del extra['radiator_pump']
    # R3 production evidence: the original F108 alias is nested inside
    # midea_00019_v1. Exempt ONLY the exact passive radiator_pump record,
    # not the whole project configuration or its other subtrees.
    legacy_project=extra.get('midea_00019_v1')
    if isinstance(legacy_project,dict) and 'radiator_pump' in legacy_project:
        block=legacy_project['radiator_pump']
        require(isinstance(block,dict) and block.get('confirmed_entity_id')==PUMP
                and passive_radiator_binding(block,c),'nested_f108_radiator_pump_not_proven_passive')
        extra['midea_00019_v1']={k:v for k,v in legacy_project.items() if k!='radiator_pump'}
    require(not has_ref(extra,refs),'competing_module_configuration')
    require(not any(has_ref(m.get('configuration'),refs) for m in modules if isinstance(m,dict) and m is not exact[0]),'competing_module_configuration')
    require(not has_ref({k:v for k,v in settings.items() if k not in (KEY,'zigbee_device_names')},refs),'competing_device_settings')
    return c

def passive_radiator_binding(block,c):
    """F108 passive duplicate; no generic ignore and no active flag tolerated."""
    if not isinstance(block,dict):return False
    if block.get('command_enabled') is not False or block.get('autostart') is not False:return False
    expected={
        'entity_id':PUMP,'switch_entity_id':PUMP,'zigbee_entity_id':PUMP,'confirmed_entity_id':PUMP,
        'ieee':PUMP_IEEE,'device_ieee':PUMP_IEEE,'device_reg_id':c['pump_reg'],
        'transport':'zigbee','source':'zigbee','capability':'switch',
        'command_enabled':False,'autostart':False,'control_enabled':False,
        'enabled':False,
    }
    # The two F108 safety flags are mandatory; every extra key must be one of
    # these exact metadata values. In particular, no modes, rules, nested
    # settings, schedules, or alternate actuator may be silently ignored.
    # F171 R5: F108 production audit revealed these two historical proof
    # fields. They are informational, never commissioning/actuation authority.
    # Accept only a boolean proof flag or a short scalar installer annotation;
    # no embedded references, containers or additional arbitrary keys.
    refs={PUMP,PUMP_IEEE,c['pump_reg']}
    for k,v in block.items():
        if k=='binding_verified':
            if type(v) is not bool:return False
        elif k=='verified_by_installer':
            if type(v) is bool:continue
            if not (isinstance(v,str) and 0<len(v)<=128 and v.strip() and v.isprintable() and not has_ref(v,refs)):return False
        elif k not in expected or type(v) is not type(expected[k]) or v!=expected[k]:
            return False
    return True


def has_ref(x,refs):
    if isinstance(x,str): return x.lower().strip() in refs
    if isinstance(x,dict): return any(has_ref(k,refs) or has_ref(v,refs) for k,v in x.items())
    if isinstance(x,list): return any(has_ref(v,refs) for v in x)
    return False

def check_hardware(ha,c):
    devices=ha.get_zha_devices(); entries=ha.get_entity_registry_entries()
    require(isinstance(devices,list) and isinstance(entries,list),'zigbee_inventory_unavailable')
    for entity,ieee,reg,manufacturer,model in [(PUMP,PUMP_IEEE,c['pump_reg'],'sonoff','ZBMINIR2'),(ROOM,ROOM_IEEE,c['room_reg'],'_tze284_9ern5sfh','TS0601')]:
        ee=[e for e in entries if isinstance(e,dict) and e.get('entity_id')==entity]
        require(len(ee)==1 and ee[0].get('device_id')==reg and ee[0].get('platform')=='zha' and not ee[0].get('disabled_by'),'entity_registry_mismatch')
        dd=[d for d in devices if isinstance(d,dict) and str(d.get('ieee','')).lower()==ieee]
        require(len(dd)==1 and dd[0].get('device_reg_id')==reg and dd[0].get('available') is True,'zigbee_device_unavailable_or_wrong_identity')
        sig=dd[0].get('signature') or {}
        require(str(dd[0].get('manufacturer') or sig.get('manufacturer') or '').lower()==manufacturer and (dd[0].get('model') or sig.get('model'))==model,'zigbee_model_mismatch')
        if entity==PUMP:
            clusters=[str(x).lower() for ep in (sig.get('endpoints') or {}).values() if isinstance(ep,dict) for x in ep.get('input_clusters',[])]
            require(any(x in ('6','0x0006','0x6') for x in clusters),'onoff_cluster_not_verified')
    states=ha.home_assistant_request(path='/states')
    require(isinstance(states,list),'automation_inventory_unavailable')
    # Conservative ownership check, never disable other automations.
    for st in states:
        if isinstance(st,dict) and str(st.get('entity_id','')).startswith(('automation.','script.')):
            require(st.get('state')=='off','possible_competing_ha_automation')

def stamp(s):
    require(isinstance(s,str),'measurement_time_missing')
    try: t=datetime.fromisoformat(s.replace('Z','+00:00'))
    except ValueError: raise Hold('measurement_time_invalid') from None
    require(t.tzinfo is not None,'measurement_time_missing_zone')
    return t.astimezone(timezone.utc)

def room_state(ha,now):
    s=ha.get_entity_state(ROOM);require(isinstance(s,dict) and s.get('entity_id')==ROOM,'room_entity_mismatch')
    a=s.get('attributes');require(isinstance(a,dict) and a.get('device_class')=='temperature' and a.get('unit_of_measurement')=='°C','room_unit_invalid')
    t=stamp(s.get('last_reported') or s.get('last_updated'))
    require(0<=(now-t).total_seconds()<=ROOM_MAX_AGE,'room_measurement_stale')
    return float(number(s.get('state'),-20,60)),t.isoformat()

def relay_state(ha):
    s=ha.get_entity_state(PUMP);require(isinstance(s,dict) and s.get('entity_id')==PUMP and s.get('state') in ('on','off'),'relay_state_unknown')
    return s['state']=='on'

def owner_claimed():
    return load_state(STATE_PATH).get('owns_relay') is True

def guarded_ha_request(fn):
    """Stack outside existing F114 decorator; retain F114 for 00015."""
    @functools.wraps(fn)
    def wrapped(*,path,method='GET',payload=None):
        if method.upper()=='GET' or not str(path).lstrip('/').startswith('services/'): return fn(path=path,method=method,payload=payload)
        try:
            from device_config import load_device_identity
            own=matches(load_device_identity())
        except Exception: own=owner_claimed()
        if not own:return fn(path=path,method=method,payload=payload)
        with _LOCK:
            if owner_claimed():
                p=payload if isinstance(payload,dict) else {}
                service=str(path).strip('/').split('/')
                if getattr(_LOCAL,'pump_write',False):
                    require(service in (['services','switch','turn_on'],['services','switch','turn_off']) and p=={'entity_id':PUMP},'local_write_scope')
                else:
                    state=load_state(STATE_PATH);refs={PUMP,PUMP_IEEE,state.get('pump_reg','')}
                    require(not has_ref(p,refs),'local_thermostat_owns_pump')
                    if len(service)>1 and service[1] in ('switch','light','homeassistant','group','script','automation'):
                        ents=p.get('entity_id');ents=[ents] if isinstance(ents,str) else ents
                        require(isinstance(ents,list) and bool(ents) and all(isinstance(x,str) and x.startswith(('switch.','light.')) and x not in refs for x in ents) and not set(p)&{'device_id','area_id','target','floor_id','label_id'},'indirect_service_owner_unknown')
            return fn(path=path,method=method,payload=payload)
    return wrapped

def cycle(identity,cloud,ha,*,now=None,state_path=None,wait=time.sleep):
    if not matches(identity): return None
    now=now or datetime.now(timezone.utc);path=state_path or STATE_PATH
    with _LOCK:
        prev=load_state(path)
        s={'schema':1,'device_uuid':UUID,'site_id':SITE,'serial_number':SERIAL,'updated_utc':now.isoformat(),'phase':'uncommissioned','status':'hold','owns_relay':prev.get('owns_relay') is True,'locked':prev.get('locked') is True,'physical_control_active':False,'shadow_ready':False,'readback_verified':False,'service_called':False}
        for key in ('commission_id','pump_reg','room_reg','relay_on','revision','mode','comfort_c','frost_c'):
            if key in prev:s[key]=prev[key]
        def done():atomic(path,s);return s
        try:
            c=config(identity,cloud)
            if c is None: s['reason']='no_settings';return done()
            if prev.get('owns_relay'):
                require(prev.get('commission_id')==c['commission_id'] and all(prev.get(k)==c[k] for k in ('pump_reg','room_reg')),'owned_identity_changed')
                require(type(prev.get('revision')) is int and c['revision']>=prev['revision'],'config_revision_rollback')
                if c['revision']==prev['revision']:
                    require(all(prev.get(k)==c[k] for k in ('mode','comfort_c','frost_c')),'config_changed_without_revision')
            s.update({k:c[k] for k in ('commission_id','revision','phase','mode','comfort_c','frost_c','pump_reg','room_reg')})
            if c['phase']=='disarmed':
                s.update(status='disarmed',owns_relay=False,locked=False,reason='explicit_disarm_no_write');return done()
            require(not (prev.get('owns_relay') and prev.get('commission_id')!=c['commission_id']),'explicit_disarm_before_new_owner')
            # Require a known, uniquely mapped secondary circuit; cloud audit and local shadow validate before arm.
            check_hardware(ha,c)
            actual=relay_state(ha);s['relay_on']=actual
            room,t=room_state(ha,now);s.update(room_c=room,room_observed_utc=t)
            s.update(decide(c['mode'],c['comfort_c'],c['frost_c'],room,actual))
            same=prev.get('commission_id')==c['commission_id']
            if c['phase']=='shadow':
                require(not prev.get('owns_relay'),'disarm_before_shadow')
                require(not (same and prev.get('locked')),'new_commission_id_required')
                s.update(status='shadow',locked=False,shadow_ready=True,readback_verified=True,reason='shadow_no_write');return done()
            require(same and not prev.get('locked'),'uncommissioned_or_latched_fault')
            require(prev.get('owns_relay') is True or (prev.get('phase')=='shadow' and prev.get('shadow_ready') is True and prev.get('revision')==c['revision'] and 0<=(now-stamp(prev.get('updated_utc'))).total_seconds()<=120),'fresh_matching_shadow_required')
            require(not (prev.get('owns_relay') and type(prev.get('relay_on')) is bool and prev['relay_on']!=actual),'unexpected_external_relay_change')
            s['owns_relay']=True; s['status']='armed_checking';atomic(path,s)
            if s['desired_on']!=actual:
                s['service_called']=True;s['locked']=True;atomic(path,s) # latch before uncertain I/O, even after abrupt restart
                _LOCAL.pump_write=True
                try:ha.call_binary_power_output_service(entity_id=PUMP,desired_on=s['desired_on'])
                finally:_LOCAL.pump_write=False
                for _ in range(12):
                    s['relay_on']=relay_state(ha)
                    if s['relay_on']==s['desired_on']:break
                    wait(.5)
                require(s['relay_on']==s['desired_on'],'relay_readback_mismatch')
            s.update(status='active',locked=False,physical_control_active=True,readback_verified=True,reason='thermostat_active');return done()
        except Exception as exc:
            reason=str(exc) if isinstance(exc,Hold) else type(exc).__name__
            if s.get('service_called') or reason=='unexpected_external_relay_change':s['locked']=True
            # No blind switch on stale/unknown state. Keep last relay position and flag a fault.
            s.update(status='locked' if s['locked'] else 'hold',physical_control_active=False,reason=reason);return done()

def get_local_status(identity):
    if not matches(identity):return None
    s=load_state(STATE_PATH)
    return s or None

def run_forever():
    from device_config import load_device_identity,load_cached_cloud_config
    import zigbee_manager as ha
    while True:
        try:
            identity=load_device_identity()
            if matches(identity):cycle(identity,load_cached_cloud_config(),ha)
        except Exception as exc:logging.warning('F171 00019 thermostat hold: %s',type(exc).__name__)
        time.sleep(POLL_SECONDS)
