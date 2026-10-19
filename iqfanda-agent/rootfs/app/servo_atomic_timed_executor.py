"""F47 R120: exact-model atomic Zigbee timed-off pulse executor candidate.

No autonomous scheduler. A pulse is allowed only with a local, installer-set
commissioning flag and a server-authorized source plus exact device identity.
The standard Zigbee timed command is NOT proof that firmware honors OFF.
A separate local backup OFF and off-state readback are obligatory.
"""
from __future__ import annotations
import math
import time
import threading
from datetime import datetime, timezone
from typing import Callable, Any
from servo_timed_command_definition import (
    SERIAL, UUID, IEEE, OPEN, CLOSE, make_timed_request, SafetyStop,
)

FLOOR="sensor.owon_ths317_et_4"
TARGET_MIN=15.0
TARGET_MAX=42.0
FLOOR_HIGH=43.0
HYSTERESIS=0.2
FRESHNESS_SECONDS=60.0
COMMAND="zigbee_servo_timed_step_v2"
SOURCE="f47_r120_00013_regulator"
MAX_OFF_READBACK_WAIT_S=6.0

def _fail_if(condition:bool, code:str)->None:
    if condition:raise SafetyStop(code)

def floor_reading(read_state:Callable[[str],dict])->float:
    result=read_state(FLOOR)
    _fail_if(not isinstance(result,dict),"floor_sensor_missing")
    try: actual=float(result.get("state"))
    except (TypeError,ValueError) as e:raise SafetyStop("floor_sensor_invalid") from e
    _fail_if(not math.isfinite(actual) or not 0<=actual<FLOOR_HIGH,"floor_sensor_high_or_invalid")
    ts=result.get("last_reported")
    _fail_if(not isinstance(ts,str) or not ts,"floor_last_reported_missing")
    try: timestamp=datetime.fromisoformat(ts.replace("Z","+00:00"))
    except ValueError as e:raise SafetyStop("floor_last_reported_invalid") from e
    _fail_if(timestamp.tzinfo is None,"floor_last_reported_naive")
    age=(datetime.now(timezone.utc)-timestamp).total_seconds()
    _fail_if(not -10<=age<=FRESHNESS_SECONDS,"floor_sensor_stale")
    return actual

def direction(actual:float,target:float)->str:
    _fail_if(not TARGET_MIN<=target<=TARGET_MAX,"target_invalid")
    if actual-target>HYSTERESIS:return "close"
    if target-actual>HYSTERESIS:return "open"
    return "hold"

def _both_off(read_state:Callable[[str],dict])->bool:
    return all(str(read_state(e).get("state") or "").lower()=="off" for e in (OPEN,CLOSE))

