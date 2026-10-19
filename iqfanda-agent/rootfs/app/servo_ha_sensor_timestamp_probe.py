"""F47 R131: read-only HA state timestamps for OWON underfloor sensor.

A displayed changing numeric value is not the same claim as a recent
last_reported timestamp. This reads both without inferring that the device is
broken or spoofing the timestamp.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Callable

UUID="014a0d23-d440-4131-abb4-28a860595043"
SERIAL="F800711-TNG-00013"
ENTITY="sensor.owon_ths317_et_4"

def _age(raw: Any) -> float | None:
    if not isinstance(raw,str) or not raw.strip():
        return None
    try:
        dt=datetime.fromisoformat(raw.replace("Z","+00:00"))
        if dt.tzinfo is None:
            return None
        age=(datetime.now(timezone.utc)-dt).total_seconds()
        return round(age,1) if -10<=age<=365*86400 else None
    except (ValueError,TypeError):
        return None

def inspect(*,identity:dict,payload:dict,get_state:Callable[[str],dict])->dict:
    if identity.get("device_uuid")!=UUID:
        return {"ok":False,"reason":"wrong_device","read_only":True}
    if payload!={"serial":SERIAL,"mode":"ha_sensor_timestamps_read_only"}:
        return {"ok":False,"reason":"invalid_payload","read_only":True}
    try:
        state=get_state(ENTITY)
        if not isinstance(state,dict):
            raise ValueError("ha_state_missing")
        raw=state.get("state")
        temp=float(raw)
        if not isfinite(temp) or not -10<=temp<43:
            raise ValueError("sensor_value_out_of_range")
        report_age=_age(state.get("last_reported"))
        changed_age=_age(state.get("last_changed"))
        updated_age=_age(state.get("last_updated"))
        return {
            "ok":True,
            "entity":ENTITY,
            "temperature_c":temp,
            "last_reported_age_seconds":report_age,
            "last_updated_age_seconds":updated_age,
            "last_changed_age_seconds":changed_age,
            "reported_within_300_seconds":(report_age is not None and report_age<=300),
            "source":"home_assistant_state_read",
            "read_only":True,
            "zigbee_direct_read_attempted":False,
            "relay_commands_sent":0,
        }
    except Exception as exc:
        return {"ok":False,"reason":"ha_sensor_state_"+type(exc).__name__,
                "read_only":True,"relay_commands_sent":0}
