"""F47 R150: one installer-observed 10-second CLOSE, exact 00013 only.

Deliberately separate from the automatic one-second regulator. Requires the
installer to verify independent 120s end-stops and motor direction interlock.
Ordinary HA Zigbee switch commands cannot guarantee OFF on lost Zigbee/radio,
power or HA. An independent thermal cutout remains mandatory.

Only run while the R127 auto scheduler is stopped by the guarded VPS wrapper.
This code NEVER energizes the OPEN output.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from servo_standard_pulse import PulseStopped, require
from servo_timed_command_definition import UUID, IEEE, SERIAL, OPEN, CLOSE

COMMAND="zigbee_servo_feedback_close_10_v1"
PURPOSE="floor_mixing_target_30_feedback_test"
SECONDS=10.0

def _state(read_state, entity):
    record=read_state(entity)
    require(isinstance(record,dict), "ha_state_missing")
    state=str(record.get("state") or "").lower()
    require(state in ("on","off"),"relay_state_unknown")
    return state

def _verify_off(read_state):
    return _state(read_state,OPEN)=="off" and _state(read_state,CLOSE)=="off"

def run(*, identity:dict, payload:dict,
        read_state:Callable[[str],dict],
        get_registry:Callable[[str],str|None],
        get_zha_devices:Callable[[],list[dict]],
        switch:Callable[[str,bool],Any],
        wait:Callable[[float],None]=time.sleep,
        physical_endstops_confirmed:bool=False)->dict:
    require(identity.get("device_uuid")==UUID,"wrong_device")
    require(physical_endstops_confirmed is True,"endstops_not_confirmed")
    require(isinstance(payload,dict),"payload_invalid")
    require(set(payload)=={"serial","device_uuid","ieee","device_reg_id","seconds","purpose","target_c"},
            "payload_invalid")
    require(payload["serial"]==SERIAL and payload["device_uuid"]==UUID and
            str(payload["ieee"]).lower()==IEEE and payload["purpose"]==PURPOSE,
            "identity_or_purpose_mismatch")
    require(type(payload["seconds"]) in (int,float) and payload["seconds"]==10.0,
            "only_10_seconds")
    reg1=get_registry(OPEN);reg2=get_registry(CLOSE)
    require(bool(reg1) and reg1==reg2 and payload["device_reg_id"]==reg1,
            "servo_registry_mismatch")
    devices=get_zha_devices()
    require(isinstance(devices,list),"zha_inventory_invalid")
    matches=[d for d in devices if isinstance(d,dict)
             and str(d.get("ieee") or "").lower()==IEEE]
    require(len(matches)==1,"servo_zha_identity_not_unique")
    d=matches[0];sig=d.get("signature") or {}
    require(isinstance(sig,dict),"signature_missing")
    require(str(sig.get("model") or d.get("model") or "")=="MINI-ZB2GS" and
            str(sig.get("manufacturer") or d.get("manufacturer") or "").upper()=="SONOFF",
            "sonoff_model_mismatch")
    eps=sig.get("endpoints") or {}
    require(isinstance(eps,dict),"sonoff_endpoints_missing")
    for ep in (1,2):
        info=eps.get(ep,eps.get(str(ep)))
        require(isinstance(info,dict),"sonoff_endpoint_missing")
        clusters=info.get("input_clusters") or []
        try:
            normalized={int(v,0) if isinstance(v,str) and v.lower().startswith("0x")
                        else int(v) for v in clusters}
        except (TypeError,ValueError):
            raise PulseStopped("sonoff_cluster_invalid")
        require(6 in normalized,"sonoff_onoff_missing")
    require(_verify_off(read_state),"outputs_not_both_off")
    # Exact setpoint: 30 C is checked again on VPS before queueing.
    require(type(payload["target_c"]) in (int,float) and
            float(payload["target_c"])==30.0,"target_must_be_30")
    from servo_floor_fresh import _age, _num
    from servo_standard_pulse import FLOOR, PUMP
    def live_floor():
        state=read_state(FLOOR)
        require(isinstance(state,dict),"floor_entity_missing")
        age=_age(state)
        require(0<=age<=300,"floor_sensor_stale")
        temp=_num(state.get("state"))
        require(temp<40.5,"floor_overheat")
        return temp,age
    floor_before,age_before=live_floor()
    require(floor_before>30.2,"no_close_demand")
    require(_state(read_state,PUMP)=="on","floor_pump_not_running")


    stop=threading.Event()
    watchdog_invoked=threading.Event()
    errors=[]
    def off_both():
        for e in (OPEN,CLOSE):
            try:
                switch(e,False)
            except Exception as exc:
                errors.append(type(exc).__name__)
    def watchdog():
        # Backup time limit if main code stalls; not a firmware cutoff.
        if not stop.wait(12.5):
            watchdog_invoked.set()
            off_both()
            # Attempt OFF again after several seconds if a radio command fails.
            if not stop.wait(3.0):
                off_both()
    worker=threading.Thread(target=watchdog,daemon=True,
                            name="00013-supervised-close-off-watchdog")
    worker.start()
    failure=None
    entered=False
    off_proven=False
    floor_after=None
    age_after=None
    try:
        # Only CLOSE receives an ON request. Never ON for OPEN.
        switch(CLOSE,True)
        entered=True
        wait(SECONDS)
    except BaseException as exc:
        failure=exc
    finally:
        off_both()
        deadline=time.monotonic()+8.0
        while time.monotonic()<deadline:
            try:
                if _verify_off(read_state):
                    off_proven=True
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if off_proven:
            stop.set()
        worker.join(timeout=5.0)
    # Observe ten seconds after both OFF. No repeat ON in this operation.
    if off_proven and not errors:
        wait(10.0)
        try:
            floor_after,age_after=live_floor()
        except Exception:
            floor_after=None
            age_after=None
    if failure is not None:
        raise PulseStopped("close_on_or_wait_failed") from failure
    require(not errors,"close_off_command_failed")
    require(off_proven,"both_off_readback_failed")
    require(not watchdog_invoked.is_set(),"watchdog_required")
    require(entered,"close_on_not_sent")
    return {
        "action":"feedback_close_10_observe_10",
        "direction":"close",
        "seconds_requested":10.0,
        "observation_wait_s":10.0,
        "floor_c_before":floor_before,
        "floor_c_after":floor_after,
        "floor_age_before_s":round(age_before,1),
        "floor_age_after_s":round(age_after,1) if age_after is not None else None,
        "temperature_change_confirmed":(floor_after is not None and floor_after!=floor_before),
        "target_c":30.0,
        "relays_after":"off_off",
        "software_off_readback_verified":True,
        "physical_valve_movement_verified":False,
        "firmware_auto_off_verified":False,
        "hardware_endstops_assumed":True,
    }
