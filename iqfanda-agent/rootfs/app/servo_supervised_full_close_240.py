"""F47 R144: one installer-supervised 240-second CLOSE, exact 00013 only.

Deliberately separate from the automatic one-second regulator. Requires the
installer to verify independent 240s end-stops and motor direction interlock.
Ordinary HA Zigbee switch commands cannot guarantee OFF on lost Zigbee/radio,
power or HA. An independent thermal cutout remains mandatory.

Only run while the R127 auto scheduler is stopped by the guarded VPS wrapper.
This code NEVER energizes the OPEN output. This keeps the CLOSE relay\nrequested ON continuously for 240 seconds; the mechanical limit switch may\ndisconnect motor power sooner and is NOT confirmed by software.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from servo_standard_pulse import PulseStopped, require
from servo_timed_command_definition import UUID, IEEE, SERIAL, OPEN, CLOSE

COMMAND="zigbee_servo_supervised_full_close_240_v1"
PURPOSE="installer_supervised_close_240s"
SECONDS=240.0

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
    require(type(payload["seconds"]) in (int,float) and payload["seconds"]==240.0,
            "only_240_seconds")
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
        if not stop.wait(242.0):
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
    if failure is not None:
        raise PulseStopped("close_on_or_wait_failed") from failure
    require(not errors,"close_off_command_failed")
    require(off_proven,"both_off_readback_failed")
    require(not watchdog_invoked.is_set(),"watchdog_required")
    require(entered,"close_on_not_sent")
    return {
        "action":"installer_supervised_close",
        "direction":"close",
        "seconds_requested":240.0,
        "relays_after":"off_off",
        "software_off_readback_verified":True,
        "physical_valve_movement_verified":False,
        "firmware_auto_off_verified":False,
        "hardware_endstops_assumed":True,
    }
