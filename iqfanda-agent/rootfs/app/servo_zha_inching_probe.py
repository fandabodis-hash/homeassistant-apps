"""F47 R112 – read-only ZHA SONOFF MINI-ZB2GS servo capability inspector.

Works with the Agent's existing zigbee_manager.get_zha_devices() snapshot.
NEVER sends a cluster command or a switch command. A manufacturer cluster's
presence alone does not attest that device-native OFF/inching is active.
"""
from __future__ import annotations

from typing import Any

SERIAL = "F800711-TNG-00013"
DEVICE_UUID = "014a0d23-d440-4131-abb4-28a860595043"
IEEE = "08:fd:52:ff:fe:07:87:d9"
MODEL = "MINI-ZB2GS"
MANUFACTURER = "SONOFF"
CUSTOM_CLUSTER = 0xFC11


class InvalidZhaDevice(RuntimeError):
    pass


def _cluster_id(raw: Any) -> int | None:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw, 0) if raw.lower().startswith("0x") else int(raw)
        except ValueError:
            return None
    if isinstance(raw, dict):
        return _cluster_id(raw.get("id", raw.get("cluster_id")))
    return None


def _endpoints(device: dict) -> list[dict]:
    signature = device.get("signature")
    if not isinstance(signature, dict):
        return []
    endpoints = signature.get("endpoints")
    if not isinstance(endpoints, dict):
        return []
    result = []
    for raw_number, raw in endpoints.items():
        if not isinstance(raw, dict):
            continue
        try:
            number = int(raw_number)
        except (ValueError, TypeError):
            continue
        clusters = raw.get("input_clusters", [])
        cluster_ids = [_cluster_id(c) for c in clusters] if isinstance(clusters, list) else []
        result.append({
            "id": number,
            "input_clusters": sorted({x for x in cluster_ids if x is not None}),
        })
    return sorted(result, key=lambda e: e["id"])


def summarize(identity: dict, devices: list[dict]) -> dict:
    """Sanitized capability report for the intended Fanda; NO credentials."""
    if str(identity.get("device_uuid", "")) != DEVICE_UUID:
        raise InvalidZhaDevice("not_model_00013")
    if not isinstance(devices, list):
        raise InvalidZhaDevice("invalid_device_list")
    matches = [
        d for d in devices
        if isinstance(d, dict) and str(d.get("ieee", "")).lower() == IEEE
    ]
    if len(matches) != 1:
        raise InvalidZhaDevice("expected_exactly_one_sonoff")
    d = matches[0]
    manufacturer = str(d.get("manufacturer") or "").upper()
    model = str(d.get("model") or "")
    signature = d.get("signature") or {}
    if isinstance(signature, dict):
        manufacturer = str(signature.get("manufacturer") or manufacturer).upper()
        model = str(signature.get("model") or model)
    if manufacturer != MANUFACTURER or model != MODEL:
        raise InvalidZhaDevice("unexpected_zha_model")
    endpoints = _endpoints(d)
    configured_channels = [ep["id"] for ep in endpoints if 0x0006 in ep["input_clusters"]]
    custom_cluster_endpoints = [ep["id"] for ep in endpoints if CUSTOM_CLUSTER in ep["input_clusters"]]
    return {
        "serial": SERIAL,
        "device_uuid": DEVICE_UUID,
        "ieee": IEEE,
        "manufacturer": MANUFACTURER,
        "model": MODEL,
        "endpoints": endpoints,
        "onoff_channels": configured_channels,
        "manufacturer_cluster_id": "0xFC11",
        "manufacturer_cluster_endpoints": custom_cluster_endpoints,
        "device_native_auto_off_configured": False,
        "device_native_auto_off_verified": False,
        "electrical_direction_interlock_verified": False,
        "actuation_allowed": False,
        "reason": "read_only_cluster_inventory_is_not_device_auto_off_proof",
    }


def probe(identity: dict, get_devices: Any) -> dict:
    """Runs in local Agent only; calls existing read-only zha/devices inventory."""
    return summarize(identity, get_devices())
