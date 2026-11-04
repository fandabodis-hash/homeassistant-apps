"""F47 R119: local one-second direction pulse, exact 00013 only.

Hardware protection was explicitly confirmed by installer: actuator directional
electrical interlock, motor end switches, and independent floor-pipe thermostat
which interrupts circulation pump. These physical safety protections are NOT
verified by this software. Zigbee OFF still remains a best-effort request.
Never claim the relay itself has a device-native OFF timer.

Cloud requests may ask for a pulse, but duration and OFF are owned locally.
This module never implements unattended scheduling; the VPS owner must be unique.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

SERIAL="F800711-TNG-00013"
DEVICE_UUID="014a0d23-d440-4131-abb4-28a860595043"
IEEE="08:fd:52:ff:fe:07:87:d9"
OPEN="switch.sonoff_mini_zb2gs_spinac_3"
CLOSE="switch.sonoff_mini_zb2gs_spinac_4"
FLOOR="sensor.owon_ths317_et_4"
PUMP="switch.sonoff_mini_zb2gs_spinac_5"
COMMAND="zigbee_servo_local_step_v1"
PULSE_S=1.0
TOLERANCE_C=0.2
MAX_FLOOR_C=43.0
MAX_RELAY_ON_S=8.0
MAX_TEMP_AGE_S=120.0

class UnsafePulse(RuntimeError): pass

def require(cond,reason):
    if not cond: raise UnsafePulse(reason)

def read_sensor(get_state:Callable[[str],dict]) -> tuple[float,dict]:
    raw=get_state(FLOOR)
    require(isinstance(raw,dict),"floor_sensor_missing")
    try:temp=float(raw.get("state"))
    except (TypeError,ValueError):raise UnsafePulse("floor_sensor_invalid")
    require(math.isfinite(temp) and 0<=temp<MAX_FLOOR_C,"floor_sensor_high_or_invalid")
    recorded=raw.get("last_reported") or raw.get("last_updated")
    require(isinstance(recorded,str) and recorded,"floor_sensor_not_timestamped")
    try:
        timestamp=datetime.fromisoformat(recorded.replace("Z","+00:00"))
        require(timestamp.tzinfo is not None,"floor_time_naive")
        age=(datetime.now(timezone.utc)-timestamp).total_seconds()
    except (TypeError,ValueError):raise UnsafePulse("floor_time_invalid")
    require(-10<=age<=MAX_TEMP_AGE_S,"floor_sensor_stale")
    return temp,raw

def demand(actual:float,target:float) -> str:
    require(15<=target<=42,"target_invalid")
    if actual>target+TOLERANCE_C:return "close"
    if actual<target-TOLERANCE_C:return "open"
    return "hold"

def wait_for_both_off(get_state,wait,deadline_s=8):
    deadline=time.monotonic()+deadline_s
    while time.monotonic()<deadline:
        try:
            states=[str(get_state(x).get("state") or "").lower() for x in (OPEN,CLOSE)]
            if states==["off","off"]:return True
        except Exception:
            pass
        wait(0.2)
    return False

def execute(*,identity:dict,payload:dict,get_state:Callable[[str],dict],
            get_device_id:Callable[[str],str|None],
            get_devices:Callable[[],list[dict]],
            switch:Callable[[str,bool],Any],
            wait:Callable[[float],None]=time.sleep,
            physical_protection_confirmed:bool=False) -> dict:
    require(identity.get("device_uuid")==DEVICE_UUID,"wrong_device")
    require(payload.get("serial")==SERIAL and payload.get("device_uuid")==DEVICE_UUID,
            "command_identity_mismatch")
    require(payload.get("ieee")==IEEE,"command_ieee_mismatch")
    require(payload.get("command_type")==COMMAND,"command_type_invalid")
    require(set(payload)<=set(("serial","device_uuid","ieee","command_type","target_c","seconds","device_reg_id","source")),
            "unexpected_command_fields")
    require(type(payload.get("seconds")) in (int,float) and payload["seconds"]==1.0,"only_one_second")
    require(payload.get("source")=="f47_r119_00013_local_regulator","wrong_regulation_owner")
    require(physical_protection_confirmed is True,"physical_protection_confirmation_missing")
    target=payload.get("target_c")
    require(type(target) in (int,float) and math.isfinite(target),"target_invalid")
    target=float(target)
    require(15<=target<=42,"target_range")
    inventory=get_devices()
    require(isinstance(inventory,list),"zigbee_inventory_invalid")
    matches=[d for d in inventory if isinstance(d,dict) and
             str(d.get("ieee") or "").lower()==IEEE]
    require(len(matches)==1,"servo_device_not_unique")
    d=matches[0];sig=d.get("signature") or {}
    require(str(sig.get("manufacturer") or d.get("manufacturer") or "").upper()=="SONOFF"
            and str(sig.get("model") or d.get("model") or "")=="MINI-ZB2GS",
            "wrong_servo_device")
    registry=[get_device_id(x) for x in (OPEN,CLOSE)]
    require(registry[0] and registry[0]==registry[1],"servo_reg_mismatch")
    require(payload.get("device_reg_id")==registry[0],"cloud_reg_mismatch")
    require(str(d.get("device_reg_id") or registry[0])==registry[0],"live_reg_mismatch")
    states=[str(get_state(x).get("state") or "").lower() for x in (OPEN,CLOSE)]
    require(states==["off","off"],"relay_not_both_off_before_start")
    actual,_=read_sensor(get_state)
    direction=demand(actual,target)
    if direction=="hold":
        return {"action":"hold","actual_c":actual,"target_c":target,"relays":"off_off"}
    # Heating pump may be OFF because independent pipe thermostat interrupted it.
    # Do not OPEN into a potentially unsafe or idle circuit.
    pump_state=str(get_state(PUMP).get("state") or "").lower()
    require(pump_state in ("on","off"),"pump_state_invalid")
    if pump_state=="off" and direction=="open":
        return {"action":"hold_pump_off","relays":"off_off"}
    entity=OPEN if direction=="open" else CLOSE
    # All following actions run in the local HA Agent; no cloud ON / OFF pair.
    # A local watchdog thread adds a second OFF attempt, but cannot prove
    # physical disconnect when HA/Zigbee is unavailable. Motor end switches
    # and the independent thermostat remain necessary physical protections.
    errors=[]
    stop=threading.Event()
    def emergency_off():
        if not stop.wait(3.0):
            for name in (OPEN,CLOSE):
                try:switch(name,False)
                except Exception as exc:errors.append(type(exc).__name__)
    thread=threading.Thread(target=emergency_off,name="00013-servo-pulse-off",daemon=True)
    try:
        thread.start()
        switch(entity,True)
        wait(PULSE_S)
    finally:
        for name in (OPEN,CLOSE):
            try:switch(name,False)
            except Exception as exc:errors.append(type(exc).__name__)
        # Watchdog continues if OFF cannot be proven; wait for its attempt.
        confirmed_off=wait_for_both_off(get_state,wait)
        if confirmed_off:stop.set()
        thread.join(timeout=5.0)
    require(not errors,"off_command_failure")
    require(confirmed_off,"off_readback_failed")
    actual_after,_=read_sensor(get_state)
    return {"action":"pulse","direction":direction,"pulse_seconds":PULSE_S,
            "actual_c":actual,"actual_after_c":actual_after,
            "target_c":target,"off_verified_by_ha":True,
            "device_native_auto_off_verified":False}
