"""Vendor-neutral main breaker protection capability contract.

IMPORTANT:
- This module MUST NOT contain vendor Modbus register addresses.
- Raw register mapping belongs only to each inverter profile/reader.
- The cloud/UI consumes semantic entity keys and per-installation breaker config.
"""

from __future__ import annotations

from typing import Any


SCHEMA_VERSION = 1

DIRECT_CURRENT_KEYS = (
    "smartmeter.proud_l1",
    "smartmeter.proud_l2",
    "smartmeter.proud_l3",
)

APPARENT_POWER_KEYS = (
    "smartmeter.zdanlivy_vykon_l1",
    "smartmeter.zdanlivy_vykon_l2",
    "smartmeter.zdanlivy_vykon_l3",
)

ACTIVE_POWER_KEYS = (
    "smartmeter.vykon_l1",
    "smartmeter.vykon_l2",
    "smartmeter.vykon_l3",
)

PHASE_VOLTAGE_KEYS = (
    "sit.napeti_l1",
    "sit.napeti_l2",
    "sit.napeti_l3",
)

SAFE_CONTROL_SOURCES = {
    "direct_phase_current",
    "apparent_power_div_voltage",
}

DISPLAY_ONLY_SOURCES = {
    "active_power_div_voltage_estimate",
}


def _entity_keys(
    profile: dict[str, Any],
) -> set[str]:
    entities = profile.get("entities")

    if not isinstance(entities, list):
        return set()

    return {
        str(item.get("entity_key") or "").strip()
        for item in entities
        if isinstance(item, dict)
        and str(item.get("entity_key") or "").strip()
    }


def resolve_profile_main_breaker_capability(
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Resolve vendor-neutral capability without raw register knowledge."""

    keys = _entity_keys(profile)
    explicit = profile.get("main_breaker_protection")

    if isinstance(explicit, dict):
        source = str(explicit.get("source") or "unsupported").strip()
        verification_status = str(
            explicit.get("verification_status") or ""
        ).strip()
        declared_control_ready = explicit.get("control_ready") is True
        declared_display_ready = explicit.get("display_ready") is True

        if source == "direct_phase_current":
            required = list(
                explicit.get("phase_current_entity_keys")
                or DIRECT_CURRENT_KEYS
            )
        elif source == "apparent_power_div_voltage":
            required = (
                list(
                    explicit.get("phase_apparent_power_entity_keys")
                    or APPARENT_POWER_KEYS
                )
                + list(
                    explicit.get("phase_voltage_entity_keys")
                    or PHASE_VOLTAGE_KEYS
                )
            )
        elif source == "active_power_div_voltage_estimate":
            required = (
                list(
                    explicit.get("phase_active_power_entity_keys")
                    or ACTIVE_POWER_KEYS
                )
                + list(
                    explicit.get("phase_voltage_entity_keys")
                    or PHASE_VOLTAGE_KEYS
                )
            )
        else:
            required = []

        semantic_ready = bool(required) and all(
            key in keys for key in required
        )
        safe_source = source in SAFE_CONTROL_SOURCES
        control_ready = (
            declared_control_ready
            and safe_source
            and semantic_ready
        )
        display_ready = (
            declared_display_ready
            and semantic_ready
        )

        return {
            "schema_version": SCHEMA_VERSION,
            "profile_id": str(profile.get("profile_id") or ""),
            "manufacturer": str(profile.get("manufacturer") or ""),
            "models": list(profile.get("models") or []),
            "source": source,
            "control_ready": control_ready,
            "display_ready": display_ready,
            "verification_status": verification_status,
            "profile_contract_present": True,
            "semantic_ready": semantic_ready,
            "phase_current_entity_keys": list(
                explicit.get("phase_current_entity_keys") or []
            ),
            "supporting_entity_keys": required,
            "safe_control_sources": sorted(SAFE_CONTROL_SOURCES),
            "display_only_sources": sorted(DISPLAY_ONLY_SOURCES),
            "raw_registers_allowed_in_core": False,
            "stale_telemetry_fail_safe": "deny_new_battery_action",
        }

    has_direct = all(key in keys for key in DIRECT_CURRENT_KEYS)
    has_apparent = all(key in keys for key in APPARENT_POWER_KEYS)
    has_active = all(key in keys for key in ACTIVE_POWER_KEYS)
    has_voltage = all(key in keys for key in PHASE_VOLTAGE_KEYS)

    if has_direct:
        source = "direct_phase_current"
        supporting = list(DIRECT_CURRENT_KEYS)
        display_ready = True
    elif has_apparent and has_voltage:
        source = "apparent_power_div_voltage"
        supporting = list(APPARENT_POWER_KEYS) + list(PHASE_VOLTAGE_KEYS)
        display_ready = True
    elif has_active and has_voltage:
        source = "active_power_div_voltage_estimate"
        supporting = list(ACTIVE_POWER_KEYS) + list(PHASE_VOLTAGE_KEYS)
        display_ready = True
    else:
        source = "unsupported"
        supporting = []
        display_ready = False

    return {
        "schema_version": SCHEMA_VERSION,
        "profile_id": str(profile.get("profile_id") or ""),
        "manufacturer": str(profile.get("manufacturer") or ""),
        "models": list(profile.get("models") or []),
        "source": source,
        "control_ready": False,
        "display_ready": display_ready,
        "verification_status": "profile_contract_missing",
        "profile_contract_present": False,
        "semantic_ready": bool(supporting),
        "phase_current_entity_keys": [],
        "supporting_entity_keys": supporting,
        "safe_control_sources": sorted(SAFE_CONTROL_SOURCES),
        "display_only_sources": sorted(DISPLAY_ONLY_SOURCES),
        "raw_registers_allowed_in_core": False,
        "stale_telemetry_fail_safe": "deny_new_battery_action",
    }

def normalize_phase_currents(
    *,
    source: str,
    values: dict[str, float | int | None],
) -> dict[str, float | None]:
    """Normalize L1/L2/L3 to ampere using semantic values only."""

    if source == "direct_phase_current":
        return {
            "l1_a": (
                abs(float(values["smartmeter.proud_l1"]))
                if values.get("smartmeter.proud_l1") is not None
                else None
            ),
            "l2_a": (
                abs(float(values["smartmeter.proud_l2"]))
                if values.get("smartmeter.proud_l2") is not None
                else None
            ),
            "l3_a": (
                abs(float(values["smartmeter.proud_l3"]))
                if values.get("smartmeter.proud_l3") is not None
                else None
            ),
        }

    if source in {
        "apparent_power_div_voltage",
        "active_power_div_voltage_estimate",
    }:
        power_prefix = (
            "smartmeter.zdanlivy_vykon_l"
            if source == "apparent_power_div_voltage"
            else "smartmeter.vykon_l"
        )

        result: dict[str, float | None] = {}

        for phase in (1, 2, 3):
            p = values.get(
                f"{power_prefix}{phase}"
            )

            u = values.get(
                f"sit.napeti_l{phase}"
            )

            key = f"l{phase}_a"

            if p is None or u is None:
                result[key] = None
                continue

            voltage = float(u)

            if voltage <= 1.0:
                result[key] = None
                continue

            result[key] = (
                abs(float(p))
                / voltage
            )

        return result

    return {
        "l1_a": None,
        "l2_a": None,
        "l3_a": None,
    }
