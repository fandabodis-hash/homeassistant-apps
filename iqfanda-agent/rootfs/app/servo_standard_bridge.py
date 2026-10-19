"""F47 R126 bridge for ordinary switch.turn_on / switch.turn_off servo pulse.

Exact device only; commissioned-once gate prevents untested automation.
The local HA Agent owns the pulse time. The remote portal may request a step
but cannot choose direct ON/OFF or bypass safe local model checks.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from servo_standard_pulse import (
    run as local_pulse, PulseStopped, COMMAND, SOURCE,
)
from servo_timed_command_definition import UUID, IEEE, SERIAL
# Defer HA dependencies until a real callback is invoked. This leaves pure
# safety-contract tests completely isolated from customer HA libraries.
def _read_entity(entity):
    from zigbee_manager import get_entity_state
    return get_entity_state(entity)

def _read_registry(entity):
    from zigbee_manager import get_entity_device_id
    return get_entity_device_id(entity)

def _read_devices():
    from zigbee_manager import get_zha_devices
    return get_zha_devices()

def _switch_entity(entity,on):
    from zigbee_manager import call_binary_power_output_service
    return call_binary_power_output_service(entity_id=entity,desired_on=on)


MARKER=Path("/config/f47_00013_standard_pulse_commission.json")
COMMISSION_COMMAND="zigbee_servo_standard_commission_v1"
RUN_COMMAND=COMMAND

def read_commission()->bool:
    try:
        marker=json.loads(MARKER.read_text(encoding="utf-8"))
        started=datetime.fromisoformat(marker["time_utc"])
        return bool(
            marker.get("serial")==SERIAL
            and marker.get("device_uuid")==UUID
            and marker.get("ieee")==IEEE
            and marker.get("software_pulse_returned_off") is True
            and started.tzinfo is not None
            and timedelta(0)<=datetime.now(timezone.utc)-started<=timedelta(days=30)
        )
    except (OSError,ValueError,TypeError,KeyError):
        return False

def write_commission()->None:
    MARKER.parent.mkdir(parents=True,exist_ok=True)
    data={"serial":SERIAL,"device_uuid":UUID,"ieee":IEEE,
          "software_pulse_returned_off":True,
          "time_utc":datetime.now(timezone.utc).isoformat()}
    tmp=MARKER.with_suffix(".tmp")
    tmp.write_text(json.dumps(data,sort_keys=True),encoding="utf-8")
    os.chmod(tmp,0o600)
    os.replace(tmp,MARKER)

def read_floor_fresh(*,identity:dict,require_direct:bool=False)->dict:
    """Read fresh HA state or uncached physical ZHA attribute (never a switch)."""
    from servo_floor_fresh import read
    from zigbee_manager import home_assistant_websocket_request
    return read(
        identity=identity,
        get_state=_read_entity,
        get_registry=_read_registry,
        get_devices=_read_devices,
        read_cluster=lambda payload:home_assistant_websocket_request(
            command_type="zha/devices/clusters/attributes/value",
            payload=payload),
        require_direct=require_direct,
    )

def floor_aware_state(*,identity:dict):
    """Allow the preflight and the local step to use newly observed ZHA values."""
    cache={}
    def get(entity):
        if entity!="sensor.owon_ths317_et_4":
            return _read_entity(entity)
        if "floor" not in cache:
            result=read_floor_fresh(identity=identity)
            from servo_floor_fresh import SensorNotFresh
            if result.get("fresh") is not True:
                raise SensorNotFresh(result.get("reason") or "floor_not_fresh")
            previous=_read_entity(entity)
            if not isinstance(previous,dict):
                raise SensorNotFresh("floor_entity_missing")
            adjusted=dict(previous)
            adjusted["state"]=str(result["temperature_c"])
            if result.get("direct_read") is True:
                # The direct (allow_cache=False) Zigbee network read happened now.
                adjusted["last_reported"]=datetime.now(timezone.utc).isoformat()
            cache["floor"]=adjusted
        return cache["floor"]
    return get

def execute(*,identity:dict,command_type:str,payload:dict[str,Any])->dict:
    if identity.get("device_uuid")!=UUID:
        raise PulseStopped("wrong_device")
    if command_type not in (COMMISSION_COMMAND,RUN_COMMAND):
        raise PulseStopped("unsupported_command")
    if command_type==RUN_COMMAND and not read_commission():
        raise PulseStopped("not_commissioned")
    if command_type==COMMISSION_COMMAND and read_commission():
        raise PulseStopped("already_commissioned")
    required={"serial","device_uuid","ieee","device_reg_id","target_c","seconds"}
    if not isinstance(payload,dict) or set(payload)!=required:
        raise PulseStopped("payload_invalid")
    fields={**payload,"command_type":COMMAND,"source":SOURCE}
    state_reader=floor_aware_state(identity=identity)
    # Only stale/absent sensor evidence may be skipped. This guard executes
    # BEFORE the motor control function is invoked, so no relay ON is possible.
    # Failed OFF/readback, Zigbee control, or any other error still locks out.
    from servo_floor_fresh import SensorNotFresh, FLOOR
    from servo_timed_command_definition import OPEN, CLOSE
    try:
        state_reader(FLOOR)
    except SensorNotFresh:
        if command_type!=RUN_COMMAND:
            raise
        reg_open=_read_registry(OPEN)
        reg_close=_read_registry(CLOSE)
        if not reg_open or reg_open!=reg_close or payload["device_reg_id"]!=reg_open:
            raise PulseStopped("stale_sensor_registry_unconfirmed")
        open_state=_read_entity(OPEN)
        close_state=_read_entity(CLOSE)
        if not isinstance(open_state,dict) or not isinstance(close_state,dict) or (
            str(open_state.get("state") or "").lower()!="off" or
            str(close_state.get("state") or "").lower()!="off"
        ):
            raise PulseStopped("stale_sensor_relay_off_unconfirmed")
        return {"action":"hold_sensor_stale",
                "reason":"no_fresh_temperature_no_motor_command",
                "relays_after":"off_off","actuation":False,
                "native_off_verified":False,"sensor_fresh":False,
                "model":SERIAL}
    result=local_pulse(
        identity=identity,payload=fields,
        read_state=state_reader,
        get_registry=_read_registry,
        get_zha_devices=_read_devices,
        switch=_switch_entity,
        physical_protections_confirmed=True,
    )
    if command_type==COMMISSION_COMMAND:
        if result.get("action")!="ordinary_local_pulse" or result.get("relays_after")!="off_off":
            raise PulseStopped("commissioning_movement_not_confirmed")
        write_commission()
        return {**result,"commissioning":"software_pulse_verified_by_HA",
                "physical_direction_observed_by_installer":False}
    return result
