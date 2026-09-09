"""Wi-Fi infrastructure discovery for TNG IQ FANDA Agent.

This module lists Wi-Fi/LAN class devices that already exist in Home Assistant
and exposes them to the cloud as infrastructure devices on the same level as
Zigbee. It intentionally does not try to vendor-pair every possible Wi-Fi
product.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zigbee_manager import (
    HomeAssistantApiError,
    get_home_assistant_states,
    home_assistant_websocket_request,
)


WIFI_INTEGRATION_DOMAINS = {
    "shelly",
    "esphome",
    "tasmota",
    "sonoff",
    "ewelink",
    "tuya",
    "localtuya",
    "tplink",
    "kasa",
    "meross",
    "blebox",
    "wled",
    "wiz",
    "yeelight",
    "xiaomi_miio",
    "mqtt",
}

EXCLUDED_ZIGBEE_DOMAINS = {
    "zha",
    "deconz",
    "zigbee",
    "zigbee2mqtt",
}


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_lower(value: Any) -> str:
    return _as_text(value).lower()


def _safe_ws_list(command_type: str) -> list[dict[str, Any]]:
    try:
        result = home_assistant_websocket_request(
            command_type=command_type,
        )
    except Exception as exc:
        raise HomeAssistantApiError(
            f"Home Assistant registry request failed: {command_type}: {exc}"
        ) from exc

    if not isinstance(result, list):
        return []

    return [
        item
        for item in result
        if isinstance(item, dict)
    ]


def _entity_domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0].lower() if "." in entity_id else ""


def _state_by_entity_id(states: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        _as_text(item.get("entity_id")): item
        for item in states
        if _as_text(item.get("entity_id"))
    }


def _device_by_id(devices: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        _as_text(item.get("id")): item
        for item in devices
        if _as_text(item.get("id"))
    }


def _integration_domain(entity_registry_item: dict[str, Any]) -> str:
    platform = _as_lower(entity_registry_item.get("platform"))
    if platform:
        return platform

    config_entry_id = _as_lower(entity_registry_item.get("config_entry_id"))
    return config_entry_id


def _contains_wifi_hint(value: Any) -> bool:
    text = _as_lower(value)
    return any(
        hint in text
        for hint in (
            "wifi",
            "wi-fi",
            "shelly",
            "esphome",
            "tasmota",
            "sonoff",
            "ewelink",
            "tuya",
            "localtuya",
            "tplink",
            "kasa",
            "meross",
            "blebox",
            "wled",
            "wiz",
        )
    )


def _contains_zigbee_hint(value: Any) -> bool:
    text = _as_lower(value)
    return any(
        hint in text
        for hint in (
            "zigbee",
            "zha",
            "deconz",
            "zigbee2mqtt",
            "lqi",
            "ieee",
            "nwk",
            "endpoint",
        )
    )


def _device_is_probable_wifi(
    *,
    device: dict[str, Any],
    entity_items: list[dict[str, Any]],
) -> bool:
    domains = {
        _integration_domain(item)
        for item in entity_items
        if _integration_domain(item)
    }

    if domains & EXCLUDED_ZIGBEE_DOMAINS:
        return False

    if (domains & WIFI_INTEGRATION_DOMAINS) - {"mqtt"}:
        return True

    if "mqtt" in domains:
        probe = " ".join(
            _as_text(device.get(key))
            for key in (
                "name_by_user",
                "name",
                "manufacturer",
                "model",
                "sw_version",
                "hw_version",
            )
        )

        if _contains_zigbee_hint(probe):
            return False

        return _contains_wifi_hint(probe)

    connections = device.get("connections")
    if isinstance(connections, list):
        for connection in connections:
            if (
                isinstance(connection, (list, tuple))
                and len(connection) >= 2
                and _as_lower(connection[0]) in {"mac", "ip", "hostname"}
            ):
                probe = " ".join(
                    _as_text(device.get(key))
                    for key in (
                        "name_by_user",
                        "name",
                        "manufacturer",
                        "model",
                    )
                )

                if _contains_zigbee_hint(probe):
                    return False

                return True

    return False


def _normalize_entity(
    *,
    entity_registry_item: dict[str, Any],
    state_item: dict[str, Any] | None,
) -> dict[str, Any]:
    entity_id = _as_text(entity_registry_item.get("entity_id"))
    attributes = (
        state_item.get("attributes")
        if isinstance(state_item, dict)
        and isinstance(state_item.get("attributes"), dict)
        else {}
    )

    domain = _entity_domain(entity_id)
    device_class = _as_text(
        entity_registry_item.get("device_class")
        or attributes.get("device_class")
    )

    capabilities: list[str] = []

    if domain in {"switch", "light"}:
        capabilities.append("binary_power_output")

    return {
        "entity_id": entity_id,
        "domain": domain,
        "platform": _integration_domain(entity_registry_item),
        "name": _as_text(
            entity_registry_item.get("name")
            or entity_registry_item.get("original_name")
            or attributes.get("friendly_name")
            or entity_id
        ),
        "state": (
            state_item.get("state")
            if isinstance(state_item, dict)
            else None
        ),
        "device_class": device_class,
        "unit_of_measurement": attributes.get("unit_of_measurement"),
        "capabilities": capabilities,
        "disabled_by": entity_registry_item.get("disabled_by"),
    }


def _connection_value(device: dict[str, Any], kind: str) -> str | None:
    connections = device.get("connections")
    if not isinstance(connections, list):
        return None

    for connection in connections:
        if not isinstance(connection, (list, tuple)) or len(connection) < 2:
            continue

        if _as_lower(connection[0]) == kind:
            value = _as_text(connection[1])
            if value:
                return value

    return None


def discover_home_assistant_wifi_devices() -> dict[str, Any]:
    states = get_home_assistant_states()
    entity_registry = _safe_ws_list("config/entity_registry/list")
    device_registry = _safe_ws_list("config/device_registry/list")

    states_by_id = _state_by_entity_id(states)
    devices_by_id = _device_by_id(device_registry)

    entities_by_device_id: dict[str, list[dict[str, Any]]] = {}

    for entity_item in entity_registry:
        entity_id = _as_text(entity_item.get("entity_id"))
        device_id = _as_text(entity_item.get("device_id"))

        if not entity_id or not device_id:
            continue

        entities_by_device_id.setdefault(device_id, []).append(entity_item)

    discovered_devices: list[dict[str, Any]] = []

    for device_id, entity_items in sorted(entities_by_device_id.items()):
        device = devices_by_id.get(device_id, {})

        if not _device_is_probable_wifi(
            device=device,
            entity_items=entity_items,
        ):
            continue

        normalized_entities = [
            _normalize_entity(
                entity_registry_item=entity_item,
                state_item=states_by_id.get(
                    _as_text(entity_item.get("entity_id"))
                ),
            )
            for entity_item in sorted(
                entity_items,
                key=lambda item: _as_text(item.get("entity_id")),
            )
        ]

        enabled_entities = [
            entity
            for entity in normalized_entities
            if not entity.get("disabled_by")
        ]

        control_entities = [
            entity
            for entity in enabled_entities
            if entity.get("domain") in {"switch", "light"}
        ]

        discovered_devices.append(
            {
                "device_id": device_id,
                "transport": "wifi",
                "source": "home_assistant_registry",
                "custom_name": _as_text(device.get("name_by_user")),
                "name": _as_text(
                    device.get("name_by_user")
                    or device.get("name")
                    or device.get("model")
                    or device_id
                ),
                "manufacturer": _as_text(device.get("manufacturer")),
                "model": _as_text(device.get("model")),
                "sw_version": _as_text(device.get("sw_version")),
                "hw_version": _as_text(device.get("hw_version")),
                "mac_address": _connection_value(device, "mac"),
                "ip_address": _connection_value(device, "ip"),
                "entity_count": len(enabled_entities),
                "control_entity_count": len(control_entities),
                "entities": enabled_entities,
            }
        )

    return {
        "worker": "command_worker",
        "executor": "wifi_manager",
        "phase": "wifi_devices_discovered",
        "infrastructure_mode": True,
        "transport": "wifi",
        "device_count": len(discovered_devices),
        "devices": discovered_devices,
        "home_assistant_registry": {
            "state_count": len(states),
            "entity_registry_count": len(entity_registry),
            "device_registry_count": len(device_registry),
        },
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
