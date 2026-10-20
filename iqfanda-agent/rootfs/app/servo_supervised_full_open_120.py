"""F47 R146: one installer-supervised 120-second OPEN, exact 00013 only.

Deliberately separate from the automatic one-second regulator. Requires the
installer to verify independent 120s end-stops and motor direction interlock.
Ordinary HA Zigbee switch commands cannot guarantee OFF on lost Zigbee/radio,
power or HA. An independent thermal cutout remains mandatory.

Only run while the R127 auto scheduler is stopped by the guarded VPS wrapper.
This code NEVER energizes the CLOSE output.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from servo_standard_pulse import PulseStopped, require
from servo_timed_command_definition import UUID, IEEE, SERIAL, OPEN, CLOSE

COMMAND="zigbee_servo_supervised_full_open_120_v1"
PURPOSE="installer_supervised_open_120s"
SECONDS=120.0

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
    require(set(payload)=={"serial","device_uuid","ieee","device_reg_id","seconds","purpose"},
            "payload_invalid")
    require(payload["serial"]==SERIAL and payload["device_uuid"]==UUID and
            str(payload["ieee"]).lower()==IEEE and payload["purpose"]==PURPOSE,
            "identity_or_purpose_mismatch")
    require(type(payload["seconds"]) in (int,float) and payload["seconds"]==120.0,
            "only_120_seconds")
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
    # A full opening admits hotter tank water. Refuse unsafe or stale floor
    # temperatures; check at most every two seconds during motion. Home
    # Assistant state freshness is not the same as a physical safety cutout.
    from datetime import datetime, timezone
    from math import isfinite
    FLOOR="sensor.owon_ths317_et_4"
    PUMP="switch.sonoff_mini_zb2gs_spinac_5"
    MAX_C=40.5
    def verify_floor_safe():
        record=read_state(FLOOR)
        require(isinstance(record,dict),"floor_sensor_missing")
        try:
            measured=float(record.get("state"))
        except (TypeError,ValueError):
            raise PulseStopped("floor_temperature_invalid")
        require(isfinite(measured) and 0<=measured<MAX_C,
                "floor_temperature_high_or_invalid")
        raw=record.get("last_reported")
        require(isinstance(raw,str) and raw,"floor_last_reported_missing")
        try:
            ts=datetime.fromisoformat(raw.replace("Z","+00:00"))
            age=(datetime.now(timezone.utc)-ts).total_seconds()
        except (ValueError,TypeError):
            raise PulseStopped("floor_timestamp_invalid")
        require(ts.tzinfo is not None and -10<=age<=300,
                "floor_sensor_stale")
        require(_state(read_state,PUMP)=="on","floor_pump_not_running")
        return measured
    floor_start=verify_floor_safe()


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
        if not stop.wait(122.0):
            watchdog_invoked.set()
            off_both()
            # Attempt OFF again after several seconds if a radio command fails.
            if not stop.wait(3.0):
                off_both()
    worker=threading.Thread(target=watchdog,daemon=True,
                            name="00013-supervised-open-off-watchdog")
    worker.start()
    failure=None
    entered=False
    off_proven=False
    floor_end=None
    try:
        # Only OPEN receives an ON request. Never ON for CLOSE.
        switch(OPEN,True)
        entered=True
        # Monitor temperature and state in 2 s chunks. End at the fixed
        # monotonic deadline rather than adding HA response time to runtime.
        deadline=time.monotonic()+SECONDS
        while True:
            left=deadline-time.monotonic()
            if left<=0:
                break
            verify_floor_safe()
            wait(min(2.0,left))
        floor_end=verify_floor_safe()
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
    if failure is not None:
        raise PulseStopped("open_on_or_wait_failed") from failure
    require(not errors,"open_off_command_failed")
    require(off_proven,"both_off_readback_failed")
    require(not watchdog_invoked.is_set(),"watchdog_required")
    require(entered,"open_on_not_sent")
    return {
        "action":"installer_supervised_open",
        "direction":"open",
        "seconds_requested":120.0,
        "relays_after":"off_off",
        "software_off_readback_verified":True,
        "physical_valve_movement_verified":False,
        "firmware_auto_off_verified":False,
        "hardware_endstops_assumed":True,
        "floor_temperature_start_c":floor_start,
        "floor_temperature_end_c":floor_end,
        "floor_limit_c":MAX_C,
    }
