"""F47 R120: model-only HA Agent routing for supervised commissioning and
subsequent atomic ZHA timed-off servo steps.

No effect on existing Zigbee commands or any other FANDA.
"""
from __future__ import annotations
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from servo_atomic_timed_executor import (
    execute, COMMAND, SOURCE, UUID, IEEE, SERIAL, SafetyStop,
)
from zigbee_manager import (
    get_entity_state, get_entity_device_id, get_zha_devices,
    call_home_assistant_service, call_binary_power_output_service,
)

APPROVAL_FILE=Path("/config/f47_00013_servo_native_timed_commission.json")
COMMISSION_COMMAND="zigbee_servo_commission_v1"
RUN_COMMAND=COMMAND

def read_marker() -> bool:
    try:
        d=json.loads(APPROVAL_FILE.read_text(encoding="utf-8"))
        stamp=datetime.fromisoformat(d.get("commissioned_at",""))
        age=datetime.now(timezone.utc)-stamp
        return (d.get("serial")==SERIAL and d.get("device_uuid")==UUID and
                d.get("ieee")==IEEE and d.get("native_timed_off_observed") is True and
                timedelta(0)<=age<=timedelta(days=30))
    except (OSError,ValueError,TypeError,KeyError):
        return False

def save_marker() -> None:
    APPROVAL_FILE.parent.mkdir(parents=True,exist_ok=True)
    data={"serial":SERIAL,"device_uuid":UUID,"ieee":IEEE,
          "commissioned_at":datetime.now(timezone.utc).isoformat(),
          "native_timed_off_observed":True}
    fd,name=tempfile.mkstemp(prefix="00013-native-timed-",suffix=".tmp",
                           dir=str(APPROVAL_FILE.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as out:
            json.dump(data,out,sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name,0o600)
        os.replace(name,APPROVAL_FILE)
    finally:
        try:os.unlink(name)
        except FileNotFoundError:pass

def run(*,identity:dict,command_type:str,payload:dict[str,Any])->dict:
    if identity.get("device_uuid")!=UUID:
        raise SafetyStop("wrong_model")
    if command_type not in (COMMISSION_COMMAND,RUN_COMMAND):
        raise SafetyStop("unsupported_command")
    if command_type==RUN_COMMAND and not read_marker():
        raise SafetyStop("native_timed_off_not_commissioned")
    if command_type==COMMISSION_COMMAND and read_marker():
        raise SafetyStop("already_commissioned")
    required={"serial","device_uuid","ieee","device_reg_id","target_c","seconds"}
    if set(payload)!=required:
        raise SafetyStop("unexpected_payload")
    # The actual cloud command type is not accepted as a servo direction.
    # The local module determines direction from the real HA floor sensor.
    approved={**payload,"command_type":RUN_COMMAND,"source":SOURCE}
    def send_timed(action:dict):
        return call_home_assistant_service(
            domain="zha", service="issue_zigbee_cluster_command",payload=action)
    def send_off(entity:str):
        return call_binary_power_output_service(entity_id=entity,desired_on=False)
    result=execute(identity=identity,payload=approved,read_state=get_entity_state,
                   get_registry=get_entity_device_id,get_zha_devices=get_zha_devices,
                   send_timed=send_timed,send_off=send_off,
                   locally_commissioned=True)
    if command_type==COMMISSION_COMMAND:
        if result.get("native_timed_off_observed") is not True:
            raise SafetyStop("commissioning_native_off_not_observed")
        save_marker()
        return {**result,"commissioning":"native_1s_off_observed"}
    return result
