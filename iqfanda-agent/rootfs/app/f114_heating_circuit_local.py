"""F114 R17: pilot local heating loop; no cloud actuation; explicit commissioning.
The original pure R16 controller is unchanged. Until an exact, fresh shadow
commissioning has been verified and a separate arm flag is received, no writes.
"""
from __future__ import annotations
import copy
import functools
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from heating_circuit_controller import decide, ConfigError, decimal_number

SERIAL='F800711-TNG-00015'
UUID='ec77027b-fea4-464b-bb11-94ea790f2706'
SITE='d6c0186c-c781-4b4a-993a-90408cce4b7e'
MODULE='301e87ea-9d94-465d-bad1-f6df3ed47100'
IDENTITY={'serial_number':SERIAL,'device_uuid':UUID,'site_id':SITE,'module_id':MODULE}
PUMP='switch.sonoff_zbminir2'
PUMP_IEEE='c0:2c:ed:ff:fe:e5:f5:b7'
PUMP_REG='74b4b101c814ae43dcf686609b740d61'
ROOM='sensor.ewelink_snzb_02p_teplota'
ROOM_IEEE='f0:44:d3:ff:fe:bf:48:92'
ROOM_REG='a5f0288ddb2fb413906ad47b5bf11793'
KEY='f114_heating_circuit_owner_v1'
DRAFT='heating_circuit_draft_v1'
STATUS_KEY='f114_heating_circuit_local_v1'
VERSION='0.1.182'
STATE_PATH=Path('/config/f114-heating-circuit-00015-state.json')
WEATHER_PATH=Path('/config/f114-heating-circuit-00015-weather.json')
# These limits are commissioning safeguards, not displayed user settings.
LIMITS={'room_min_c':5,'room_max_c':35,'outdoor_min_c':5,'outdoor_max_c':30}
ROOM_MAX_AGE=1800
WEATHER_MAX_AGE=7200
POLL_SECONDS=15
_LOCK=threading.RLock()
_LOCAL=threading.local()
_weather_attempt=0.0

class Hold(RuntimeError): pass

def require(ok, why):
    if not ok: raise Hold(why)

def utcnow(): return datetime.now(timezone.utc)

def load_json(path):
    try:
        with path.open(encoding='utf-8') as f: result=json.load(f)
        return result if isinstance(result,dict) else {}
    except (OSError,ValueError,TypeError): return {}

def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix=path.name+'.')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=True,allow_nan=False,separators=(',',':'));f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)

def matches(identity):
    if not isinstance(identity,dict): return False
    if str(identity.get('device_uuid'))!=UUID or str(identity.get('site_id'))!=SITE: return False
    for key in ('serial_number','device_serial_number'):
        if identity.get(key) and identity[key]!=SERIAL: return False
    return True

