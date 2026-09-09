"""Wi-Fi infrastructure discovery for TNG IQ FANDA Agent.

R131 extends the R120 Wi-Fi discovery with a safer broad detector and with a
diagnostic audit. It still does not pair Wi-Fi devices and does not execute any
Home Assistant service. It only reads Home Assistant states and registries.
"""

from __future__ import annotations

from collections import Counter
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
    "tplink_router",
    "meross",
    "blebox",
    "wled",
    "wiz",
    "yeelight",
    "xiaomi_miio",
    "mqtt",
    "rest",
    "command_line",
}

EXCLUDED_ZIGBEE_DOMAINS = {
    "zha",
    "deconz",
    "zigbee",
    "zigbee2mqtt",
}

CORE_OR_SYSTEM_DOMAINS = {
    "homeassistant",
    "hassio",
    "supervisor",
    "mobile_app",
    "persistent_notification",
    "sun",
    "zone",
    "person",
    "device_tracker",
    "scene",
    "script",
    "automation",
}

INFRASTRUCTURE_ENTITY_DOMAINS = {
    "switch",
    "light",
    "sensor",
    "binary_sensor",
    "number",
    "button",
    "select",
    "climate",
    "cover",
    "fan",
    "lock",
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


def _try_ws_list(
    command_type: str,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        return _safe_ws_list(command_type)
    except Exception as exc:
        warnings.append(
            {
                "command_type": command_type,
                "error": str(exc),
            }
        )
        return []


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


def _device_identifier_domains(device: dict[str, Any]) -> set[str]:
    domains: set[str] = set()

    identifiers = device.get("identifiers")
    if not isinstance(identifiers, list):
        return domains

    for identifier in identifiers:
        if not isinstance(identifier, (list, tuple)) or len(identifier) < 1:
            continue

        domain = _as_lower(identifier[0])
        if domain:
            domains.add(domain)

    return domains


def _connection_types(device: dict[str, Any]) -> set[str]:
    types: set[str] = set()

    connections = device.get("connections")
    if not isinstance(connections, list):
        return types

    for connection in connections:
        if not isinstance(connection, (list, tuple)) or len(connection) < 1:
            continue

        kind = _as_lower(connection[0])
        if kind:
            types.add(kind)

    return types


def _contains_wifi_hint(value: Any) -> bool:
    text = _as_lower(value)
    return any(
        hint in text
        for hint in (
            "wifi",
            "wi-fi",
            "wlan",
            "lan",
            "ethernet",
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


def _entity_domains_for_device(entity_items: list[dict[str, Any]]) -> set[str]:
    domains: set[str] = set()

    for item in entity_items:
        entity_id = _as_text(item.get("entity_id"))
        domain = _entity_domain(entity_id)
        if domain:
            domains.add(domain)

    return domains


def _platforms_for_device(entity_items: list[dict[str, Any]]) -> set[str]:
    return {
        _integration_domain(item)
        for item in entity_items
        if _integration_domain(item)
    }


def _device_probe_text(device: dict[str, Any]) -> str:
    pieces = [
        _as_text(device.get(key))
        for key in (
            "name_by_user",
            "name",
            "manufacturer",
            "model",
            "sw_version",
            "hw_version",
            "configuration_url",
        )
    ]
    return " ".join(piece for piece in pieces if piece)


def _classification(
    *,
    device: dict[str, Any],
    entity_items: list[dict[str, Any]],
) -> dict[str, Any]:
    platforms = _platforms_for_device(entity_items)
    identifier_domains = _device_identifier_domains(device)
    connection_types = _connection_types(device)
    entity_domains = _entity_domains_for_device(entity_items)
    probe = _device_probe_text(device)

    reasons: list[str] = []
    excluded_reasons: list[str] = []

    if platforms & EXCLUDED_ZIGBEE_DOMAINS:
        excluded_reasons.append(
            "excluded_zigbee_entity_platform"
        )

    if identifier_domains & EXCLUDED_ZIGBEE_DOMAINS:
        excluded_reasons.append(
            "excluded_zigbee_identifier"
        )

    if _contains_zigbee_hint(probe):
        excluded_reasons.append(
            "excluded_zigbee_text_hint"
        )

    if platforms <= CORE_OR_SYSTEM_DOMAINS and not _contains_wifi_hint(probe):
        excluded_reasons.append(
            "excluded_core_or_system_only"
        )

    if excluded_reasons:
        return {
            "include": False,
            "reasons": [],
            "excluded_reasons": excluded_reasons,
            "platforms": sorted(platforms),
            "identifier_domains": sorted(identifier_domains),
            "connection_types": sorted(connection_types),
            "entity_domains": sorted(entity_domains),
        }

    known_wifi_platforms = (platforms & WIFI_INTEGRATION_DOMAINS) - {"mqtt"}
    if known_wifi_platforms:
        reasons.append(
            "known_wifi_platform:" + ",".join(sorted(known_wifi_platforms))
        )

    known_wifi_identifiers = (
        identifier_domains & WIFI_INTEGRATION_DOMAINS
    ) - {"mqtt"}
    if known_wifi_identifiers:
        reasons.append(
            "known_wifi_identifier:" + ",".join(sorted(known_wifi_identifiers))
        )

    if "mqtt" in platforms or "mqtt" in identifier_domains:
        if _contains_wifi_hint(probe):
            reasons.append("mqtt_with_wifi_hint")

    if connection_types & {"mac", "ip", "hostname"}:
        if entity_domains & INFRASTRUCTURE_ENTITY_DOMAINS:
            reasons.append(
                "network_connection_with_infrastructure_entities"
            )

    configuration_url = _as_lower(device.get("configuration_url"))
    if configuration_url.startswith("http"):
        reasons.append("configuration_url_http")

    if _contains_wifi_hint(probe):
        reasons.append("wifi_text_hint")

    if (
        not reasons
        and entity_domains & {"switch", "light"}
        and platforms
        and not platforms <= CORE_OR_SYSTEM_DOMAINS
    ):
        reasons.append("non_core_controllable_non_zigbee_device")

    return {
        "include": bool(reasons),
        "reasons": reasons,
        "excluded_reasons": excluded_reasons,
        "platforms": sorted(platforms),
        "identifier_domains": sorted(identifier_domains),
        "connection_types": sorted(connection_types),
        "entity_domains": sorted(entity_domains),
    }


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


def _diagnostic_device_summary(
    *,
    device: dict[str, Any],
    entity_items: list[dict[str, Any]],
    classification: dict[str, Any],
    max_entities: int = 10,
) -> dict[str, Any]:
    entities = [
        _as_text(item.get("entity_id"))
        for item in sorted(
            entity_items,
            key=lambda item: _as_text(item.get("entity_id")),
        )
        if _as_text(item.get("entity_id"))
    ]

    return {
        "device_id": _as_text(device.get("id")),
        "name": _as_text(
            device.get("name_by_user")
            or device.get("name")
            or device.get("model")
            or device.get("id")
        ),
        "manufacturer": _as_text(device.get("manufacturer")),
        "model": _as_text(device.get("model")),
        "configuration_url": _as_text(device.get("configuration_url")),
        "platforms": classification.get("platforms", []),
        "identifier_domains": classification.get(
            "identifier_domains",
            [],
        ),
        "connection_types": classification.get("connection_types", []),
        "entity_domains": classification.get("entity_domains", []),
        "reasons": classification.get("reasons", []),
        "excluded_reasons": classification.get("excluded_reasons", []),
        "entity_count": len(entities),
        "entity_samples": entities[:max_entities],
    }


def discover_home_assistant_wifi_devices() -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []

    states = get_home_assistant_states()
    entity_registry = _safe_ws_list("config/entity_registry/list")
    device_registry = _safe_ws_list("config/device_registry/list")

    config_entries = _try_ws_list(
        "config/config_entries/list",
        warnings,
    )

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
    included_summaries: list[dict[str, Any]] = []
    excluded_summaries: list[dict[str, Any]] = []

    platform_counter: Counter[str] = Counter()
    identifier_counter: Counter[str] = Counter()
    connection_counter: Counter[str] = Counter()
    entity_domain_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    excluded_reason_counter: Counter[str] = Counter()

    for device_id, entity_items in sorted(entities_by_device_id.items()):
        device = devices_by_id.get(device_id, {})
        classification = _classification(
            device=device,
            entity_items=entity_items,
        )

        platform_counter.update(classification.get("platforms", []))
        identifier_counter.update(
            classification.get("identifier_domains", [])
        )
        connection_counter.update(classification.get("connection_types", []))
        entity_domain_counter.update(
            classification.get("entity_domains", [])
        )
        reason_counter.update(classification.get("reasons", []))
        excluded_reason_counter.update(
            classification.get("excluded_reasons", [])
        )

        summary = _diagnostic_device_summary(
            device=device,
            entity_items=entity_items,
            classification=classification,
        )

        if not classification.get("include"):
            if len(excluded_summaries) < 20:
                excluded_summaries.append(summary)
            continue

        included_summaries.append(summary)

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
                "classification_reasons": classification.get("reasons", []),
                "platforms": classification.get("platforms", []),
                "identifier_domains": classification.get(
                    "identifier_domains",
                    [],
                ),
                "connection_types": classification.get(
                    "connection_types",
                    [],
                ),
                "entity_domains": classification.get("entity_domains", []),
                "entity_count": len(enabled_entities),
                "control_entity_count": len(control_entities),
                "entities": enabled_entities,
            }
        )

    return {
        "worker": "command_worker",
        "executor": "wifi_manager",
        "phase": "wifi_devices_discovered",
        "phase28_r131_diagnostic_discovery": True,
        "infrastructure_mode": True,
        "transport": "wifi",
        "device_count": len(discovered_devices),
        "devices": discovered_devices,
        "home_assistant_registry": {
            "state_count": len(states),
            "entity_registry_count": len(entity_registry),
            "device_registry_count": len(device_registry),
            "config_entry_count": len(config_entries),
        },
        "diagnostics": {
            "warnings": warnings,
            "device_with_entities_count": len(entities_by_device_id),
            "included_candidate_count": len(included_summaries),
            "excluded_sample_count": len(excluded_summaries),
            "platform_counts": dict(platform_counter.most_common(40)),
            "identifier_domain_counts": dict(
                identifier_counter.most_common(40)
            ),
            "connection_type_counts": dict(
                connection_counter.most_common(40)
            ),
            "entity_domain_counts": dict(
                entity_domain_counter.most_common(40)
            ),
            "include_reason_counts": dict(reason_counter.most_common(40)),
            "excluded_reason_counts": dict(
                excluded_reason_counter.most_common(40)
            ),
            "included_candidate_samples": included_summaries[:20],
            "excluded_device_samples": excluded_summaries,
        },
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
