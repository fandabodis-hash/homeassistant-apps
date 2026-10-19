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
from zigbee_manager import (
    get_entity_state, get_entity_device_id, get_zha_devices,
    call_binary_power_output_service,
)

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
    result=local_pulse(
        identity=identity,payload=fields,
        read_state=get_entity_state,
        get_registry=get_entity_device_id,
        get_zha_devices=get_zha_devices,
        switch=lambda entity,on:call_binary_power_output_service(
            entity_id=entity,desired_on=on),
        physical_protections_confirmed=True,
    )
    if command_type==COMMISSION_COMMAND:
        if result.get("action")!="ordinary_local_pulse" or result.get("relays_after")!="off_off":
            raise PulseStopped("commissioning_movement_not_confirmed")
        write_commission()
        return {**result,"commissioning":"software_pulse_verified_by_HA",
                "physical_direction_observed_by_installer":False}
    return result
