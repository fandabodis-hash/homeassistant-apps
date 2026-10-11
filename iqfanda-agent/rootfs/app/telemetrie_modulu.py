"""Periodicky sber a odesilani telemetrie modulu."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from communication.inverter_adapter import (
    read_inverter_snapshot,
)
from communication.pylontech_us5000 import (
    read_pylontech_snapshot,
)
from device_config import (
    load_cached_cloud_config,
    load_device_identity,
)
from host.cloud_client import cloud_client
from zigbee_manager import (
    call_binary_power_output_service,
    get_binary_power_output_service_domain,
    get_entity_state,
)

from spot_boiler_intent import (
    combine_boiler_requests,
    load_spot_boiler_intent,
)

from pv_surplus_target_intent import (
    load_pv_surplus_target_intent,
)
from weekly_relay_owner import is_verified_weekly_owner

from pv_surplus_decision import (
    STAV_ACTIVE,
    STAV_FAULT,
    STAV_OFF,
    vyhodnotit_cil_prebytku,
    vyhodnotit_stav_prebytku,
    vyhodnotit_teplotu_cile,
)


DEFAULT_TELEMETRY_INTERVAL_SECONDS = 60
MIN_TELEMETRY_INTERVAL_SECONDS = 5
MAX_TELEMETRY_INTERVAL_SECONDS = 3600
ERROR_RETRY_INTERVAL_SECONDS = 15

PV_SURPLUS_CONTROL_INTERVAL_SECONDS = 5
PV_SURPLUS_CONTROL_ERROR_RETRY_SECONDS = 5

# Kratkodoby vypadek FVE dat nesmi okamzite
# vypnout jiz bezici spotrebic.
# Po 180 sekundach souvisleho FAULTu plati FAIL-SAFE.
PV_SURPLUS_TELEMETRY_GRACE_SECONDS = 180.0


def ziskej_interval_telemetrie(
    konfigurace: dict[str, Any] | None,
) -> int:
    """Vrati bezpecne omezeny interval telemetrie."""
    if not isinstance(konfigurace, dict):
        return DEFAULT_TELEMETRY_INTERVAL_SECONDS

    raw_interval = konfigurace.get(
        "telemetry_interval_seconds",
        DEFAULT_TELEMETRY_INTERVAL_SECONDS,
    )

    try:
        interval = int(raw_interval)
    except (TypeError, ValueError):
        logging.warning(
            "Neplatny telemetry_interval_seconds: %r. "
            "Pouzivam %s sekund.",
            raw_interval,
            DEFAULT_TELEMETRY_INTERVAL_SECONDS,
        )
        return DEFAULT_TELEMETRY_INTERVAL_SECONDS

    return max(
        MIN_TELEMETRY_INTERVAL_SECONDS,
        min(interval, MAX_TELEMETRY_INTERVAL_SECONDS),
    )


def najdi_fve_runtime(
    cloud_config: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Najde jedinou aktivni read-only FVE runtime.

    Tato sdilena provozni vrstva nezna
    vyrobce, model ani fyzicky transport.
    """
    runtime_configurations = cloud_config.get(
        "module_runtime_configurations"
    )

    if not isinstance(
        runtime_configurations,
        list,
    ):
        return None

    matches: list[dict[str, Any]] = []

    for runtime_configuration in (
        runtime_configurations
    ):
        if not isinstance(
            runtime_configuration,
            dict,
        ):
            continue

        module_key = str(
            runtime_configuration.get(
                "module_key"
            )
            or ""
        ).strip().lower()

        if module_key != "photovoltaic":
            continue

        if (
            runtime_configuration.get(
                "telemetry_enabled"
            )
            is not True
        ):
            continue

        if (
            runtime_configuration.get(
                "read_only"
            )
            is not True
        ):
            continue

        matches.append(
            runtime_configuration
        )

    if len(matches) != 1:
        return None

    return matches[0]


