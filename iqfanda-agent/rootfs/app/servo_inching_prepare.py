"""F47 R117: model 00013 SONOFF MINI-ZB2GS native inching configuration.

IMPORTANT: This stage configures a device-side 1-second OFF timer through the
ZHA cluster service. It NEVER turns a relay ON and NEVER attests that the
firmware accepted or executed the timer. Physical actuator control stays OFF.
The payload is based on the vendor protocol in zigbee-herdsman-converters.
"""
from __future__ import annotations

from typing import Callable, Any

SERIAL = "F800711-TNG-00013"
UUID = "014a0d23-d440-4131-abb4-28a860595043"
IEEE = "08:fd:52:ff:fe:07:87:d9"
OPEN = "switch.sonoff_mini_zb2gs_spinac_3"
CLOSE = "switch.sonoff_mini_zb2gs_spinac_4"
CLUSTER = 0xFC11
MANUFACTURER = 0x1286
COMMAND_ID = 0


class UnsafeConfiguration(RuntimeError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise UnsafeConfiguration(message)


def frame(endpoint: int) -> list[int]:
    """Vendor protocolData: ENABLE inching, OFF after 1.0s, channel 1 or 2."""
    require(type(endpoint) is int and endpoint in (1, 2), "invalid_endpoint")
    payload = [0x01, 0x17, 0x07, 0x80, 0x80, endpoint - 1,
               0x02, 0x00, 0x00, 0x00]
    checksum = 0
    for item in payload:
        checksum ^= item
    return payload + [checksum]


def _has_cluster(entry: dict, cluster: int) -> bool:
    for c in entry.get("input_clusters") or []:
        try:
            value = int(c, 0) if isinstance(c, str) and c.startswith("0x") else int(c)
        except (TypeError, ValueError):
            continue
        if value == cluster:
            return True
    return False


def prepare(
    *,
    identity: dict,
    payload: dict,
    get_zha_devices: Callable[[], list[dict]],
    get_entity_state: Callable[[str], dict],
    get_entity_device_id: Callable[[str], str | None],
    get_ha_services: Callable[[], list[dict]],
    call_ha_service: Callable[..., Any],
) -> dict:
    """Send configuration frames only; NEVER assert device-native auto OFF."""
    require(identity.get("device_uuid") == UUID, "wrong_device_uuid")
    require(payload == {"serial": SERIAL, "mode": "prepare_native_off_1s"},
            "unexpected_command_payload")
    all_devices = get_zha_devices()
    require(isinstance(all_devices, list), "bad_zha_inventory")
    matches = [d for d in all_devices if isinstance(d, dict)
               and str(d.get("ieee") or "").lower() == IEEE]
    require(len(matches) == 1, "servo_device_not_unique")
    device = matches[0]
    signature = device.get("signature") or {}
    require(isinstance(signature, dict), "bad_zha_signature")
    manufacturer = str(signature.get("manufacturer") or device.get("manufacturer") or "").upper()
    model = str(signature.get("model") or device.get("model") or "")
    require(manufacturer == "SONOFF" and model == "MINI-ZB2GS", "wrong_hardware")
    require(device.get("available") is not False, "device_unavailable")
    eps = signature.get("endpoints")
    require(isinstance(eps, dict), "zha_endpoints_missing")
    for ep in (1, 2):
        entry = eps.get(ep, eps.get(str(ep)))
        require(isinstance(entry, dict) and _has_cluster(entry, CLUSTER)
                and _has_cluster(entry, 6), "required_endpoint_missing")
    ids = [get_entity_device_id(eid) for eid in (OPEN, CLOSE)]
    require(bool(ids[0]) and ids[0] == ids[1], "relay_registry_mismatch")
    require(str(device.get("device_reg_id") or ids[0]) == ids[0],
            "zha_device_registry_mismatch")
    for eid in (OPEN, CLOSE):
        state = get_entity_state(eid)
        require(isinstance(state, dict)
                and str(state.get("state") or "").lower() == "off",
                "servo_outputs_not_both_off")
    services = get_ha_services()
    require(isinstance(services, list), "bad_services")
    zha = next((d for d in services if isinstance(d, dict)
                and d.get("domain") == "zha"), None)
    require(isinstance(zha, dict) and
            "issue_zigbee_cluster_command" in (zha.get("services") or {}),
            "zha_cluster_service_unavailable")

    completed = []
    for endpoint in (1, 2):
        # Configures SONOFF device-local timing, but DOES NOT turn relay ON.
        # ZHA may reject unknown manufacturer command; errors are surfaced.
        result = call_ha_service(
            domain="zha",
            service="issue_zigbee_cluster_command",
            payload={
                "ieee": IEEE, "endpoint_id": endpoint,
                "cluster_id": CLUSTER, "cluster_type": "in",
                "command": COMMAND_ID, "command_type": "server",
                "manufacturer": MANUFACTURER,
                "params": {"data": frame(endpoint)},
            },
        )
        completed.append({"endpoint": endpoint, "request_returned": True,
                          "response_type": type(result).__name__})
    return {
        "serial": SERIAL, "ieee": IEEE, "commands": completed,
        "relay_on_commands": 0,
        "device_native_auto_off_verified": False,
        "directional_interlock_verified": False,
        "actuation_enabled": False,
        "result": "NATIVE_INCHING_CONFIG_REQUESTED_UNVERIFIED",
    }