def execute(*, identity:dict,payload:dict,read_state:Callable[[str],dict],
            get_registry:Callable[[str],str|None], get_zha_devices:Callable[[],list[dict]],
            send_timed:Callable[[dict],Any], send_off:Callable[[str],Any],
            locally_commissioned:bool=False, wait:Callable[[float],None]=time.sleep)->dict:
    _fail_if(identity.get("device_uuid")!=UUID,"wrong_device")
    required={"serial","device_uuid","ieee","command_type","device_reg_id",
              "source","target_c","seconds"}
    _fail_if(set(payload)!=required,"payload_contract_changed")
    _fail_if(payload.get("serial")!=SERIAL or payload.get("device_uuid")!=UUID
             or str(payload.get("ieee") or "").lower()!=IEEE,"identity_mismatch")
    _fail_if(payload.get("command_type")!=COMMAND or payload.get("source")!=SOURCE,
             "wrong_command_owner")
    _fail_if(type(payload.get("seconds")) not in (int,float)
             or payload["seconds"]!=1.0,"only_one_second")
    _fail_if(locally_commissioned is not True,"commissioning_gate_not_open")
    raw_target=payload.get("target_c")
    _fail_if(type(raw_target) not in (float,int) or not math.isfinite(raw_target),
             "invalid_target_type")
    target=float(raw_target)
    _fail_if(not TARGET_MIN<=target<=TARGET_MAX,"target_not_safe")
    reg_open=get_registry(OPEN); reg_close=get_registry(CLOSE)
    _fail_if(not reg_open or reg_open!=reg_close or payload["device_reg_id"]!=reg_open,
             "relay_device_registry_mismatch")
    devices=get_zha_devices()
    _fail_if(not isinstance(devices,list),"invalid_zha_inventory")
    matching=[d for d in devices if isinstance(d,dict) and
              str(d.get("ieee") or "").lower()==IEEE]
    _fail_if(len(matching)!=1,"incorrect_zha_device_count")
    device=matching[0]
    model=str((device.get("signature") or {}).get("model") or device.get("model") or "")
    manufacturer=str((device.get("signature") or {}).get("manufacturer") or device.get("manufacturer") or "").upper()
    _fail_if(model!="MINI-ZB2GS" or manufacturer!="SONOFF","wrong_relay_model")
    _fail_if(device.get("available") is False,"relay_offline")
    _fail_if(not _both_off(read_state),"servo_relays_not_both_off")
    temp=floor_reading(read_state)
    move=direction(temp,target)
    if move=="hold":
        return {"action":"hold","actual_c":temp,"target_c":target}
    selected=1 if move=="open" else 2
    req=make_timed_request(device_uuid=UUID,ieee=IEEE,endpoint=selected,
                           relay_open_state="off",relay_close_state="off")
    # A second independent best-effort OFF follows the device-local timed packet.
    # It is NOT a guarantee during HA/Zigbee communication failure.
    errors=[]
    backup_stop=threading.Event()
    def backup_off():
        if not backup_stop.wait(3.0):
            for e in (OPEN,CLOSE):
                try:send_off(e)
                except Exception as exc:errors.append(type(exc).__name__)
    worker=threading.Thread(target=backup_off,daemon=True,name="00013-servo-backup-off")
    worker.start()
    failure=None
    seen_on=False
    self_off_seen=False
    try:
        send_timed(req)
        # Before ANY explicit OFF, witness the relay ON and then OFF.
        # If it never visibly switches, or stays ON, fail closed.
        witness_deadline=time.monotonic()+2.5
        while time.monotonic()<witness_deadline:
            try:
                states={e:str(read_state(e).get("state") or "").lower()
                        for e in (OPEN,CLOSE)}
                selected_entity=OPEN if move=="open" else CLOSE
                other_entity=CLOSE if move=="open" else OPEN
                if states[other_entity]!="off":
                    raise SafetyStop("opposite_output_energized")
                if states[selected_entity]=="on":
                    seen_on=True
                if seen_on and states[selected_entity]=="off":
                    self_off_seen=True
                    break
            except SafetyStop:
                raise
            except Exception:
                pass
            wait(0.15)
    except BaseException as exc:
        failure=exc
    finally:
        # Best-effort redundant OFF, even if timed ON/OFF worked.
        for e in (OPEN,CLOSE):
            try:send_off(e)
            except Exception as exc:errors.append(type(exc).__name__)
        deadline=time.monotonic()+MAX_OFF_READBACK_WAIT_S
        confirmed=False
        while time.monotonic()<deadline:
            try:
                if _both_off(read_state):
                    confirmed=True
                    break
            except Exception:
                pass
            wait(0.2)
        if confirmed:backup_stop.set()
        worker.join(timeout=4.5)
    if failure is not None:
        raise SafetyStop("timed_command_failed") from failure
    _fail_if(bool(errors),"backup_off_failure")
    _fail_if(not confirmed,"off_readback_not_confirmed")
    _fail_if(not seen_on or not self_off_seen,"native_timed_off_not_observed")
    return {"action":"timed_pulse","direction":move,"actual_c":temp,
            "target_c":target,"requested_on_time_s":1.0,
            "both_off_confirmed":True,
            "native_timed_off_observed":True}
