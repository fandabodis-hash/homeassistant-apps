"""F47 R125 — sanitized local Home Assistant/ZHA failure evidence, READ ONLY.

Never issues HA services, switch changes, Zigbee commands or firmware updates.
Only reads bounded tails of Home Assistant log files or Supervisor Core log.
Returns static categories and exception *class names*, not raw log lines.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable
import re
import urllib.request
from urllib.error import URLError, HTTPError

SERIAL="F800711-TNG-00013"
UUID="014a0d23-d440-4131-abb4-28a860595043"
IEEE="08:fd:52:ff:fe:07:87:d9"
LIMIT=4*1024*1024
CANDIDATES=(
    "/homeassistant/home-assistant.log",
    "/homeassistant/home-assistant.log.1",
    "/config/home-assistant.log",
    "/config/home-assistant.log.1",
)
KEYWORDS=(
    "zha", "zigpy", "zigbee", "on_with_timed_off", "cluster",
    "0x42", "keyerror", "exception", "error", "traceback",
)
EXCEPTIONS=(
    "KeyError","ValueError","TypeError","AttributeError","ZHAException",
    "ZigbeeException","DeliveryError","TimeoutError","CancelledError",
    "HomeAssistantError","NotImplementedError","Exception",
)
CATEGORIES=(
    ("command_42_unregistered", ("keyerror: 66", "keyerror: 0x42", "unknown command 66",
                                "unknown command 0x42")),
    ("unsupported_zigbee_command", ("unsup_cluster_command","unsupported cluster command",
                                   "unsupported command","unsupported_command")),
    ("zha_cluster_missing", ("cluster 6 not found", "cluster 0x0006 not found")),
    ("zigbee_delivery_failed", ("deliveryerror", "failed to deliver",
                               "message delivery failed", "no route")),
    ("zha_response_error", ("failed to issue cluster command with status",
                            "failed to issue cluster command")),
    ("zha_api_schema_error", ("extra keys not allowed", "invalid format",
                             "required key not provided", "voluptuous")),
    ("timeout", ("timeouterror", "asyncio.exceptions.timeout", "timed out")),
    ("zigpy_unknown_command", ("keyerror:", "unknown command")),
    ("zha_exception", ("zhaexception",)),
    ("zigpy_exception", ("zigbeeexception",)),
)
class ReadOnlyError(RuntimeError):pass

def summarize(raw: str) -> dict[str,Any]:
    if not isinstance(raw,str):
        raise ReadOnlyError("log_type_invalid")
    counts=Counter()
    exception_counts=Counter()
    relevant=0
    lines=raw[-LIMIT:].splitlines()[-18000:]
    for rawline in lines:
        line=rawline.lower()
        if not any(token in line for token in KEYWORDS):
            continue
        relevant+=1
        for name,patterns in CATEGORIES:
            if any(pat in line for pat in patterns):
                counts[name]+=1
        for exc in EXCEPTIONS:
            if re.search(r"(?<![a-z])"+exc.lower()+r"(?=[:(]|$)",line):
                exception_counts[exc]+=1
    # A signal appearing near the last logged failure is much more
    # meaningful than the number of unrelated historical log lines.
    order=(
        "command_42_unregistered","unsupported_zigbee_command",
        "zha_cluster_missing","zigbee_delivery_failed","zha_response_error",
        "zha_api_schema_error","timeout","zigpy_unknown_command",
        "zha_exception","zigpy_exception",
    )
    best=next((name for name in order if counts[name]),"unknown_no_specific_signature")
    return {
        "category":best,
        "category_counts":{name:counts[name] for name in order if counts[name]},
        "exception_counts":{key:exception_counts[key] for key in sorted(exception_counts)},
        "lines_scanned":len(lines),
        "relevant_lines":relevant,
        "raw_log_lines_returned":0,
    }

def read_file_tails() -> tuple[str,list[str]]:
    chunks=[]
    paths=[]
    for candidate in CANDIDATES:
        path=Path(candidate)
        try:
            if not path.is_file() or path.is_symlink():
                continue
            size=path.stat().st_size
            with path.open("rb") as f:
                f.seek(max(0,size-LIMIT))
                data=f.read(LIMIT)
            chunks.append(data.decode("utf-8",errors="replace"))
            paths.append("homeassistant_file")
        except (OSError,PermissionError):
            continue
    return "\n".join(chunks)[-LIMIT:], paths

def read_supervisor_core_log(token:str,timeout:float=12)->str:
    if not token:
        raise ReadOnlyError("supervisor_token_missing")
    req=urllib.request.Request(
        "http://supervisor/core/logs",
        headers={"Authorization":"Bearer "+token,
                 "Accept":"text/plain",
                 "User-Agent":"TNG-IQ-FANDA-Agent-00013-R125"},
        method="GET",
    )
    with urllib.request.urlopen(req,timeout=timeout) as response:
        return response.read(LIMIT).decode("utf-8",errors="replace")

def run(*,identity:dict,payload:dict,local_files:Callable[[],tuple[str,list[str]]],
        supervisor_logs:Callable[[],str])->dict:
    if identity.get("device_uuid")!=UUID:
        raise ReadOnlyError("wrong_model")
    if payload!={"serial":SERIAL,"mode":"zha_log_rootcause_read_only"}:
        raise ReadOnlyError("invalid_payload")
    sources=[]
    errors=[]
    raw=""
    try:
        text,found=local_files()
        if isinstance(text,str) and text:
            raw=text[-LIMIT:]
            sources.extend(found)
    except Exception as exc:
        errors.append("files_"+type(exc).__name__)
    if not raw:
        try:
            text=supervisor_logs()
            if isinstance(text,str) and text:
                raw=text[-LIMIT:]
                sources.append("supervisor_core_logs")
        except HTTPError as exc:
            errors.append("supervisor_http_"+str(int(exc.code)))
        except Exception as exc:
            errors.append("supervisor_"+type(exc).__name__)
    verdict=summarize(raw)
    verdict.update({
        "model":SERIAL,
        "sources_checked":len(CANDIDATES)+1,
        "source_types":sorted(set(sources)),
        "source_errors":sorted(set(errors)),
        "logs_found":bool(raw),
        "actuation":False,
        "commands_sent":0,
        "note":"Log classification is suggestive and must not be treated as motor OFF proof.",
    })
    return verdict
