"""F47 R126: only model 00013, ordinary local HA switch ON -> OFF pulse.

This is not Zigbee 0x42 or SONOFF 0xFC11 inching. It reproduces the confirmed
manual method through the customer's local Agent, not cloud-timed commands.
A software timeout cannot guarantee de-energization if Zigbee/HA loses power.
The installer-confirmed independent directional interlock, end stops and
floor pipe thermostat are assumed, NOT measured or certified here.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from servo_timed_command_definition import (
    UUID, IEEE, SERIAL, OPEN, CLOSE,
)

COMMAND="zigbee_servo_standard_pulse_v1"
SOURCE="f47_r126_00013_local"
FLOOR="sensor.owon_ths317_et_4"
PUMP="switch.sonoff_mini_zb2gs_spinac_5"
MAX_FLOOR=43.0
MAX_AGE_SECONDS=300
TARGET_MIN=15.0
TARGET_MAX=42.0
DEADBAND=0.2

class PulseStopped(RuntimeError):
    """An exact-model local pulse was blocked or its OFF could not be proven."""

def require(value:bool,reason:str)->None:
    if not value:
        raise PulseStopped(reason)

def _state(read_state,entity):
    result=read_state(entity)
    require(isinstance(result,dict),"ha_state_missing")
    state=str(result.get("state") or "").lower()
    require(state not in ("","unknown","unavailable"),"ha_state_unavailable")
    return state

def _both_off(read_state)->bool:
    return _state(read_state,OPEN)=="off" and _state(read_state,CLOSE)=="off"

def _reading(read_state):
    obj=read_state(FLOOR)
    require(isinstance(obj,dict),"sensor_state_missing")
    try:
        temp=float(obj.get("state"))
    except (ValueError,TypeError) as exc:
        raise PulseStopped("sensor_invalid") from exc
    require(math.isfinite(temp) and 0.0<=temp<MAX_FLOOR,"sensor_out_of_bounds")
    timestamp=obj.get("last_reported")
    require(isinstance(timestamp,str) and timestamp,"sensor_last_reported_missing")
    try:
        reported=datetime.fromisoformat(timestamp.replace("Z","+00:00"))
        require(reported.tzinfo is not None,"sensor_timestamp_timezone_missing")
        age=(datetime.now(timezone.utc)-reported).total_seconds()
    except ValueError as exc:
        raise PulseStopped("sensor_timestamp_invalid") from exc
    require(-10<=age<=MAX_AGE_SECONDS,"sensor_stale")
    return temp

def run(*,identity:dict,payload:dict,read_state:Callable[[str],dict],
        get_registry:Callable[[str],str|None],
        get_zha_devices:Callable[[],list[dict]],
        switch:Callable[[str,bool],Any],
        wait:Callable[[float],None]=time.sleep,
        physical_protections_confirmed:bool=False)->dict:
    """At most one pulse, duration owned by local HA Agent.

    Only the selected output is switched ON. Both directions are sent OFF in
    finally even on HA exceptions. Independent physical protection remains
    required since no software can force a radio relay off through a lost link.
    """
    require(identity.get("device_uuid")==UUID,"wrong_device")
    require(isinstance(payload,dict),"payload_not_dict")
    fields={"serial","device_uuid","ieee","device_reg_id","target_c",
            "seconds","command_type","source"}
    require(set(payload)==fields,"payload_invalid")
    require(payload["serial"]==SERIAL and payload["device_uuid"]==UUID
            and str(payload["ieee"]).lower()==IEEE,"identity_mismatch")
    require(payload["command_type"]==COMMAND and payload["source"]==SOURCE,
            "owner_mismatch")
    require(type(payload["seconds"]) in (int,float) and payload["seconds"]==1.0,
            "only_one_second")
    require(physical_protections_confirmed is True,
            "installer_protections_not_confirmed")
    raw=payload["target_c"]
    require(type(raw) in (float,int) and math.isfinite(raw),"target_not_numeric")
    target=float(raw)
    require(TARGET_MIN<=target<=TARGET_MAX,"target_out_of_bounds")
    reg1=get_registry(OPEN)
    reg2=get_registry(CLOSE)
    require(bool(reg1) and reg1==reg2 and payload["device_reg_id"]==reg1,
            "servo_registry_mismatch")
    devices=get_zha_devices()
    require(isinstance(devices,list),"zha_inventory_invalid")
    matches=[d for d in devices if isinstance(d,dict)
             and str(d.get("ieee") or "").lower()==IEEE]
    require(len(matches)==1,"sonoff_not_unique")
    device=matches[0]
    sig=device.get("signature") or {}
    require(isinstance(sig,dict),"sonoff_signature_invalid")
    require(str(sig.get("model") or device.get("model") or "")=="MINI-ZB2GS"
            and str(sig.get("manufacturer") or device.get("manufacturer") or "").upper()=="SONOFF",
            "sonoff_model_invalid")
    eps=sig.get("endpoints") or {}
    require(isinstance(eps,dict),"zha_endpoints_missing")
    for ep in (1,2):
        item=eps.get(ep,eps.get(str(ep)))
        require(isinstance(item,dict),"zha_endpoint_missing")
        raw_clusters=item.get("input_clusters") or []
        cl=set()
        for val in raw_clusters:
            try:
                cl.add(int(val,0) if isinstance(val,str) and val.startswith("0x") else int(val))
            except (TypeError,ValueError):
                continue
        require(6 in cl,"zha_onoff_endpoint_missing")
    require(_both_off(read_state),"relays_not_off")
    temp=_reading(read_state)
    direction=("close" if temp-target>DEADBAND else
               "open" if target-temp>DEADBAND else "hold")
    if direction=="hold":
        return {"action":"hold","actual_c":temp,"target_c":target,
                "actuation":False}
    pump_state=_state(read_state,PUMP)
    require(pump_state in ("on","off"),"pump_state_invalid")
    if pump_state=="off" and direction=="open":
        return {"action":"hold_pump_off","actuation":False}
    selected=OPEN if direction=="open" else CLOSE
    # Begin redundant OFF attempts before issuing the one ON.
    # This watchdog is NOT a hardware cutoff.
    stop=threading.Event()
    watchdog_attempted=threading.Event()
    errors=[]
    def watchdog():
        if not stop.wait(2.5):
            watchdog_attempted.set()
            for e in (OPEN,CLOSE):
                try:switch(e,False)
                except Exception as exc:errors.append(type(exc).__name__)
    thread=threading.Thread(target=watchdog,daemon=True,
                            name="00013-ordinary-servo-watchdog")
    failure=None
    off_proven=False
    thread.start()
    started=None
    try:
        switch(selected,True)
        started=time.monotonic()
        # Local Agent holds the 1-second interval; never cloud sleep.
        wait(1.0)
    except BaseException as exc:
        failure=exc
    finally:
        for e in (OPEN,CLOSE):
            try:switch(e,False)
            except Exception as exc:errors.append(type(exc).__name__)
        deadline=time.monotonic()+6.0
        while time.monotonic()<deadline:
            try:
                if _both_off(read_state):
                    off_proven=True
                    break
            except Exception:
                pass
            wait(0.2)
        if off_proven:stop.set()
        thread.join(timeout=3.5)
    if failure is not None:
        raise PulseStopped("switch_on_or_wait_failed") from failure
    require(not errors,"switch_off_failed")
    require(off_proven,"both_off_readback_failed")
    require(not watchdog_attempted.is_set(),"watchdog_required")
    require(started is not None,"pulse_not_started")
    return {"action":"ordinary_local_pulse","direction":direction,
            "seconds_requested":1.0,"actual_c":temp,"target_c":target,
            "relays_after":"off_off","device_native_auto_off_verified":False,
            "physical_protections_assumed":True}