def revision(v):
    return hashlib.sha256(json.dumps(v,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def has_reference(v, refs):
    if isinstance(v,str): return v.strip().lower() in refs
    if isinstance(v,dict): return any(has_reference(k,refs) or has_reference(x,refs) for k,x in v.items())
    if isinstance(v,list): return any(has_reference(x,refs) for x in v)
    return False

REFS={PUMP,PUMP_IEEE,PUMP_REG}

def number(value,lo,hi):
    require(type(value) in (int,float), 'number_required')
    require(math.isfinite(value) and lo<=value<=hi,'number_outside_limit')
    return float(value)

def get_owner(config):
    settings=config.get('settings') if isinstance(config,dict) else None
    owner=settings.get(KEY) if isinstance(settings,dict) else None
    if owner is None: return None
    require(isinstance(owner,dict),'owner_contract_invalid')
    require(set(owner)=={'version','phase','identity','commission_id','pump','room','weather','secondary_circuit_verified','exclusive_ownership_confirmed'},'owner_fields_invalid')
    require(type(owner['version']) is int and owner['version']==1 and owner['identity']==IDENTITY,'owner_identity_invalid')
    require(owner['phase'] in ('shadow','armed','disarmed'),'owner_phase_invalid')
    require(isinstance(owner['commission_id'],str) and re.fullmatch(r'[0-9a-f]{32}',owner['commission_id']),'commission_id_invalid')
    require(owner['pump']=={'entity_id':PUMP,'ieee':PUMP_IEEE,'device_reg_id':PUMP_REG},'owner_pump_invalid')
    require(owner['room']=={'entity_id':ROOM,'ieee':ROOM_IEEE,'device_reg_id':ROOM_REG},'owner_room_invalid')
    require(type(owner['secondary_circuit_verified']) is bool and type(owner['exclusive_ownership_confirmed']) is bool,'commissioning_approval_invalid')
    if owner['phase']=='armed':
        require(owner['secondary_circuit_verified'] is True and owner['exclusive_ownership_confirmed'] is True,'commissioning_approval_missing')
    w=owner['weather'];require(isinstance(w,dict) and set(w)=={'source','latitude','longitude'} and w['source']=='open_meteo','weather_source_not_verified')
    number(w['latitude'],-90,90);number(w['longitude'],-180,180)
    return owner

def config_parts(identity, cloud):
    require(matches(identity),'foreign_device')
    require(isinstance(cloud,dict) and str(cloud.get('device_uuid'))==UUID and cloud.get('timezone')=='Europe/Prague','cloud_cache_identity_invalid')
    owner=get_owner(cloud)
    if owner is None: return None,None
    if owner['phase']=='disarmed':return owner,None
    modules=cloud.get('module_runtime_configurations'); require(isinstance(modules,list),'runtime_modules_missing')
    hp=[m for m in modules if isinstance(m,dict) and m.get('module_key')=='heat_pump']
    require(len(hp)==1 and hp[0].get('module_type')=='heat_pump' and hp[0].get('status') in ('configuration','configured','verified'),'hp_module_invalid')
    cfg=hp[0].get('configuration'); require(isinstance(cfg,dict) and type(cfg.get('generation')) is int and cfg['generation']==1 and cfg.get('client_visibility') is True,'hp_generation_invalid')
    pts=cfg.get('f49_r28_optional_points_v1'); bindings=cfg.get('schema_entity_bindings_v1')
    require(isinstance(pts,dict) and isinstance(bindings,dict),'mapping_missing')
    for slot,element,entity in [('heating_pump_switch','heating-circulation-pump-state',PUMP),('indoor_temperature','indoor-temperature',ROOM)]:
        p=pts.get(slot);b=bindings.get(element)
        require(isinstance(p,dict) and p.get('mode')=='entity' and p.get('entity_id')==entity,'optional_mapping_changed')
        require(isinstance(b,dict) and b.get('entity_id')==entity and b.get('transport')=='zigbee','native_mapping_changed')
    # F49 mode=none is authoritative. Do not delete the old alias or operate HP switch 2.
    old=pts.get('heating_switch_2');require(isinstance(old,dict) and old.get('mode')=='none' and not old.get('entity_id'),'legacy_heating_2_not_disabled')
    for k,v in pts.items():
        if k!='heating_pump_switch' and isinstance(v,dict) and v.get('mode')=='entity':
            require(not has_reference(v,REFS),'competing_optional_binding')
            if k in ('heating_pump_switch_2','heating_pump_switch_3'):require(not v.get('entity_id'),'multiple_heating_pumps_not_supported_in_pilot')
    for k,v in bindings.items():
        if k not in ('heating-circulation-pump-state','hp-heating-switch-2'):
            require(not has_reference(v,REFS),'competing_native_binding')
        if k=='hp-heating-switch-2' and has_reference(v,REFS):require(v.get('entity_id')==PUMP,'legacy_binding_conflict')
    for m in modules:
        if m is hp[0]:continue
        require(not has_reference(m.get('configuration'),REFS),'other_module_references_pump')
    # Reject other references in HP config outside the two maps, draft, and identity-only metadata.
    extra={k:v for k,v in cfg.items() if k not in ('schema_entity_bindings_v1','f49_r28_optional_points_v1',DRAFT)}
    require(not has_reference(extra,REFS),'other_hp_config_references_pump')
    settings=cloud['settings'];require(not has_reference({k:v for k,v in settings.items() if k!=KEY and k!='zigbee_device_names'},REFS),'other_device_settings_reference_pump')
    saved=cfg.get(DRAFT);require(isinstance(saved,dict) and set(saved)=={'identity','draft'} and saved['identity']==IDENTITY,'saved_draft_missing_or_foreign')
    draft=saved['draft'];require(isinstance(draft,dict) and set(draft)=={'schema_version','enabled','timezone','pump_mode','thermostat_mode','levels_c','season_mode','outdoor_threshold_c','week'},'draft_contract_invalid')
    # enabled=false is the original UI persistence contract. A separately
    # commissioned owner is the only authority, not a client-editable flag.
    require(type(draft['schema_version']) is int and draft['schema_version']==1 and draft['enabled'] is False and draft['timezone']=='Europe/Prague','draft_must_not_arm')
    require(draft.get('pump_mode') in ('manual','auto','off'),'pump_mode_missing')
    return owner,draft

def parse_stamp(raw):
    require(isinstance(raw,str),'measurement_timestamp_missing')
    dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
    require(dt.tzinfo is not None,'measurement_timestamp_no_timezone')
    return dt.astimezone(timezone.utc)

def measured_room(obj,now):
    require(isinstance(obj,dict) and obj.get('entity_id')==ROOM,'room_state_identity_invalid')
    attrs=obj.get('attributes');require(isinstance(attrs,dict) and attrs.get('device_class')=='temperature' and attrs.get('unit_of_measurement')=='°C','room_unit_invalid')
    try:v=float(obj['state']);stamp=parse_stamp(obj.get('last_reported') or obj.get('last_updated'))
    except (KeyError,TypeError,ValueError,Hold):return None,False,None
    age=(now-stamp).total_seconds()
    if not math.isfinite(v) or not -20<=v<=60 or not -10<=age<=ROOM_MAX_AGE:return None,False,round(age)
    return v,True,max(0,round(age))

def relay_state(obj):
    require(isinstance(obj,dict) and obj.get('entity_id')==PUMP and obj.get('state') in ('on','off'),'relay_state_unknown')
    return obj['state']=='on'

def check_hardware(ha,*,require_room_online):
    devices=ha.get_zha_devices();require(isinstance(devices,list),'zha_inventory_invalid')
    entries=ha.get_entity_registry_entries();require(isinstance(entries,list),'entity_registry_invalid')
    for entity,reg,ieee,manufacturer,model in [(PUMP,PUMP_REG,PUMP_IEEE,'sonoff','ZBMINIR2'),(ROOM,ROOM_REG,ROOM_IEEE,'ewelink','SNZB-02P')]:
        matches_reg=[x for x in entries if isinstance(x,dict) and x.get('entity_id')==entity]
        require(len(matches_reg)==1 and matches_reg[0].get('device_id')==reg and matches_reg[0].get('platform')=='zha' and not matches_reg[0].get('disabled_by'),'entity_registry_identity_changed')
        dev=[x for x in devices if isinstance(x,dict) and str(x.get('ieee','')).lower()==ieee]
        require(len(dev)==1 and dev[0].get('device_reg_id')==reg,'zigbee_device_identity_changed')
        if entity==PUMP or require_room_online:require(dev[0].get('available') is True,'zigbee_device_not_available')
        sig=dev[0].get('signature') or {}; require(str(dev[0].get('manufacturer') or sig.get('manufacturer') or '').lower()==manufacturer and str(dev[0].get('model') or sig.get('model') or '')==model,'zigbee_model_changed')
        if entity==PUMP:
            endpoints=sig.get('endpoints') or {}; found=False
            for ep in endpoints.values():
                if not isinstance(ep,dict):continue
                for c in ep.get('input_clusters') or []:
                    try:found=found or (int(c,0) if isinstance(c,str) else int(c))==6
                    except (TypeError,ValueError):pass
            require(found,'relay_onoff_cluster_not_verified')
    # Deliberately conservative pilot. We do not disable or modify any HA automation.
    # An enabled local automation or a running script is an unresolved owner.
    states=ha.home_assistant_request(path='/states');require(isinstance(states,list),'ha_automation_inventory_unavailable')
    for state in states:
        if not isinstance(state,dict):continue
        eid=str(state.get('entity_id') or '')
        if eid.startswith('automation.'):
            require(state.get('state')=='off','enabled_or_unknown_ha_automation_owner')
        if eid.startswith('script.'):
            require(state.get('state')=='off','running_or_unknown_ha_script_owner')
    return True

def weather_value(owner,now,*,fetch=None,cache_path=None,monotonic=time.monotonic):
    global _weather_attempt
    if cache_path is None: cache_path=WEATHER_PATH
    w=owner['weather'];key=revision(w);cached=load_json(cache_path)
    tick=monotonic()
    # Throttle failures too. No network needed for manual/off modes.
    if not _weather_attempt or tick-_weather_attempt>=300:
        _weather_attempt=tick
        try:
            if fetch is None:
                url='https://api.open-meteo.com/v1/forecast?'+urlencode({'latitude':w['latitude'],'longitude':w['longitude'],'current':'temperature_2m','temperature_unit':'celsius','timezone':'UTC'})
                with urlopen(Request(url,headers={'User-Agent':'TNG-IQ-FANDA-Agent'}),timeout=6) as r:
                    data=json.loads(r.read(65536))
            else:data=fetch(w)
            require(data.get('utc_offset_seconds')==0 and data.get('current_units',{}).get('temperature_2m')=='°C','weather_unit_or_timezone_invalid')
            cur=data['current'];v=number(cur['temperature_2m'],-60,65)
            dt=datetime.fromisoformat(cur['time'].replace('Z','+00:00'))
            if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
            age=(now-dt).total_seconds();require(-900<=age<=WEATHER_MAX_AGE,'weather_observation_stale')
            fresh={'key':key,'temperature_c':v,'observed_utc':dt.isoformat()};atomic(cache_path,fresh);cached=fresh
        except Exception as e:logging.warning('F114 R17 weather unavailable: %s',type(e).__name__)
    try:
        require(cached.get('key')==key,'weather_location_changed')
        age=(now-parse_stamp(cached['observed_utc'])).total_seconds()
        require(-900<=age<=WEATHER_MAX_AGE,'weather_cache_expired')
        return number(cached['temperature_c'],-60,65),True,max(0,round(age))
    except (KeyError,TypeError,ValueError,Hold):return None,False,None

def owner_claimed():
    """Called only for service calls: persistent marker protects during cache failures."""
    from device_config import load_device_identity,load_cached_cloud_config
    try:
        identity=load_device_identity()
        if not matches(identity):return False
    except Exception:
        # The marker is created only on exact pilot identity after all gates.
        return load_json(STATE_PATH).get('identity')==IDENTITY and load_json(STATE_PATH).get('owns_relay') is True
    state=load_json(STATE_PATH)
    if state.get('identity')==IDENTITY and state.get('owns_relay') is True:return True
    try:
        cloud=load_cached_cloud_config();owner=get_owner(cloud)
        return isinstance(cloud,dict) and cloud.get('device_uuid')==UUID and owner is not None and owner['phase']=='armed'
    except Exception:return False

def pilot_lock_required():
    # All pilot service writes share the same lock BEFORE owner activation.
    # A persistent owner marker also retains protection if identity loading fails.
    try:
        from device_config import load_device_identity
        return matches(load_device_identity())
    except Exception:
        state=load_json(STATE_PATH)
        return state.get('identity')==IDENTITY and state.get('owns_relay') is True


def guarded_ha_request(fn):
    """Serialize HA service writes; deny writes to pilot pump from all non-owner paths.
    Other devices and unrelated exact entity targets retain original behavior.
    """
    @functools.wraps(fn)
    def wrapped(*,path,method='GET',payload=None):
        if method.upper()=='GET' or not str(path).lstrip('/').startswith('services/'):
            return fn(path=path,method=method,payload=payload)
        # Foreign installations retain the original service behavior.
        # Serialize even unarmed pilot writes to avoid a shadow/arm race.
        if not pilot_lock_required() and not owner_claimed():return fn(path=path,method=method,payload=payload)
        with _LOCK:
            if owner_claimed():
                parts=str(path).lstrip('/').split('/')
                domain=parts[1] if len(parts)>1 else ''
                service=parts[2] if len(parts)>2 else ''
                target=payload if isinstance(payload,dict) else {}
                local=getattr(_LOCAL,'pump_write',False)
                if local:
                    require(domain=='switch' and service in ('turn_on','turn_off') and target=={'entity_id':PUMP},'local_pump_write_scope_violation')
                elif has_reference(target,REFS):
                    raise Hold('heating_circuit_local_owner_refuses_external_relay_write')
                elif domain in ('switch','light','homeassistant','group') and service in ('turn_on','turn_off','toggle'):
                    entities=target.get('entity_id');entities=[entities] if isinstance(entities,str) else entities
                    # Indirect device/area/group/all targets cannot be proven disjoint.
                    require(isinstance(entities,list) and bool(entities) and all(isinstance(x,str) and x.startswith(('switch.','light.')) and x not in REFS for x in entities) and not set(target)&{'device_id','area_id','target','floor_id','label_id'},'unscoped_switch_service_blocked_during_local_owner')
            return fn(path=path,method=method,payload=payload)
    return wrapped

def get_local_status(identity):
    if not matches(identity):return None
    s=load_json(STATE_PATH)
    if s.get('identity')!=IDENTITY:return None
    allowed=('identity','version','commission_id','phase','updated_utc','status','reason','owns_relay','physical_control_active','shadow_ready','pump_mode','thermostat_mode','target_c','room_c','outdoor_c','room_age_s','outdoor_age_s','relay_on','desired_on','draft_sha256','readback_verified','service_called','locked')
    return {k:s[k] for k in allowed if k in s}

def cycle(identity,cloud,ha,*,now=None,state_path=None,weather=None,wait=time.sleep):
    """One local cycle. No remote config fetch; only the atomic HA-local cache.
    Readback, config and all owner checks are performed before any physical command.
    """
    if not matches(identity):return None
    if now is None:now=utcnow()
    if state_path is None:state_path=STATE_PATH
    if weather is None:weather=weather_value
    with _LOCK:
        prev=load_json(state_path);old_owned=prev.get('identity')==IDENTITY and prev.get('owns_relay') is True
        s={'identity':dict(IDENTITY),'version':VERSION,'updated_utc':now.isoformat(),'phase':'uncommissioned','status':'not_commissioned','owns_relay':old_owned,'physical_control_active':False,'shadow_ready':False,'readback_verified':False,'service_called':False,'locked':False}
        def done():atomic(state_path,s);return s
        try:
            owner,draft=config_parts(identity,cloud)
            if owner is None:
                s['reason']='explicit_owner_required';return done()
            s.update(commission_id=owner['commission_id'],phase=owner['phase'])
            if owner['phase']=='disarmed':
                # Release ownership without changing relay. Explicit OFF must be selected
                # and read-back verified before a normal decommissioning operation.
                s.update(status='disarmed',owns_relay=False,reason='explicit_disarm_no_relay_write');return done()
            s.update(pump_mode=draft['pump_mode'],thermostat_mode=draft['thermostat_mode'],draft_sha256=revision(draft))
            check_hardware(ha,require_room_online=owner['phase']=='shadow')
            pump_on=relay_state(ha.get_entity_state(PUMP));s['relay_on']=pump_on
            try:room,room_ok,room_age=measured_room(ha.get_entity_state(ROOM),now)
            except Exception:room,room_ok,room_age=None,False,None
            s.update(room_c=room,room_age_s=room_age)
            # Establish weather proof even in shadow for a future Auto switch.
            out,out_ok,out_age=(weather(owner,now) if draft['pump_mode']=='auto' or owner['phase']=='shadow' else (None,False,None))
            s.update(outdoor_c=out,outdoor_age_s=out_age)
            # Named FVE boost is an explicit selected target, not automatic surplus detection.
            # The independently planned FVE surplus trigger is deliberately not introduced here.
            decision=decide(draft,limits=LIMITS,now=now,room_c=room,outdoor_c=out,room_fresh=room_ok,outdoor_fresh=out_ok,pump_on=pump_on,enabled=True,identity_ok=True,bindings_verified=True,exclusive_owner=True,safety_allows_on=(owner['phase']=='shadow' or (owner['secondary_circuit_verified'] is True and owner['exclusive_ownership_confirmed'] is True)),boost_authorized=draft['thermostat_mode']=='boost')
            s.update(target_c=decision.target_c,desired_on=decision.desired_on,reason=decision.reason)
            require(decision.desired_on is not None,'decision_not_authorized')
            invalid={'invalid_config','invalid_pump_mode','invalid_auto_configuration_or_measurement','outdoor_temperature_unavailable','room_temperature_unavailable','equipment_safety_block'}
            if owner['phase']=='shadow':
                if prev.get('commission_id')==owner['commission_id'] and prev.get('locked') is True:
                    s['locked']=True;raise Hold('new_commission_id_required_after_fault')
                s.update(status='shadow',shadow_ready=room_ok and out_ok and decision.reason not in invalid,readback_verified=True,owns_relay=old_owned)
                return done()
            # Only a shadow cycle for this exact commission ID and draft, or an already
            # owned cycle, can start. No control on a version install by itself.
            previous=prev.get('identity')==IDENTITY and prev.get('commission_id')==owner['commission_id']
            require(previous and (prev.get('owns_relay') is True or (prev.get('phase')=='shadow' and prev.get('shadow_ready') is True and prev.get('draft_sha256')==s['draft_sha256'])),'no_matching_shadow_commissioning')
            require(not (previous and prev.get('locked') is True),'latched_local_owner_fault_requires_recommission')
            if not prev.get('owns_relay'):
                require(room_ok and (out_ok or draft['pump_mode']!='auto') and decision.reason not in invalid,'shadow_measurement_changed_or_invalid_before_first_write')
            if prev.get('owns_relay') is True and type(prev.get('relay_on')) is bool and prev['relay_on']!=pump_on:
                s['locked']=True;raise Hold('unexpected_external_relay_change')
            # Claim persistently BEFORE the first physical service write.
            s['owns_relay']=True;s['status']='armed_checking';atomic(state_path,s)
            desired=decision.desired_on
            if desired!=pump_on:
                _LOCAL.pump_write=True
                try:
                    s['service_called']=True
                    ha.call_binary_power_output_service(entity_id=PUMP,desired_on=desired)
                finally:_LOCAL.pump_write=False
                confirmed=False
                for _ in range(12):
                    actual=relay_state(ha.get_entity_state(PUMP));s['relay_on']=actual
                    if actual==desired:confirmed=True;break
                    wait(.5)
                if not confirmed:s['locked']=True;raise Hold('relay_readback_mismatch')
            s.update(readback_verified=True,status='active',physical_control_active=True,shadow_ready=True)
            return done()
        except Exception as exc:
            # No blind ON/OFF when identity, ownership, config or HA state is unknown.
            # A failed service attempt is latched to avoid repeated uncontrolled writes.
            if s.get('service_called') or prev.get('locked') or s.get('locked'):s['locked']=True
            s.update(status='locked' if s['locked'] else 'hold',physical_control_active=False,reason=str(exc) if isinstance(exc,Hold) else type(exc).__name__)
            # Preserve last confirmed actual state only when it was read this cycle.
            return done()

def run_forever():
    from device_config import load_device_identity,load_cached_cloud_config
    import zigbee_manager as ha
    while True:
        try:
            identity=load_device_identity()
            if matches(identity):cycle(identity,load_cached_cloud_config(),ha)
        except Exception as e:logging.warning('F114 R17 local loop hold: %s',type(e).__name__)
        time.sleep(POLL_SECONDS)
