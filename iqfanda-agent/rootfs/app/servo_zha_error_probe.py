"""F47 R118 – safe remote ZHA error-log signature inspection for 00013.

Read-only. No actuator commands, no HA log content forwarded, no remote shell.
Only reports whitelisted category names / exception types, never full log lines.
"""
from __future__ import annotations
from collections import Counter
import re
from urllib import request, error
from typing import Callable, Any

SERIAL="F800711-TNG-00013"
UUID="014a0d23-d440-4131-abb4-28a860595043"
IEEE="08:fd:52:ff:fe:07:87:d9"
ALLOWED_EXCEPTION_TYPES={
    "AttributeError","ValueError","TypeError","KeyError","LookupError",
    "ZigbeeException","DeliveryError","TimeoutError","NotImplementedError",
    "MalformedCommand","UnknownCommand","Exception",
}
ERROR_TAGS={
    "zha_cluster_command":"issue_zigbee_cluster_command",
    "zha_integration":"homeassistant/components/zha",
    "zigpy_zcl":"zigpy/zcl",
    "zhaquirks_sonoff":"zhaquirks/sonoff",
    "manufacturer_cluster":"0xfc11",
    "sonoff_protocol":"protocoldata",
    "unsupported_command":"unsupported command",
    "command_not_found":"command not found",
    "no_such_command":"no such command",
    "unknown_command":"unknown command",
}
class ProbeError(RuntimeError): pass

def summarize(text: str) -> dict:
    if not isinstance(text,str): raise ProbeError("bad_error_log")
    # Deliberately discard whole lines immediately, return only bounded signatures.
    lines=text[-1024*1024:].lower().splitlines()
    count=Counter()
    kinds=set()
    for original in text[-1024*1024:].splitlines():
        line=original.lower()
        for key,token in ERROR_TAGS.items():
            if token in line: count[key]+=1
        for name in ALLOWED_EXCEPTION_TYPES:
            if name.lower()+":" in line or line.endswith(name.lower()):
                kinds.add(name)
    return {
        "lines_examined":len(lines),
        "recognised_tags":dict(sorted(count.items())),
        "exception_classes":sorted(kinds),
        "raw_lines_returned":0,
        "diagnostic_only":True,
    }

def inspect(identity: dict,payload: dict,read_zha_devices: Callable[[],list[dict]],
            read_ha_services: Callable[[],list[dict]],read_error_log: Callable[[],str]) -> dict:
    if str(identity.get("device_uuid") or "")!=UUID: raise ProbeError("wrong_fanda")
    if payload!={"serial":SERIAL,"mode":"read_only_zha_failure"}: raise ProbeError("unexpected_payload")
    devices=read_zha_devices()
    if not isinstance(devices,list): raise ProbeError("zha_inventory_invalid")
    matches=[d for d in devices if isinstance(d,dict) and
             str(d.get("ieee") or "").lower()==IEEE]
    if len(matches)!=1: raise ProbeError("sonoff_not_unique")
    d=matches[0]
    if str(d.get("model") or "").strip()!="MINI-ZB2GS": raise ProbeError("wrong_sonoff_model")
    services=read_ha_services()
    if not isinstance(services,list): raise ProbeError("ha_services_invalid")
    zha=next((s for s in services if isinstance(s,dict) and s.get("domain")=="zha"),None)
    if not isinstance(zha,dict): raise ProbeError("zha_service_unavailable")
    cmds=zha.get("services") or {}
    if not isinstance(cmds,dict): raise ProbeError("zha_commands_invalid")
    entry=cmds.get("issue_zigbee_cluster_command")
    if not isinstance(entry,dict): raise ProbeError("cluster_service_unavailable")
    fields=entry.get("fields") or {}
    field_names=sorted(str(x) for x in fields) if isinstance(fields,dict) else []
    try:
        signature=summarize(read_error_log())
        status="READ_ONLY_LOG_SIGNATURES_COLLECTED"
    except Exception as exc:
        # Never transmit raw exception from HTTP log endpoint.
        signature={"error_log_available":False,
                   "error_class":type(exc).__name__,
                   "raw_lines_returned":0}
        status="HA_LOG_UNAVAILABLE_SERVICE_FIELDS_ONLY"
    return {"serial":SERIAL,
            "ieee":IEEE,
            "zha_service_field_names":field_names,
            "log_signature":signature,
            "status":status,
            "actuation_allowed":False,
            "device_native_auto_off_verified":False,
            "electrical_interlock_verified":False,
            "relay_commands_sent":0}

def local_error_log(*,api_base:str,supervisor_token:str) -> str:
    if not (api_base.startswith("http://supervisor/") or
            api_base.startswith("https://supervisor/")):
        raise ProbeError("unexpected_ha_api_base")
    if not supervisor_token: raise ProbeError("no_supervisor_token")
    url=api_base.rstrip("/")+"/error_log"
    req=request.Request(url,method="GET",headers={
        "Authorization":"Bearer "+supervisor_token,
        "Accept":"text/plain",
        "User-Agent":"F47-R118-model00013-zha-readonly",
    })
    with request.urlopen(req,timeout=18) as res:
        # Never read unlimited log or emit contents.
        raw=res.read(2*1024*1024)
    return raw.decode("utf-8",errors="replace")
