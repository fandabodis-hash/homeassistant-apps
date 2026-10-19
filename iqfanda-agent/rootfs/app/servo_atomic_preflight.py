"""F47 R122: exact-model, read-only R120 servo commissioning preflight.

Mirrors the preconditions of servo_atomic_timed_executor before ANY timed
command is sent. This function never issues switch or ZHA write requests.
Report only static diagnostic reason codes; no HA credentials or full states.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Callable

from servo_timed_command_definition import SERIAL, UUID, IEEE, OPEN, CLOSE
from servo_atomic_timed_executor import FLOOR, PUMP, TARGET_MIN, TARGET_MAX, FLOOR_HIGH, FRESHNESS_SECONDS, HYSTERESIS

class Blocked(RuntimeError):
    pass

def stop(code: str) -> None:
    raise Blocked(code)

def preflight(
    *, identity: dict[str, Any], payload: dict[str, Any],
    read_state: Callable[[str], dict],
    get_registry: Callable[[str], str | None],
    get_zha_devices: Callable[[], list[dict]],
) -> dict:
    if identity.get("device_uuid") != UUID:
        stop("wrong_device")
    if not isinstance(payload, dict) or set(payload) != {"serial", "target_c"}:
        stop("payload_contract_invalid")
    if payload.get("serial") != SERIAL:
        stop("serial_mismatch")
    raw_target = payload.get("target_c")
    if type(raw_target) not in (int,float):
        stop("target_type_invalid")
    target=float(raw_target)
    if not isfinite(target) or not TARGET_MIN <= target <= TARGET_MAX:
        stop("target_not_safe")
    reg_open=get_registry(OPEN)
    reg_close=get_registry(CLOSE)
    if not reg_open or reg_close != reg_open:
        stop("relay_registry_mismatch")
    devices=get_zha_devices()
    if not isinstance(devices,list):
        stop("zha_inventory_invalid")
    matches=[d for d in devices if isinstance(d,dict)
             and str(d.get("ieee") or "").lower()==IEEE]
    if len(matches) != 1:
        stop("zha_device_not_unique")
    dev=matches[0]
    signature=dev.get("signature") or {}
    if not isinstance(signature,dict):
        stop("signature_invalid")
    model=str(signature.get("model") or dev.get("model") or "")
    manufacturer=str(signature.get("manufacturer") or dev.get("manufacturer") or "").upper()
    if model!="MINI-ZB2GS" or manufacturer!="SONOFF":
        stop("sonoff_model_mismatch")
    if dev.get("available") is False:
        stop("zigbee_device_offline")
    eps=signature.get("endpoints") or {}
    if not isinstance(eps,dict):
        stop("zha_endpoint_signature_missing")
    for ep in (1,2):
        entry=eps.get(ep,eps.get(str(ep)))
        if not isinstance(entry,dict):
            stop("zha_endpoint_missing")
        clusters=set()
        for raw in entry.get("input_clusters") or []:
            try:
                clusters.add(int(raw,0) if isinstance(raw,str) and raw.startswith("0x") else int(raw))
            except (TypeError,ValueError):
                continue
        if 6 not in clusters:
            stop("zha_onoff_cluster_missing")
    op=read_state(OPEN)
    cl=read_state(CLOSE)
    if not isinstance(op,dict) or not isinstance(cl,dict):
        stop("relay_states_missing")
    op_s=str(op.get("state") or "").lower()
    cl_s=str(cl.get("state") or "").lower()
    if op_s!="off" or cl_s!="off":
        stop("relay_not_both_off")
    floor=read_state(FLOOR)
    if not isinstance(floor,dict):
        stop("floor_sensor_missing")
    try:
        actual=float(floor.get("state"))
    except (ValueError,TypeError):
        stop("floor_sensor_invalid")
    if not isfinite(actual) or not 0<=actual<FLOOR_HIGH:
        stop("floor_sensor_high_or_invalid")
    timestamp=floor.get("last_reported")
    if not isinstance(timestamp,str) or not timestamp:
        stop("floor_last_reported_missing")
    try:
        updated=datetime.fromisoformat(timestamp.replace("Z","+00:00"))
    except ValueError:
        stop("floor_last_reported_invalid")
    if updated.tzinfo is None:
        stop("floor_last_reported_naive")
    age=(datetime.now(timezone.utc)-updated).total_seconds()
    if not -10<=age<=FRESHNESS_SECONDS:
        stop("floor_sensor_stale")
    move="close" if actual-target>HYSTERESIS else "open" if target-actual>HYSTERESIS else "hold"
    pump=read_state(PUMP)
    if not isinstance(pump,dict):
        stop("floor_pump_state_missing")
    pump_state=str(pump.get("state") or "").lower()
    if pump_state not in ("on","off"):
        stop("floor_pump_state_unavailable")
    if pump_state=="off" and move=="open":
        move="hold_pump_off"
    return {
        "ready":True,
        "reason":"preconditions_pass_read_only",
        "actual_c":actual,
        "target_c":target,
        "demand":move,
        "floor_sensor_last_reported_age_s":round(age,1),
        "pump_state":pump_state,
        "relay_open":"off",
        "relay_close":"off",
        "zha_endpoints":[1,2],
        "actuation":False,
        "native_timed_off_verified":False,
    }

def safe_preflight(**kwargs:Any)->dict:
    try:
        return preflight(**kwargs)
    except Blocked as exc:
        return {"ready":False,"reason":str(exc),"actuation":False}
    except Exception as exc:
        return {"ready":False,"reason":"preflight_runtime_"+type(exc).__name__,
                "actuation":False}
