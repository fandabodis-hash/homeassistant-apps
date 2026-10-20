"""F47 R171 candidate: 00013 local-only 5 s pulse / 15 s OFF interval mixing.

NO cloud call in the control loop. The one-time installer enable action is
distinct from regulation. HA REST/WebSocket calls address the local HA core.
Never rely on Zigbee/HA software OFF as an independent electrical cutout.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

UUID = "014a0d23-d440-4131-abb4-28a860595043"
SERIAL = "F800711-TNG-00013"
IEEE = "08:fd:52:ff:fe:07:87:d9"
REGISTRY = "ac89cb802d360fa4d08b89ea31673f76"
OPEN = "switch.sonoff_mini_zb2gs_spinac_3"
CLOSE = "switch.sonoff_mini_zb2gs_spinac_4"
PUMP = "switch.sonoff_mini_zb2gs_spinac_5"
FLOOR = "sensor.owon_ths317_et_4"
CONFIG_PATH = Path("/config/f47_00013_local_mixing_3s15s.json")
STATUS_PATH = Path("/config/f47_00013_local_mixing_status.json")
LAST_PULSE_PATH = Path("/config/f47_00013_local_mixing_last_pulse.json")
PULSE_SECONDS = 5.0
PAUSE_SECONDS = 15.0
TARGET_C = 30.0
DEADBAND_C = 0.3
MAX_AGE_SECONDS = 300.0
# Do not require a new OWON report after every 3 s movement. Limit travel
# while using one valid sample to 4 x 3 s = 12 s (10% of 120 s stroke).
# R170: allow timed movement without a fresh temperature report after
# four strokes, but NEVER permit unbounded travel from stale feedback.
# A single sample can authorize at most 60 s (half of the 120 s stroke).
# This guard is deliberately conservative until physical position feedback
# and an independently verified actuator calibration are available.
MAX_PULSES_PER_READING = 12
MAX_SAMPLE_AGE_FOR_PULSE_SECONDS = 180.0
MAX_FLOOR_C = 43.0
MODEL_LOCK = threading.Lock()

class LocalSafetyStop(RuntimeError):
    pass

def require(ok: bool, reason: str) -> None:
    if not ok:
        raise LocalSafetyStop(reason)

def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w",encoding="utf-8") as out:
        json.dump(value,out,ensure_ascii=False,sort_keys=True)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    os.chmod(tmp,0o600)
    os.replace(tmp,path)

def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError,TypeError):
        return {}

def _valid_target(raw) -> bool:
    return type(raw) in (float,int) and math.isfinite(raw) and 15.0<=raw<=42.0

def _local_target(identity:dict) -> float:
    config=_load(CONFIG_PATH)
    require(_valid_config(config,identity),"not_armed")
    return float(config["target_c"])

def _status(kind: str, **data) -> None:
    config=_load(CONFIG_PATH)
    target=(float(config["target_c"]) if _valid_config(config,{"device_uuid":UUID})
            else TARGET_C)
    _atomic(STATUS_PATH,{"mode":"local_ha_only","status":kind,
        "updated_utc":datetime.now(timezone.utc).isoformat(),
        "pulse_seconds":PULSE_SECONDS,"pause_seconds":PAUSE_SECONDS,
        "target_c":target,**data})

def _valid_config(data:dict, identity:dict) -> bool:
    if identity.get("device_uuid")!=UUID or not isinstance(data,dict):
        return False
    if not _valid_target(data.get("target_c")):
        return False
    copy=dict(data)
    copy["target_c"]=TARGET_C
    return copy == {
        "serial":SERIAL,"device_uuid":UUID,"enabled":True,
        "target_c":TARGET_C,"pulse_seconds":PULSE_SECONDS,
        "pause_seconds":PAUSE_SECONDS,"source":"local_agent_ha_only_v1",
    }

def is_armed(identity:dict)->bool:
    return _valid_config(_load(CONFIG_PATH),identity)

def _ha():
    from zigbee_manager import (get_entity_state,get_entity_device_id,get_zha_devices,
                                call_binary_power_output_service)
    return get_entity_state,get_entity_device_id,get_zha_devices,call_binary_power_output_service

def _read_entity(read,entity) -> dict:
    obj=read(entity)
    require(isinstance(obj,dict),"ha_state_not_object")
    return obj

def _relay(read,entity) -> str:
    val=str(_read_entity(read,entity).get("state") or "").lower()
    require(val in ("on","off"),"relay_state_unknown")
    return val

def _off_verified(read) -> bool:
    # Avoid short-circuiting: check both independent channels.
    left=_relay(read,OPEN)
    right=_relay(read,CLOSE)
    return left=="off" and right=="off"

def _floor(read):
    obj=_read_entity(read,FLOOR)
    try:
        value=float(obj.get("state"))
        stamp=datetime.fromisoformat(str(obj.get("last_reported") or "").replace("Z","+00:00"))
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
    except (TypeError,ValueError,OverflowError):
        raise LocalSafetyStop("floor_invalid")
    require(stamp.tzinfo is not None and 0<=age<=MAX_AGE_SECONDS,"floor_stale")
    require(math.isfinite(value) and 0<=value<MAX_FLOOR_C,"floor_overheat_or_invalid")
    return value,round(age,1),stamp.timestamp()

def _confirm_hardware(registry,devices):
    left=registry(OPEN)
    right=registry(CLOSE)
    require(left==REGISTRY and right==REGISTRY,"servo_registry_drift")
    matches=[d for d in devices() if isinstance(d,dict) and
             str(d.get("ieee") or "").lower()==IEEE]
    require(len(matches)==1,"servo_ieee_not_unique")
    d=matches[0];sig=d.get("signature") or {}
    require(isinstance(sig,dict),"servo_signature_missing")
    require(str(sig.get("model") or d.get("model") or "")=="MINI-ZB2GS" and
            str(sig.get("manufacturer") or d.get("manufacturer") or "").upper()=="SONOFF",
            "servo_device_not_sonoff")
    eps=sig.get("endpoints") or {}
    require(isinstance(eps,dict),"servo_endpoints_missing")
    for index in (1,2):
        ep=eps.get(index,eps.get(str(index)))
        require(isinstance(ep,dict),"servo_endpoint_missing")
        clusters=set()
        for raw in ep.get("input_clusters") or []:
            try:
                clusters.add(int(raw,0) if isinstance(raw,str) and raw.startswith("0x") else int(raw))
            except (TypeError,ValueError):
                continue
        require(6 in clusters,"servo_onoff_cluster_missing")

def enable(*,identity:dict,payload:dict,read=None,registry=None,devices=None) -> dict:
    """One-time commissioning only: NO motor ON and no cloud periodic control."""
    require(identity.get("device_uuid")==UUID,"wrong_device")
    expected={"serial":SERIAL,"mode":"local_ha_only_v1",
              "target_c":TARGET_C,"pulse_seconds":PULSE_SECONDS,
              "pause_seconds":PAUSE_SECONDS}
    require(type(payload)==dict and payload==expected,"enable_payload_invalid")
    require(not CONFIG_PATH.exists(),"local_configuration_already_exists")
    if read is None or registry is None or devices is None:
        read,registry,devices,_=_ha()
    _confirm_hardware(registry,devices)
    require(_off_verified(read),"relays_not_both_off")
    temp,age,_=_floor(read)
    require(_relay(read,PUMP)=="on","floor_pump_not_running")
    _status("armed",floor_c=temp,sensor_age_seconds=age,
            note="read_only_preflight_complete_no_relay_command")
    _atomic(CONFIG_PATH,{"serial":SERIAL,"device_uuid":UUID,"enabled":True,
        "target_c":TARGET_C,"pulse_seconds":PULSE_SECONDS,
        "pause_seconds":PAUSE_SECONDS,"source":"local_agent_ha_only_v1"})
    return {"status":"armed","actuation":False,"runtime":"local_ha_only",
            "pulse_seconds":PULSE_SECONDS,"pause_seconds":PAUSE_SECONDS,
            "target_c":TARGET_C}

def _off_both(switch,errors:list) -> None:
    for entity in (OPEN,CLOSE):
        try:
            switch(entity_id=entity,desired_on=False)
        except Exception as exc:
            errors.append(type(exc).__name__)

def _pulse(read,switch,entity,wait=time.sleep,monotonic=time.monotonic) -> None:
    require(entity in (OPEN,CLOSE),"wrong_direction")
    require(_off_verified(read),"pre_pulse_not_both_off")
    stopped=threading.Event()
    watchdog_used=threading.Event()
    off_errors=[]
    def watchdog():
        if not stopped.wait(4.5):
            watchdog_used.set()
            _off_both(switch,off_errors)
    guard=threading.Thread(target=watchdog,name="00013-local-servo-off-guard",daemon=True)
    _status("pulse_active",direction="open" if entity==OPEN else "close")
    guard.start()
    problem=None
    try:
        switch(entity_id=entity,desired_on=True)
        wait(PULSE_SECONDS)
    except BaseException as exc:
        problem=exc
    finally:
        _off_both(switch,off_errors)
        deadline=monotonic()+6
        proven=False
        while monotonic()<deadline:
            try:
                if _off_verified(read):
                    proven=True
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if proven:
            stopped.set()
        guard.join(timeout=2.0)
    require(problem is None,"pulse_on_or_wait_failed")
    require(proven,"off_readback_unproven")
    require(not off_errors,"off_command_failed")
    require(not watchdog_used.is_set(),"off_watchdog_required")

def _cycle_unlocked(*,identity:dict,read,registry,devices,switch,wait=time.sleep) -> dict:
    target=_local_target(identity)
    _confirm_hardware(registry,devices)
    require(_off_verified(read),"relays_not_both_off")
    temp,age,reported_epoch=_floor(read)
    if _relay(read,PUMP)!="on":
        return {"action":"hold_pump_off","temperature_c":temp,"sensor_age_seconds":age}
    delta=temp-target
    if abs(delta)<=DEADBAND_C:
        return {"action":"hold_in_band","temperature_c":temp,"sensor_age_seconds":age}
    # Use the latest actually reported OWON sample on the 3s/15s cadence.
    # Limit movement when HA/Zigbee temperature updates are slow. Never
    # repeatedly advance blindly for the full servo stroke on old data.
    if age>MAX_SAMPLE_AGE_FOR_PULSE_SECONDS:
        return {"action":"hold_sensor_too_old_for_pulse",
                "temperature_c":temp,"sensor_age_seconds":age,
                "max_pulse_sample_age_s":MAX_SAMPLE_AGE_FOR_PULSE_SECONDS}
    direction="close" if delta>0 else "open"
    last=_load(LAST_PULSE_PATH)
    if LAST_PULSE_PATH.exists():
        previous=last.get("last_report_epoch")
        require(type(previous) in (float,int) and math.isfinite(previous),
                "pulse_ledger_corrupt")
        if reported_epoch<previous:
            return {"action":"hold_owon_report_regressed",
                    "temperature_c":temp,"sensor_age_seconds":age}
        if reported_epoch==previous:
            count=last.get("pulse_count",1)  # 0.1.162 legacy record = 1 pulse
            require(type(count) is int and 0<=count<=MAX_PULSES_PER_READING,
                    "pulse_ledger_count_invalid")
            if last.get("direction")!=direction:
                return {"action":"hold_direction_changed_wait_new_owon",
                        "temperature_c":temp,"sensor_age_seconds":age}
        else:
            count=0
    else:
        count=0
    if count>=MAX_PULSES_PER_READING:
        return {"action":"hold_sample_motion_budget",
                "temperature_c":temp,"sensor_age_seconds":age,
                "completed_pulses_on_sample":count,
                "max_pulses_per_report":MAX_PULSES_PER_READING}
    entity=CLOSE if delta>0 else OPEN
    _pulse(read,switch,entity,wait=wait)
    new_count=count+1
    _atomic(LAST_PULSE_PATH,{"last_report_epoch":reported_epoch,
            "direction":direction,"pulse_count":new_count,
            "last_target_c":target})
    return {"action":direction,
            "temperature_c":temp,"sensor_age_seconds":age,
            "seconds":PULSE_SECONDS,
            "completed_pulses_on_sample":new_count,
            "max_pulses_per_report":MAX_PULSES_PER_READING}

def cycle(*,identity:dict,read,registry,devices,switch,wait=time.sleep) -> dict:
    with MODEL_LOCK:
        return _cycle_unlocked(identity=identity,read=read,registry=registry,
                               devices=devices,switch=switch,wait=wait)

def set_local_target(*,identity:dict,payload:dict,read=None) -> dict:
    """One-off configuration transport. NO relay command, NO cloud control loop.

    A separate VPS-side installer must compare the requested target against
    the exact 00013 portal setting before dispatching this command.
    """
    require(identity.get("device_uuid")==UUID,"wrong_device")
    require(isinstance(payload,dict) and
            set(payload)=={"serial","old_target_c","target_c","mode"},
            "target_payload_invalid")
    require(payload["serial"]==SERIAL and payload["mode"]=="local_setpoint_only",
            "target_scope_invalid")
    require(_valid_target(payload["target_c"]),"target_value_invalid")
    require(_valid_target(payload["old_target_c"]),"previous_target_invalid")
    if read is None:
        read,*_=_ha()
    with MODEL_LOCK:
        config=_load(CONFIG_PATH)
        require(_valid_config(config,identity),"not_armed")
        current=float(config["target_c"])
        require(current==float(payload["old_target_c"]),"target_revision_mismatch")
        require(_off_verified(read),"setpoint_relays_not_both_off")
        if current != float(payload["target_c"]):
            config["target_c"]=float(payload["target_c"])
            _atomic(CONFIG_PATH,config)
        previous=_load(STATUS_PATH)
        if previous.get("status")=="locked":
            # Setpoint updates must never unlock a motor failure.
            previous.update({"target_c":float(payload["target_c"]),
                             "updated_utc":datetime.now(timezone.utc).isoformat()})
            _atomic(STATUS_PATH,previous)
        else:
            _status("hold",reason="local_setpoint_updated_wait_owon")
        return {"action":"local_setpoint_saved","target_c":float(payload["target_c"]),
                "old_target_c":current,"actuation":False,
                "runtime":"local_ha_only","motor_commands_sent":0}

def run_forever(*,read=None,registry=None,devices=None,switch=None,
                sleep=time.sleep,load_identity=None,pulse_wait=time.sleep) -> None:
    """Always-on local thread. No cloud import/call anywhere in this loop."""
    if load_identity is None:
        from device_config import load_device_identity
        load_identity=load_device_identity
    if read is None or registry is None or devices is None or switch is None:
        read,registry,devices,switch=_ha()
    active=False
    while True:
        try:
            identity=load_identity()
            if not is_armed(identity):
                active=False
                sleep(2.0)
                continue
            state=_load(STATUS_PATH)
            if state.get("status") in ("locked","pulse_active"):
                # An interrupted ON must NEVER resume after Agent restart.
                if state.get("status")=="pulse_active":
                    errs=[]
                    _off_both(switch,errs)
                    _status("locked",reason="previous_pulse_interrupted",
                            off_commands_ok=not errs)
                sleep(15.0)
                continue
            if not active:
                _status("local_running")
                active=True
            try:
                detail=cycle(identity=identity,read=read,registry=registry,
                             devices=devices,switch=switch,wait=pulse_wait)
                _status("local_running",**detail)
            except LocalSafetyStop as exc:
                reason=str(exc)
                if reason in ("floor_stale","floor_invalid","floor_overheat_or_invalid",
                              "floor_pump_not_running"):
                    # Sensor and pump gating are holds only when BOTH outputs OFF.
                    try:
                        require(_off_verified(read),"relay_off_unconfirmed_sensor_hold")
                        _status("hold",reason=reason)
                    except Exception:
                        errors=[]
                        _off_both(switch,errors)
                        _status("locked",reason="relay_off_unconfirmed_sensor_hold",
                                off_commands_ok=not errors)
                else:
                    errors=[]
                    _off_both(switch,errors)
                    _status("locked",reason=reason,off_commands_ok=not errors)
            except Exception as exc:
                errors=[]
                _off_both(switch,errors)
                _status("locked",reason="unclassified_"+type(exc).__name__,
                        off_commands_ok=not errors)
            # 15 s OFF minimum after every 5 s actuation; on HOLD same pacing.
            sleep(PAUSE_SECONDS)
        except Exception:
            logging.exception("F47 00013 LOCAL MIXING supervisor failure; no blind motion")
            sleep(PAUSE_SECONDS)
