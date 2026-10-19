"""F47 R127: exact model 00013, obtain fresh OWON floor-temperature reading.

If HA entity last_reported is >300 s old, ask the SAME ZHA device for its
Temperature Measurement cluster measured_value (0x0402/0x0000) using an
uncached READ.  This is a read-only Zigbee command, never a switch command.
If it fails, prevent motor pulses rather than trusting stale HA temperature.
"""
from __future__ import annotations
from datetime import datetime,timezone
from math import isfinite
from typing import Callable,Any
import re

MODEL_UUID="014a0d23-d440-4131-abb4-28a860595043"
FLOOR="sensor.owon_ths317_et_4"
MAX_AGE_S=300
MAX_FLOOR_C=43
ZCL_TEMPERATURE=0x0402
ZCL_MEASURED_VALUE=0x0000

class SensorNotFresh(RuntimeError):
    pass

def classify_ha_read_error(exc: Exception) -> str:
    """Only stable public error categories; NEVER forward exception messages.

    Home Assistant WebSocket errors may contain device and user information.
    This classifier emits no raw string, addresses or credentials.
    """
    normalized=str(exc).lower()[:8192]
    category="unknown_ha_error"
    rules=(
        ("ws_command_unknown",("unknown_command","unknown command",
                               "unknown websocket command","not a registered command")),
        ("ws_not_authorized",("unauthorized","not authorized",
                             "requires admin","permission denied","forbidden")),
        ("zha_integration_unavailable",("zha integration is not loaded",
                                        "zha integration not found","gateway not found")),
        ("zha_device_unavailable",("device not found","device is not available",
                                   "device unavailable","unknown device")),
        ("zha_cluster_missing",("cluster 1026 not found","cluster 0x0402 not found",
                                "cluster not found","unknown cluster")),
        ("zha_attribute_unsupported",("unsupported_attribute","unsupported attribute",
                                      "unsup_attribute","attribute not supported")),
        ("zha_zigbee_no_reply",("deliveryerror","failed to deliver",
                               "delivery failed","no route")),
        ("zha_zigbee_timeout",("timed out","timeout","deadline exceeded")),
        ("zha_read_failed",("failed to read attribute","failed to read from",
                            "readattributesresponse","read_attribute")),
        ("ws_internal_error",("internal_error","internal server error","server got itself")),
        ("ws_connection_failure",("websocket neni dostupny","connection closed",
                                   "connection refused","broken pipe")),
    )
    for name,patterns in rules:
        if any(p in normalized for p in patterns):
            category=name
            break
    return category


def _stop(reason:str):
    raise SensorNotFresh(reason)

def _age(record:dict)->float:
    raw=record.get("last_reported")
    if not isinstance(raw,str) or not raw:
        return float("inf")
    try:
        stamp=datetime.fromisoformat(raw.replace("Z","+00:00"))
        if stamp.tzinfo is None:
            return float("inf")
        return (datetime.now(timezone.utc)-stamp).total_seconds()
    except (ValueError,TypeError):
        return float("inf")

def _num(raw:Any) -> float:
    try: value=float(raw)
    except (ValueError,TypeError) as exc: raise SensorNotFresh("floor_value_invalid") from exc
    if not isfinite(value) or not -10<value<MAX_FLOOR_C:
        _stop("floor_value_out_of_range")
    return value

def locate_sensor(*, device_id:str,devices:list[dict])->tuple[str,int]:
    if not isinstance(devices,list):
        _stop("zha_inventory_invalid")
    candidates=[]
    for d in devices:
        if not isinstance(d,dict):
            continue
        entities=d.get("entities") or []
        linked=d.get("device_reg_id")==device_id
        if not linked and isinstance(entities,list):
            linked=any(isinstance(ent,dict)
                       and ent.get("entity_id")==FLOOR
                       for ent in entities)
        if not linked:
            continue
        ieee=str(d.get("ieee") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){7}",ieee):
            _stop("floor_zha_ieee_invalid")
        signature=d.get("signature") or {}
        eps=signature.get("endpoints") or {}
        if not isinstance(eps,dict):
            _stop("floor_zha_signature_missing")
        endpoints=[]
        for k,v in eps.items():
            if not isinstance(v,dict):
                continue
            clusters=set()
            for raw in v.get("input_clusters") or []:
                try:
                    clusters.add(int(raw,0) if isinstance(raw,str)
                                 and raw.lower().startswith("0x") else int(raw))
                except (TypeError,ValueError):
                    continue
            if ZCL_TEMPERATURE in clusters:
                try:
                    ep=int(k)
                    if ep>0:endpoints.append(ep)
                except (ValueError,TypeError):
                    pass
        if len(endpoints)!=1:
            _stop("floor_zha_temperature_endpoint_not_unique")
        candidates.append((ieee,endpoints[0]))
    if len(candidates)!=1:
        _stop("floor_zha_device_not_unique")
    return candidates[0]

def read(*,identity:dict,get_state:Callable[[str],dict],
         get_registry:Callable[[str],str|None],
         get_devices:Callable[[],list[dict]],
         read_cluster:Callable[[dict],Any],
         require_direct:bool=False)->dict:
    if identity.get("device_uuid")!=MODEL_UUID:
        _stop("wrong_model")
    record=get_state(FLOOR)
    if not isinstance(record,dict):
        _stop("floor_entity_missing")
    original=_num(record.get("state"))
    age=_age(record)
    if 0<=age<=MAX_AGE_S and not require_direct:
        return {"temperature_c":original,"source":"ha_last_reported",
                "reported_age_s":round(age,1),"fresh":True,"direct_read":False}
    device_id=get_registry(FLOOR)
    if not isinstance(device_id,str) or not device_id:
        _stop("floor_device_registry_missing")
    ieee,endpoint=locate_sensor(device_id=device_id,devices=get_devices())
    payload={"ieee":ieee,"endpoint_id":endpoint,"cluster_id":ZCL_TEMPERATURE,
             "cluster_type":"in","attribute":ZCL_MEASURED_VALUE}
    try:
        raw=read_cluster(payload)
    except Exception as exc:
        raise SensorNotFresh("floor_zha_direct_read_"+type(exc).__name__+"_"+classify_ha_read_error(exc)) from exc
    if type(raw) not in (str,int):
        _stop("floor_zha_direct_read_invalid_response")
    try:centidegrees=int(str(raw).strip())
    except ValueError: _stop("floor_zha_direct_read_not_number")
    if centidegrees in (0x8000,-32768) or not -1000<centidegrees<MAX_FLOOR_C*100:
        _stop("floor_zha_direct_read_out_of_range")
    temp=centidegrees/100.0
    return {"temperature_c":temp,"source":"direct_zha_uncached",
            "reported_age_s":round(age,1) if isfinite(age) else None,
            "fresh":True,"direct_read":True,"temperature_endpoint":endpoint}

def safe_read(**kwargs)->dict:
    try:
        return read(**kwargs)
    except SensorNotFresh as exc:
        return {"fresh":False,"reason":str(exc),"direct_read":False}
    except Exception as exc:
        return {"fresh":False,"reason":"floor_read_"+type(exc).__name__,
                "direct_read":False}