def najdi_baterie_runtime(
    cloud_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Najde jedinou aktivni read-only runtime baterie."""
    runtime_configurations = cloud_config.get(
        "module_runtime_configurations"
    )

    if not isinstance(
        runtime_configurations,
        list,
    ):
        return None

    matches: list[dict[str, Any]] = []

    for runtime_configuration in (
        runtime_configurations
    ):
        if not isinstance(
            runtime_configuration,
            dict,
        ):
            continue

        if (
            str(
                runtime_configuration.get(
                    "module_key"
                )
                or ""
            ).strip().lower()
            != "battery"
        ):
            continue

        if (
            runtime_configuration.get(
                "telemetry_enabled"
            )
            is not True
            or runtime_configuration.get(
                "read_only"
            )
            is not True
        ):
            continue

        matches.append(
            runtime_configuration
        )

    if len(matches) != 1:
        return None

    return matches[0]


def nacti_fve_telemetrii(
    runtime_configuration: dict[str, Any],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    str,
    str,
]:
    """
    Nacte FVE data vyhradne pres univerzalni
    profilovy adapter.

    Tato runtime vrstva nezna konkretniho vyrobce.
    Vsechny odlisnosti stridace patri pouze do
    communication profilu a adapteru.
    """
    snapshot = read_inverter_snapshot(
        runtime_configuration
    )

    if not isinstance(
        snapshot,
        dict,
    ):
        raise RuntimeError(
            "Univerzalni reader nevratil "
            "platny snapshot."
        )

    entities = snapshot.get(
        "entities"
    )

    if (
        not isinstance(
            entities,
            list,
        )
        or not entities
    ):
        raise RuntimeError(
            "Univerzalni reader nevratil "
            "normalizovane FVE entity."
        )

    telemetry_source = str(
        snapshot.get(
            "telemetry_source"
        )
        or "inverter_profile"
    ).strip()

    if not telemetry_source:
        telemetry_source = (
            "inverter_profile"
        )

    return (
        snapshot,
        entities,
        telemetry_source,
        "universal",
    )


def najdi_pv_surplus_runtime(
    cloud_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Najde povolenou runtime konfiguraci rizeni prebytku."""
    runtime_configurations = cloud_config.get(
        "module_runtime_configurations"
    )

    if not isinstance(runtime_configurations, list):
        return None

    for runtime_configuration in runtime_configurations:
        if not isinstance(runtime_configuration, dict):
            continue

        module_key = str(
            runtime_configuration.get("module_key") or ""
        ).strip().lower()

        if (
            module_key == "pv_surplus_control"
            and runtime_configuration.get(
                "telemetry_enabled"
            )
            is True
            and runtime_configuration.get("read_only") is True
        ):
            return runtime_configuration

    return None


def vytvorit_klic_cile(
    target: dict[str, Any],
    index: int,
) -> str:
    """Vrati stabilni a unikatni prefix entity ciloveho prvku."""
    target_id = str(
        target.get("id") or ""
    ).strip().lower()

    normalized_id = "".join(
        character
        for character in target_id
        if character.isalnum()
    )

    if normalized_id:
        return f"cil.{normalized_id}"

    return f"cil.index{index}"


def ziskej_kvalitu_ha_stavu(
    state: dict[str, Any] | None,
) -> str:
    """Prevede stav HA na kvalitu module telemetry."""
    if not isinstance(state, dict):
        return "error"

    raw_state = str(
        state.get("state") or ""
    ).strip().lower()

    if raw_state == "unavailable":
        return "unavailable"

    if raw_state == "unknown":
        return "unknown"

    return "good"


def normalizovat_ha_hodnotu(
    entity_id: str,
    state: dict[str, Any] | None,
) -> tuple[
    bool | int | float | str | None,
    str,
    str | None,
]:
    """Prevede HA stav na hodnotu podporovanou module telemetry."""
    normalized_entity_id = str(
        entity_id or ""
    ).strip()

    is_switch = normalized_entity_id.startswith(
        "switch."
    )

    if not isinstance(state, dict):
        return (
            None,
            "boolean" if is_switch else "number",
            None,
        )

    raw_state = str(
        state.get("state") or ""
    ).strip()

    attributes = state.get("attributes")

    if not isinstance(attributes, dict):
        attributes = {}

    unit = attributes.get("unit_of_measurement")

    if unit is not None:
        unit = str(unit).strip() or None

    lowered = raw_state.lower()

    if lowered in {"unknown", "unavailable"}:
        return (
            None,
            "boolean" if is_switch else "number",
            unit,
        )

    if is_switch:
        if lowered == "on":
            return True, "boolean", unit

        if lowered == "off":
            return False, "boolean", unit

        return raw_state, "text", unit

    try:
        numeric_value = float(raw_state)
    except (TypeError, ValueError):
        return raw_state, "text", unit

    return numeric_value, "number", unit


def nacist_ha_stav(
    entity_id: str,
) -> dict[str, Any] | None:
    """Bezpecne nacte jednu HA entitu."""
    try:
        state = get_entity_state(entity_id)
    except Exception as exc:
        logging.warning(
            "Nacteni HA entity %s selhalo: %s",
            entity_id,
            exc,
        )
        return None

    if not isinstance(state, dict):
        logging.warning(
            "HA entita %s vratila neplatny stav.",
            entity_id,
        )
        return None

    return state


def vytvorit_pv_surplus_entity(
    runtime_configuration: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Sestavi provozni entity overenych cilu prebytku."""
    configuration = runtime_configuration.get(
        "configuration"
    )

    if not isinstance(configuration, dict):
        return [], False

    targets = configuration.get("targets")

    if not isinstance(targets, list):
        return [], False

    entities: list[dict[str, Any]] = []
    snapshot_complete = True

    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            continue

        if target.get("enabled") is not True:
            continue

        if (
            target.get("configuration_status")
            != "verified"
        ):
            continue

        target_name = str(
            target.get("name") or "Cil"
        ).strip()

        target_id = str(
            target.get("id") or ""
        ).strip()

        prefix = vytvorit_klic_cile(
            target,
            index,
        )

        sensors = target.get("sensors")

        if isinstance(sensors, list):
            for sensor in sensors:
                if not isinstance(sensor, dict):
                    continue

                if sensor.get("status") != "verified":
                    continue

                role = str(
                    sensor.get("role") or ""
                ).strip().lower()

                reference = str(
                    sensor.get("reference") or ""
                ).strip()

                # R3R11_ISOLATED_MULTI_TARGET_PV_SURPLUS
                if (
                    role not in {
                        "water_temperature",
                        "temperature",
                    }
                    or not reference
                ):
                    continue

                state = nacist_ha_stav(reference)

                if state is None:
                    snapshot_complete = False

                value, value_type, unit = (
                    normalizovat_ha_hodnotu(
                        reference,
                        state,
                    )
                )

                entities.append(
                    {
                        "entity_key":
                            f"{prefix}.teplota",
                        "category":
                            "pv_surplus_target",
                        "name":
                            f"{target_name} teplota",
                        "value":
                            value,
                        "unit":
                            unit,
                        "value_type":
                            value_type,
                        "quality":
                            ziskej_kvalitu_ha_stavu(
                                state
                            ),
                        "source_address":
                            None,
                        "attributes": {
                            "target_id":
                                target_id,
                            "target_name":
                                target_name,
                            "ha_entity_id":
                                reference,
                            "role":
                                role,
                        },
                    }
                )

        output = target.get("output")

        if not isinstance(output, dict):
            continue

        if output.get("status") != "verified":
            continue

        output_reference = str(
            output.get("reference") or ""
        ).strip()

        if output_reference:
            state = nacist_ha_stav(
                output_reference
            )

            if state is None:
                snapshot_complete = False

            value, value_type, unit = (
                normalizovat_ha_hodnotu(
                    output_reference,
                    state,
                )
            )

            entities.append(
                {
                    "entity_key":
                        f"{prefix}.stav",
                    "category":
                        "pv_surplus_target",
                    "name":
                        f"{target_name} stav",
                    "value":
                        value,
                    "unit":
                        unit,
                    "value_type":
                        value_type,
                    "quality":
                        ziskej_kvalitu_ha_stavu(
                            state
                        ),
                    "source_address":
                        None,
                    "attributes": {
                        "target_id":
                            target_id,
                        "target_name":
                            target_name,
                        "ha_entity_id":
                            output_reference,
                    },
                }
            )

        measurements = output.get(
            "measurements"
        )

        if not isinstance(measurements, dict):
            continue

        measurement_definitions = (
            (
                "power_entity_id",
                "vykon",
                "Vykon",
            ),
            (
                "energy_entity_id",
                "energie_celkem",
                "Energie celkem",
            ),
            (
                "current_entity_id",
                "proud",
                "Proud",
            ),
            (
                "voltage_entity_id",
                "napeti",
                "Napeti",
            ),
        )

        for (
            configuration_key,
            entity_suffix,
            display_name,
        ) in measurement_definitions:
            reference = str(
                measurements.get(
                    configuration_key
                )
                or ""
            ).strip()

            if not reference:
                continue

            state = nacist_ha_stav(reference)

            if state is None:
                snapshot_complete = False

            value, value_type, unit = (
                normalizovat_ha_hodnotu(
                    reference,
                    state,
                )
            )

            entities.append(
                {
                    "entity_key":
                        f"{prefix}.{entity_suffix}",
                    "category":
                        "pv_surplus_target",
                    "name":
                        f"{target_name} {display_name}",
                    "value":
                        value,
                    "unit":
                        unit,
                    "value_type":
                        value_type,
                    "quality":
                        ziskej_kvalitu_ha_stavu(
                            state
                        ),
                    "source_address":
                        None,
                    "attributes": {
                        "target_id":
                            target_id,
                        "target_name":
                            target_name,
                        "ha_entity_id":
                            reference,
                    },
                }
            )

    keys = [
        entity["entity_key"]
        for entity in entities
    ]

    if len(keys) != len(set(keys)):
        raise RuntimeError(
            "PV surplus mapper vytvoril "
            "duplicitni entity_key."
        )

    return entities, snapshot_complete


def odeslat_pv_surplus_telemetrii(
    *,
    identity: dict[str, Any],
    cloud_config: dict[str, Any],
) -> None:
    """Odesle jeden samostatny snapshot rizeni prebytku."""
    runtime_configuration = najdi_pv_surplus_runtime(
        cloud_config
    )

    if runtime_configuration is None:
        return

    entities, snapshot_complete = (
        vytvorit_pv_surplus_entity(
            runtime_configuration
        )
    )

    if not entities:
        logging.info(
            "PV surplus nema zadne aktivni "
            "overene telemetricke entity."
        )
        return

    response = cloud_client.submit_module_telemetry(
        device_uuid=str(
            identity["device_uuid"]
        ),
        device_token=str(
            identity["device_token"]
        ),
        module_key="pv_surplus_control",
        source="home_assistant",
        captured_at=vytvorit_cas_snapshotu(),
        entities=entities,
        snapshot_complete=snapshot_complete,
    )

    if (
        not isinstance(response, dict)
        or not response.get("ok")
    ):
        status_code = (
            response.get("status_code")
            if isinstance(response, dict)
            else None
        )

        error = (
            response.get("error")
            if isinstance(response, dict)
            else "Neplatna odpoved cloudoveho klienta."
        )

        raise RuntimeError(
            "Odeslani PV surplus telemetrie selhalo. "
            f"HTTP: {status_code}, chyba: {error}"
        )

    logging.info(
        "PV surplus telemetrie odeslana. "
        "Pocet entit: %s, uplny snapshot: %s.",
        len(entities),
        snapshot_complete,
    )


_pv_surplus_dry_run_state: dict[str, Any] = {
    "state": STAV_OFF,
    "confirming_since": None,
}


def aplikovat_pv_surplus_telemetry_grace(
    *,
    energy_result: dict[str, Any],
    previous_state: str,
    output_active: bool,
    now: float,
) -> dict[str, Any]:
    """
    Zachova bezici cil pri kratkodobem vypadku FVE dat.

    Grace plati pouze kdyz:
    - energeticke rozhodnuti je FAULT,
    - predchozi stav byl ACTIVE,
    - fyzicky vystup uz je ON.

    Platne OFF/BLOCKED rozhodnuti se nemeni.
    """
    if (
        energy_result.get("state")
        != STAV_FAULT
    ):
        _pv_surplus_dry_run_state[
            "telemetry_fault_since"
        ] = None

        return energy_result

    if (
        previous_state != STAV_ACTIVE
        or not output_active
    ):
        _pv_surplus_dry_run_state[
            "telemetry_fault_since"
        ] = None

        return energy_result

    now_value = float(now)

    fault_since = (
        _pv_surplus_dry_run_state.get(
            "telemetry_fault_since"
        )
    )

    if fault_since is None:
        fault_since = now_value

        _pv_surplus_dry_run_state[
            "telemetry_fault_since"
        ] = fault_since

    else:
        fault_since = float(
            fault_since
        )

    if fault_since > now_value:
        fault_since = now_value

        _pv_surplus_dry_run_state[
            "telemetry_fault_since"
        ] = fault_since

    elapsed = (
        now_value
        - fault_since
    )

    if (
        elapsed
        >= PV_SURPLUS_TELEMETRY_GRACE_SECONDS
    ):
        return {
            **energy_result,
            "reason": (
                "telemetry_fault_timeout/"
                + str(
                    energy_result.get(
                        "reason"
                    )
                    or "unknown"
                )
            ),
            "telemetry_grace_active":
                False,
            "telemetry_fault_elapsed_seconds":
                elapsed,
            "telemetry_fault_timeout_seconds":
                PV_SURPLUS_TELEMETRY_GRACE_SECONDS,
        }

    return {
        **energy_result,
        "state":
            STAV_ACTIVE,
        "reason": (
            "telemetry_grace/"
            + str(
                energy_result.get(
                    "reason"
                )
                or "unknown"
            )
        ),
        "surplus_available":
            True,
        "confirming_since":
            None,
        "confirmation_elapsed_seconds":
            0.0,
        "telemetry_grace_active":
            True,
        "telemetry_fault_elapsed_seconds":
            elapsed,
        "telemetry_fault_timeout_seconds":
            PV_SURPLUS_TELEMETRY_GRACE_SECONDS,
    }


def vyhodnotit_pv_surplus_dry_run(
    *,
    cloud_config: dict[str, Any],
    fve_entities: list[dict[str, Any]],
    now: float,
) -> dict[str, Any] | None:
    """
    Vyhodnoti zivy PV surplus runtime pouze v dry-run rezimu.

    Funkce nikdy fyzicky neovlada vystup.
    """
    runtime_configuration = najdi_pv_surplus_runtime(
        cloud_config
    )

    if runtime_configuration is None:
        return None

    configuration = runtime_configuration.get(
        "configuration"
    )

    if not isinstance(configuration, dict):
        return None

    surplus_source = configuration.get(
        "surplus_source"
    )

    if not isinstance(surplus_source, dict):
        return None

    if surplus_source.get("type") != "battery_soc":
        logging.info(
            "PV surplus dry-run: zdroj %s zatim "
            "neni podporovan Decision Enginem.",
            surplus_source.get("type"),
        )
        return None

    battery_soc = surplus_source.get(
        "battery_soc"
    )

    if not isinstance(battery_soc, dict):
        return None

    source_configuration = {
        **battery_soc,
        "confirmation_seconds":
            surplus_source.get(
                "confirmation_seconds",
                30,
            ),
    }

    targets = configuration.get("targets")

    if not isinstance(targets, list):
        return None

    target = next(
        (
            item
            for item in sorted(
                (
                    item
                    for item in targets
                    if isinstance(item, dict)
                    and item.get("enabled") is True
                    and item.get(
                        "configuration_status"
                    )
                    == "verified"
                ),
                key=lambda item: int(
                    item.get("priority") or 999999
                ),
            )
        ),
        None,
    )

    if target is None:
        return None

    sensors = target.get("sensors")

    if not isinstance(sensors, list):
        return None

    temperature_sensor = next(
        (
            sensor
            for sensor in sensors
            if isinstance(sensor, dict)
            and sensor.get("status") == "verified"
            and sensor.get("role")
            == "water_temperature"
            and str(
                sensor.get("reference") or ""
            ).strip()
        ),
        None,
    )

    if temperature_sensor is None:
        return None

    temperature_reference = str(
        temperature_sensor["reference"]
    ).strip()

    temperature_state = nacist_ha_stav(
        temperature_reference
    )

    temperature_value, _, _ = (
        normalizovat_ha_hodnotu(
            temperature_reference,
            temperature_state,
        )
    )

    output = target.get("output")

    if not isinstance(output, dict):
        return None

    output_reference = str(
        output.get("reference") or ""
    ).strip()

    if not output_reference:
        return None

    output_state = nacist_ha_stav(
        output_reference
    )

    output_value, _, _ = normalizovat_ha_hodnotu(
        output_reference,
        output_state,
    )

    output_active = output_value is True

    previous_state = str(
        _pv_surplus_dry_run_state.get(
            "state"
        )
        or STAV_OFF
    )

    confirming_since = (
        _pv_surplus_dry_run_state.get(
            "confirming_since"
        )
    )

    energy_result = vyhodnotit_stav_prebytku(
        konfigurace=source_configuration,
        fve_entity=fve_entities,
        predchozi_stav=previous_state,
        confirming_since=confirming_since,
        now=now,
    )

    energy_result = aplikovat_pv_surplus_telemetry_grace(
        energy_result=energy_result,
        previous_state=previous_state,
        output_active=output_active,
        now=now,
    )

    target_result = vyhodnotit_teplotu_cile(
        target=target,
        temperature_c=temperature_value,
        vystup_aktivni=output_active,
    )

    combined_result = vyhodnotit_cil_prebytku(
        surplus_result=energy_result,
        target_result=target_result,
    )

    _pv_surplus_dry_run_state["state"] = (
        energy_result["state"]
    )

    _pv_surplus_dry_run_state[
        "confirming_since"
    ] = energy_result.get(
        "confirming_since"
    )

    if combined_result["should_be_on"]:
        would_action = (
            "NONE_ALREADY_ON"
            if output_active
            else "WOULD_TURN_ON"
        )
    else:
        would_action = (
            "WOULD_TURN_OFF"
            if output_active
            else "NONE_ALREADY_OFF"
        )

    result = {
        "target_name":
            target.get("name") or "Cil",
        "output_reference":
            output_reference,
        "energy_state":
            energy_result["state"],
        "energy_reason":
            energy_result["reason"],
        "target_state":
            target_result["state"],
        "target_reason":
            target_result["reason"],
        "heat_demand":
            target_result["heat_demand"],
        "should_be_on":
            combined_result["should_be_on"],
        "would_action":
            would_action,
        "actual_output_on":
            output_active,
        "soc_percent":
            energy_result.get("soc_percent"),
        "pv_power_w":
            energy_result.get("pv_power_w"),
        "grid_power_w":
            energy_result.get("grid_power_w"),
        "temperature_c":
            target_result.get("temperature_c"),
    }

    logging.info(
        "PV SURPLUS DRY-RUN | "
        "cil=%s | energy=%s/%s | "
        "target=%s/%s | "
        "soc=%s %% | pv=%s W | grid=%s W | "
        "teplota=%s C | actual=%s | "
        "should_be_on=%s | action=%s",
        result["target_name"],
        result["energy_state"],
        result["energy_reason"],
        result["target_state"],
        result["target_reason"],
        result["soc_percent"],
        result["pv_power_w"],
        result["grid_power_w"],
        result["temperature_c"],
        result["actual_output_on"],
        result["should_be_on"],
        result["would_action"],
    )

    return result



def _r3r11_target_priority(target: dict[str, Any]) -> int:
    try:
        return int(target.get("priority") or 999999)
    except (TypeError, ValueError):
        return 999999


def _r3r11_active_targets(
    configuration: dict[str, Any],
) -> list[dict[str, Any]]:
    targets = configuration.get("targets")
    if not isinstance(targets, list):
        return []

    return sorted(
        (
            target
            for target in targets
            if isinstance(target, dict)
            and target.get("enabled") is True
            and target.get("configuration_status") == "verified"
        ),
        key=_r3r11_target_priority,
    )


def _r3r11_target_temperature_sensor(
    target: dict[str, Any],
) -> dict[str, Any] | None:
    target_type = str(target.get("type") or "").strip().lower()

    if target_type == "domestic_hot_water":
        accepted_roles = ("water_temperature",)
    elif target_type == "generic_load":
        # Historical transport alias remains accepted.
        accepted_roles = ("temperature", "water_temperature")
    else:
        return None

    sensors = target.get("sensors")
    if not isinstance(sensors, list):
        return None

    for preferred_role in accepted_roles:
        for sensor in sensors:
            if not isinstance(sensor, dict):
                continue
            if sensor.get("status") != "verified":
                continue

            role = str(sensor.get("role") or "").strip().lower()
            reference = str(sensor.get("reference") or "").strip()

            if role == preferred_role and reference:
                return sensor

    return None


def _r3r11_read_target_context(
    target: dict[str, Any],
) -> dict[str, Any]:
    target_id = str(target.get("id") or "").strip()
    target_name = str(target.get("name") or "Cil").strip()
    target_type = str(target.get("type") or "").strip().lower()

    if target_type not in {
        "domestic_hot_water",
        "generic_load",
    }:
        raise ValueError(
            "Nepodporovany typ energetickeho cile: "
            f"{target_type or 'unknown'}"
        )

    conditions = target.get("conditions")
    if not isinstance(conditions, dict):
        raise ValueError("Energeticky cil nema conditions.")

    temperature_operator = str(
        conditions.get("temperature_operator")
        or "below_or_equal"
    ).strip().lower()

    if target_type == "domestic_hot_water":
        temperature_operator = "below_or_equal"

    if (
        target_type == "generic_load"
        and temperature_operator != "below_or_equal"
    ):
        raise ValueError(
            "Autonomni generic_load zatim podporuje "
            "pouze temperature_operator=below_or_equal."
        )

    sensor = _r3r11_target_temperature_sensor(target)
    if sensor is None:
        raise ValueError(
            "Energeticky cil nema overene "
            "teplotni cidlo podporovane role."
        )

    temperature_reference = str(
        sensor.get("reference") or ""
    ).strip()

    temperature_state = nacist_ha_stav(temperature_reference)
    temperature_value, _, _ = normalizovat_ha_hodnotu(
        temperature_reference,
        temperature_state,
    )

    if temperature_value is None:
        raise ValueError(
            "Teplotni entita energetickeho cile "
            "nema platnou hodnotu."
        )

    output = target.get("output")
    if not isinstance(output, dict):
        raise ValueError("Energeticky cil nema vystup.")

    if output.get("status") != "verified":
        raise ValueError("Vystup energetickeho cile neni overen.")

    output_reference = str(
        output.get("reference") or ""
    ).strip()

    if not output_reference:
        raise ValueError("Energeticky cil nema output reference.")

    output_state = nacist_ha_stav(output_reference)
    output_value, _, _ = normalizovat_ha_hodnotu(
        output_reference,
        output_state,
    )

    if not isinstance(output_value, bool):
        raise ValueError(
            "Vystup energetickeho cile nema platny boolean stav."
        )

    return {
        "target": target,
        "target_id": target_id,
        "target_name": target_name,
        "target_type": target_type,
        "priority": _r3r11_target_priority(target),
        "temperature_operator": temperature_operator,
        "temperature_reference": temperature_reference,
        "temperature_c": temperature_value,
        "output_reference": output_reference,
        "actual_output_on": output_value,
    }


def vyhodnotit_pv_surplus_targets_dry_run(
    *,
    cloud_config: dict[str, Any],
    fve_entities: list[dict[str, Any]],
    now: float,
) -> list[dict[str, Any]]:
    """
    R3R11 isolated multi-target evaluator.

    FVE energy state is evaluated once.
    Every verified target is evaluated independently.
    Failure of one target must not suppress another target.
    """
    runtime_configuration = najdi_pv_surplus_runtime(cloud_config)
    if runtime_configuration is None:
        return []

    configuration = runtime_configuration.get("configuration")
    if not isinstance(configuration, dict):
        return []

    surplus_source = configuration.get("surplus_source")
    if not isinstance(surplus_source, dict):
        return []

    if surplus_source.get("type") != "battery_soc":
        return []

    battery_soc = surplus_source.get("battery_soc")
    if not isinstance(battery_soc, dict):
        return []

    source_configuration = {
        **battery_soc,
        "confirmation_seconds": surplus_source.get(
            "confirmation_seconds",
            30,
        ),
    }

    targets = _r3r11_active_targets(configuration)
    if not targets:
        return []

    contexts = []
    errors = {}

    for target in targets:
        target_id = str(target.get("id") or "").strip()
        try:
            contexts.append(_r3r11_read_target_context(target))
        except Exception as exc:
            errors[target_id] = str(exc)

    any_output_active = any(
        context.get("actual_output_on") is True
        for context in contexts
    )

    previous_state = str(
        _pv_surplus_dry_run_state.get("state")
        or STAV_OFF
    )
    confirming_since = _pv_surplus_dry_run_state.get(
        "confirming_since"
    )

    energy_result = vyhodnotit_stav_prebytku(
        konfigurace=source_configuration,
        fve_entity=fve_entities,
        predchozi_stav=previous_state,
        confirming_since=confirming_since,
        now=now,
    )

    energy_result = aplikovat_pv_surplus_telemetry_grace(
        energy_result=energy_result,
        previous_state=previous_state,
        output_active=any_output_active,
        now=now,
    )

    _pv_surplus_dry_run_state["state"] = energy_result["state"]
    _pv_surplus_dry_run_state["confirming_since"] = (
        energy_result.get("confirming_since")
    )

    context_by_id = {
        context["target_id"]: context
        for context in contexts
    }

    results = []

    for target in targets:
        target_id = str(target.get("id") or "").strip()
        target_name = str(target.get("name") or "Cil").strip()
        target_type = str(target.get("type") or "").strip().lower()
        priority = _r3r11_target_priority(target)
        context = context_by_id.get(target_id)

        if context is None:
            results.append(
                {
                    "target_id": target_id,
                    "target_name": target_name,
                    "target_type": target_type,
                    "priority": priority,
                    "resource_key": "pv_surplus_target:" + target_id,
                    "energy_state": energy_result["state"],
                    "energy_reason": energy_result["reason"],
                    "target_state": STAV_FAULT,
                    "target_reason": errors.get(
                        target_id,
                        "target_context_fault",
                    ),
                    "heat_demand": False,
                    "should_be_on": False,
                    "actual_output_on": None,
                    "can_actuate": False,
                    "soc_percent": energy_result.get("soc_percent"),
                    "pv_power_w": energy_result.get("pv_power_w"),
                    "grid_power_w": energy_result.get("grid_power_w"),
                }
            )
            continue

        try:
            target_result = vyhodnotit_teplotu_cile(
                target=target,
                temperature_c=context["temperature_c"],
                vystup_aktivni=context["actual_output_on"],
            )

            combined_result = vyhodnotit_cil_prebytku(
                surplus_result=energy_result,
                target_result=target_result,
            )

            # R3R20R6_MULTI_TARGET_GRACE_HOLD_ONLY
            actual_output_on = bool(context["actual_output_on"])
            should_be_on = bool(combined_result["should_be_on"])

            # Historical grace contract may keep only a target
            # that was already physically ON. A different OFF
            # target must never start merely because another
            # target keeps the shared energy state in grace.
            telemetry_grace_hold_only = (
                energy_result.get("telemetry_grace_active") is True
                and not actual_output_on
            )

            if telemetry_grace_hold_only:
                should_be_on = False

            if should_be_on:
                would_action = (
                    "NONE_ALREADY_ON"
                    if actual_output_on
                    else "WOULD_TURN_ON"
                )
            else:
                would_action = (
                    "WOULD_TURN_OFF"
                    if actual_output_on
                    else "NONE_ALREADY_OFF"
                )

            result = {
                "target_id": target_id,
                "target_name": target_name,
                "target_type": target_type,
                "priority": priority,
                "resource_key": "pv_surplus_target:" + target_id,
                "temperature_operator": context["temperature_operator"],
                "temperature_reference": context["temperature_reference"],
                "output_reference": context["output_reference"],
                "energy_state": energy_result["state"],
                "energy_reason": energy_result["reason"],
                "target_state": target_result["state"],
                "target_reason": target_result["reason"],
                "heat_demand": target_result["heat_demand"],
                "should_be_on": should_be_on,
                "would_action": would_action,
                "actual_output_on": actual_output_on,
                "can_actuate": True,
                "telemetry_grace_hold_only":
                    telemetry_grace_hold_only,
                "soc_percent": energy_result.get("soc_percent"),
                "pv_power_w": energy_result.get("pv_power_w"),
                "grid_power_w": energy_result.get("grid_power_w"),
                "temperature_c": target_result.get("temperature_c"),
            }

        except Exception as exc:
            result = {
                "target_id": target_id,
                "target_name": target_name,
                "target_type": target_type,
                "priority": priority,
                "resource_key": "pv_surplus_target:" + target_id,
                "output_reference": context.get("output_reference"),
                "energy_state": energy_result["state"],
                "energy_reason": energy_result["reason"],
                "target_state": STAV_FAULT,
                "target_reason": str(exc),
                "heat_demand": False,
                "should_be_on": False,
                "actual_output_on": context.get("actual_output_on"),
                "can_actuate": False,
                "soc_percent": energy_result.get("soc_percent"),
                "pv_power_w": energy_result.get("pv_power_w"),
                "grid_power_w": energy_result.get("grid_power_w"),
            }

        results.append(result)

        logging.info(
            "PV SURPLUS TARGET | "
            "cil=%s | typ=%s | priorita=%s | "
            "energy=%s/%s | target=%s/%s | "
            "soc=%s %% | teplota=%s C | "
            "actual=%s | should=%s | action=%s",
            result["target_name"],
            result["target_type"],
            result["priority"],
            result["energy_state"],
            result["energy_reason"],
            result["target_state"],
            result["target_reason"],
            result.get("soc_percent"),
            result.get("temperature_c"),
            result.get("actual_output_on"),
            result.get("should_be_on"),
            result.get("would_action"),
        )

    return results

def provest_pv_surplus_action(
    *,
    output_reference: str,
    should_be_on: bool,
    actual_output_on: bool,
) -> dict[str, Any]:
    """
    Provede jeden fyzicky povel nad overenym
    binary_power_output.

    Konkretni HA domena je pouze adapter.
    """

    normalized_reference = str(
        output_reference or ""
    ).strip()

    service_domain = (
        get_binary_power_output_service_domain(
            normalized_reference
        )
    )

    requested_on = bool(
        should_be_on
    )

    actual_on = bool(
        actual_output_on
    )

    if requested_on == actual_on:

        return {
            "action": (
                "NONE_ALREADY_ON"
                if requested_on
                else "NONE_ALREADY_OFF"
            ),

            "service_called":
                False,

            "capability":
                "binary_power_output",

            "service_domain":
                service_domain,

            "output_reference":
                normalized_reference,
        }

    service_result = (
        call_binary_power_output_service(
            entity_id=normalized_reference,
            desired_on=requested_on,
        )
    )

    readback_state = get_entity_state(
        normalized_reference
    )

    readback_raw = str(
        readback_state.get(
            "state"
        )
        or ""
    ).strip().lower()

    if readback_raw not in {
        "on",
        "off",
    }:
        raise RuntimeError(
            "PV surplus actuator read-back "
            "vratil neplatny stav: "
            f"{readback_raw!r}."
        )

    readback_on = (
        readback_raw == "on"
    )

    if readback_on != requested_on:

        raise RuntimeError(
            "PV surplus actuator nebyl "
            "potvrzen read-back kontrolou."
        )

    return {
        "action": (
            "TURNED_ON"
            if requested_on
            else "TURNED_OFF"
        ),

        "service_called":
            True,

        "capability":
            "binary_power_output",

        "service":
            service_result["service"],

        "service_domain":
            service_domain,

        "output_reference":
            normalized_reference,

        "readback_verified":
            True,

        "readback_state":
            readback_raw,
    }

def vyhodnotit_pv_surplus_control_jednou(
    *,
    fve_entities: list[dict[str, Any]],
    now: float | None = None,
) -> dict[str, Any] | None:
    """
    R3R11 multi-target control dispatcher.

    Shared FVE decision is evaluated once.
    Targets are controlled independently.
    Newer target-intent and spot-boiler arbitration is preserved.
    """
    cloud_config = load_cached_cloud_config()

    if not isinstance(cloud_config, dict):
        raise RuntimeError(
            "Cloudova konfigurace zatim neni dostupna."
        )

    pv_surplus_runtime = najdi_pv_surplus_runtime(cloud_config)
    if pv_surplus_runtime is None:
        return None

    if not isinstance(fve_entities, list) or not fve_entities:
        raise RuntimeError(
            "FVE entity pro rizeni prebytku nejsou dostupne."
        )

    configuration = pv_surplus_runtime.get("configuration")
    if not isinstance(configuration, dict):
        raise RuntimeError(
            "PV surplus runtime nema platnou konfiguraci."
        )

    evaluation_time = (
        time.monotonic()
        if now is None
        else float(now)
    )

    results = vyhodnotit_pv_surplus_targets_dry_run(
        cloud_config=cloud_config,
        fve_entities=fve_entities,
        now=evaluation_time,
    )

    if not results:
        return None

    actuation_enabled = (
        configuration.get("actuation_enabled") is True
    )

    used_outputs: set[str] = set()

    for result in results:
        result["actuation_enabled"] = actuation_enabled
        result["actuator_result"] = None
        result["actuator_error"] = None

        if result.get("can_actuate") is not True:
            result["control_source"] = "target_fault"
            logging.warning(
                "PV SURPLUS TARGET SKIP | cil=%s | reason=%s",
                result.get("target_name"),
                result.get("target_reason"),
            )
            continue

        output_reference = str(
            result.get("output_reference") or ""
        ).strip()

        if (
            not output_reference
            or output_reference in used_outputs
        ):
            result["control_source"] = "duplicate_output_guard"
            result["actuator_error"] = (
                "Output je prazdny nebo jej pouziva vice cilu."
            )
            continue

        used_outputs.add(output_reference)

        # F43: verified active cloud weekly thermostat exclusively owns this relay.
        # The local SOC control may report telemetry but must not drive it.
        if (str(result.get("target_type") or "") == "generic_load"
                and is_verified_weekly_owner(
                    cloud_config,
                    target_id=str(result.get("target_id") or ""),
                    output_reference=output_reference,
                )):
            result["control_source"] = "cloud_weekly_exclusive"
            result["local_actuation_suppressed"] = True
            logging.info(
                "F43 weekly exclusive: local SOC relay write skipped target=%s",
                result.get("target_id"),
            )
            continue

        desired_on = bool(result.get("should_be_on"))
        target_type = str(
            result.get("target_type") or ""
        ).strip().lower()

        if target_type == "domestic_hot_water":
            spot_intent = load_spot_boiler_intent()

            if (
                isinstance(spot_intent, dict)
                and str(
                    spot_intent.get("output_reference") or ""
                ).strip()
                != output_reference
            ):
                spot_intent = None

            combined_control = combine_boiler_requests(
                pv_should_be_on=desired_on,
                spot_intent=spot_intent,
            )

            result["pv_should_be_on"] = (
                combined_control["pv_should_be_on"]
            )
            result["spot_should_be_on"] = (
                combined_control["spot_should_be_on"]
            )
            result["spot_intent"] = spot_intent
            result["control_source"] = combined_control["source"]
            desired_on = bool(combined_control["should_be_on"])

        elif target_type == "generic_load":
            target_intent = None

            try:
                target_intent = load_pv_surplus_target_intent(
                    resource_key=result["resource_key"],
                    output_reference=output_reference,
                )
            except Exception as exc:
                logging.warning(
                    "PV target intent read selhal pro %s: %s",
                    result.get("target_name"),
                    exc,
                )

            result["target_intent"] = target_intent

            if isinstance(target_intent, dict):
                desired_on = (
                    target_intent.get("desired_on") is True
                )
                result["control_source"] = (
                    "pv_surplus_target_intent"
                )
            else:
                result["control_source"] = "pv_surplus"

        else:
            result["control_source"] = "unsupported_target_type"
            continue

        result["should_be_on"] = desired_on

        if not actuation_enabled:
            logging.info(
                "PV SURPLUS ACTUATOR | cil=%s | "
                "enabled=False | action=DRY_RUN_ONLY",
                result.get("target_name"),
            )
            continue

        try:
            actuator_result = provest_pv_surplus_action(
                output_reference=output_reference,
                should_be_on=desired_on,
                actual_output_on=bool(
                    result.get("actual_output_on")
                ),
            )

            result["actuator_result"] = actuator_result

            logging.info(
                "PV SURPLUS ACTUATOR | cil=%s | typ=%s | "
                "source=%s | output=%s | action=%s | readback=%s",
                result.get("target_name"),
                target_type,
                result.get("control_source"),
                output_reference,
                actuator_result.get("action"),
                actuator_result.get("readback_state"),
            )

        except Exception as exc:
            result["actuator_error"] = str(exc)
            logging.warning(
                "PV SURPLUS ACTUATOR selhal | "
                "cil=%s | output=%s | error=%s",
                result.get("target_name"),
                output_reference,
                exc,
            )

    if len(results) == 1:
        return results[0]

    return {
        "multi_target": True,
        "target_count": len(results),
        "actuation_enabled": actuation_enabled,
        "energy_state": results[0].get("energy_state"),
        "energy_reason": results[0].get("energy_reason"),
        "targets": results,
    }

def vytvorit_cas_snapshotu() -> str:
    """Vrati aktualni UTC cas ve formatu ISO 8601."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def odeslat_baterie_telemetrii(
    *,
    identity: dict[str, Any],
    cloud_config: dict[str, Any],
) -> None:
    """
    Odesle samostatnou Pylontech bateriovou telemetrii.

    Selhani baterie nesmi zastavit FVE ani ostatni moduly.
    """
    runtime_configuration = (
        najdi_baterie_runtime(
            cloud_config
        )
    )

    if runtime_configuration is None:
        return

    snapshot = read_pylontech_snapshot(
        runtime_configuration
    )

    if not isinstance(snapshot, dict):
        raise RuntimeError(
            "Pylontech adapter nevratil snapshot."
        )

    entities = snapshot.get("entities")

    if (
        not isinstance(entities, list)
        or not entities
    ):
        raise RuntimeError(
            "Pylontech snapshot nema entity."
        )

    telemetry_source = str(
        snapshot.get("telemetry_source")
        or "pylontech_us5000_rs232"
    ).strip()

    response = cloud_client.submit_module_telemetry(
        device_uuid=str(
            identity["device_uuid"]
        ),
        device_token=str(
            identity["device_token"]
        ),
        module_key="battery",
        source=telemetry_source,
        captured_at=vytvorit_cas_snapshotu(),
        entities=entities,
        snapshot_complete=(
            snapshot.get("complete")
            is True
        ),
    )

    if (
        not isinstance(response, dict)
        or not response.get("ok")
    ):
        status_code = (
            response.get("status_code")
            if isinstance(response, dict)
            else None
        )
        error = (
            response.get("error")
            if isinstance(response, dict)
            else "Neplatna odpoved cloudoveho klienta."
        )

        raise RuntimeError(
            "Odeslani bateriove telemetrie selhalo. "
            f"HTTP: {status_code}, chyba: {error}"
        )

    logging.info(
        "Pylontech bateriova telemetrie odeslana. "
        "Moduly=%s/%s entity=%s.",
        snapshot.get(
            "detected_module_count"
        ),
        snapshot.get(
            "expected_module_count"
        ),
        len(entities),
    )


def odeslat_telemetrii_jednou() -> int:
    """Nacte a odesle jeden snapshot FVE telemetrie."""
    identity = load_device_identity()
    cloud_config = load_cached_cloud_config()

    if not isinstance(cloud_config, dict):
        raise RuntimeError(
            "Cloudova konfigurace zatim neni dostupna."
        )

    local_interval = ziskej_interval_telemetrie(
        cloud_config
    )

    try:
        odeslat_baterie_telemetrii(
            identity=identity,
            cloud_config=cloud_config,
        )
    except Exception as exc:
        logging.warning(
            "Pylontech bateriova telemetrie selhala: %s",
            exc,
        )

    runtime_configuration = najdi_fve_runtime(
        cloud_config
    )

    if runtime_configuration is None:
        logging.info(
            "Aktivni podporovana FVE telemetrie neni "
            "v cloudove konfiguraci povolena."
        )

        try:
            odeslat_pv_surplus_telemetrii(
                identity=identity,
                cloud_config=cloud_config,
            )
        except Exception as exc:
            logging.warning(
                "PV surplus telemetrie selhala bez "
                "aktivni FVE telemetrie: %s",
                exc,
            )

        return local_interval

    (
        snapshot,
        entities,
        telemetry_source,
        telemetry_reader,
    ) = nacti_fve_telemetrii(
        runtime_configuration
    )

    manufacturer = str(
        runtime_configuration.get(
            "manufacturer"
        )
        or ""
    ).strip().lower()

    model = str(
        runtime_configuration.get(
            "model"
        )
        or ""
    ).strip()

    logging.info(
        "FVE telemetrie reader=%s, vyrobce=%s, model=%s.",
        telemetry_reader,
        manufacturer,
        model,
    )

    try:
        #
        # Rizeni prebytku vzdy dostava stejny
        # normalizovany snapshot z univerzalniho
        # inverter adapteru. Vyrobce ani model
        # zde nesmi ovlivnit rozhodovaci logiku.
        #
        vyhodnotit_pv_surplus_control_jednou(
            fve_entities=entities,
            now=time.monotonic(),
        )

    except Exception as exc:
        logging.warning(
            "PV surplus Decision Engine selhal: %s",
            exc,
        )

    captured_at = vytvorit_cas_snapshotu()

    snapshot_complete = (
        snapshot.get(
            "complete"
        )
        is True
    )

    response = cloud_client.submit_module_telemetry(
        device_uuid=str(
            identity[
                "device_uuid"
            ]
        ),
        device_token=str(
            identity[
                "device_token"
            ]
        ),
        module_key="photovoltaic",
        source=telemetry_source,
        captured_at=captured_at,
        entities=entities,
        snapshot_complete=snapshot_complete,
    )

    if (
        not isinstance(
            response,
            dict,
        )
        or not response.get(
            "ok"
        )
    ):
        status_code = (
            response.get(
                "status_code"
            )
            if isinstance(
                response,
                dict,
            )
            else None
        )

        error = (
            response.get(
                "error"
            )
            if isinstance(
                response,
                dict,
            )
            else "Neplatna odpoved cloudoveho klienta."
        )

        raise RuntimeError(
            "Odeslani FVE telemetrie selhalo. "
            f"HTTP: {status_code}, chyba: {error}"
        )

    response_data = response.get(
        "data"
    )

    if not isinstance(
        response_data,
        dict,
    ):
        response_data = {}

    next_interval = ziskej_interval_telemetrie(
        response_data
        if "telemetry_interval_seconds"
        in response_data
        else cloud_config
    )

    logging.info(
        "FVE telemetrie odeslana. "
        "Prijato: %s, aktualizovano: %s, "
        "uplny snapshot: %s, dalsi odeslani za %s s.",
        response_data.get(
            "entities_received",
            len(
                entities
            ),
        ),
        response_data.get(
            "entities_updated",
            len(
                entities
            ),
        ),
        snapshot_complete,
        next_interval,
    )

    try:
        odeslat_pv_surplus_telemetrii(
            identity=identity,
            cloud_config=cloud_config,
        )

    except Exception as exc:
        logging.warning(
            "PV surplus telemetrie selhala, "
            "FVE telemetrie zustava aktivni: %s",
            exc,
        )

    return next_interval



def main() -> None:
    """Spusti nekonecnou sluzbu telemetrie modulu."""
    logging.info(
        "Sluzba telemetrie modulu byla spustena."
    )

    while True:
        try:
            interval = odeslat_telemetrii_jednou()
        except Exception as exc:
            logging.warning(
                "Cyklus telemetrie modulu selhal: %s",
                exc,
            )
            interval = ERROR_RETRY_INTERVAL_SECONDS

        time.sleep(interval)


if __name__ == "__main__":
    main()
