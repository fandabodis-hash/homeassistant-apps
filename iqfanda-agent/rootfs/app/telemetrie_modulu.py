"""Periodicky sber a odesilani telemetrie modulu."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from communication.fve_entity_mapper import (
    vytvorit_goodwe_fve_entity,
)
from communication.goodwe_probe import (
    read_goodwe_control_snapshot,
    read_goodwe_et_snapshot,
)
from device_config import (
    load_cached_cloud_config,
    load_device_identity,
)
from host.cloud_client import cloud_client
from zigbee_manager import (
    call_home_assistant_service,
    get_entity_state,
)

from spot_boiler_intent import (
    combine_boiler_requests,
    load_spot_boiler_intent,
)

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


def najdi_goodwe_fve_runtime(
    cloud_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Najde povolenou read-only GoodWe FVE konfiguraci."""
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

        manufacturer = str(
            runtime_configuration.get("manufacturer") or ""
        ).strip().lower()

        if (
            module_key == "photovoltaic"
            and manufacturer == "goodwe"
            and runtime_configuration.get(
                "telemetry_enabled"
            )
            is True
            and runtime_configuration.get("read_only") is True
        ):
            return runtime_configuration

    return None



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

                if (
                    role != "water_temperature"
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
                                "water_temperature",
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
    goodwe_entities: list[dict[str, Any]],
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
        goodwe_entity=goodwe_entities,
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


def provest_pv_surplus_action(
    *,
    output_reference: str,
    should_be_on: bool,
    actual_output_on: bool,
) -> dict[str, Any]:
    """
    Provede jeden fyzicky povel PV surplus vystupu.

    Funkce smi ovladat pouze Home Assistant switch entity.
    Pokud je vystup jiz v pozadovanem stavu, nic nevola.
    """
    normalized_reference = str(
        output_reference or ""
    ).strip()

    if not normalized_reference.startswith("switch."):
        raise ValueError(
            "PV surplus actuator smi ovladat "
            "pouze switch.* entitu."
        )

    requested_on = bool(should_be_on)
    actual_on = bool(actual_output_on)

    if requested_on == actual_on:
        return {
            "action": (
                "NONE_ALREADY_ON"
                if requested_on
                else "NONE_ALREADY_OFF"
            ),
            "service_called": False,
            "output_reference": normalized_reference,
        }

    service = (
        "turn_on"
        if requested_on
        else "turn_off"
    )

    call_home_assistant_service(
        domain="switch",
        service=service,
        payload={
            "entity_id": normalized_reference,
        },
    )

    readback_state = get_entity_state(
        normalized_reference
    )

    readback_raw = str(
        readback_state.get("state") or ""
    ).strip().lower()

    if readback_raw not in {"on", "off"}:
        raise RuntimeError(
            "PV surplus actuator read-back vratil "
            f"neplatny stav: {readback_raw!r}."
        )

    readback_on = readback_raw == "on"

    if readback_on != requested_on:
        raise RuntimeError(
            "PV surplus actuator nebyl potvrzen "
            "read-back kontrolou."
        )

    return {
        "action": (
            "TURNED_ON"
            if requested_on
            else "TURNED_OFF"
        ),
        "service_called": True,
        "service": f"switch.{service}",
        "output_reference": normalized_reference,
        "readback_verified": True,
        "readback_state": readback_raw,
    }


def vyhodnotit_pv_surplus_control_jednou(
    *,
    fve_entities: list[dict[str, Any]],
    now: float | None = None,
) -> dict[str, Any] | None:
    """
    Provede jeden rychly PV surplus control cyklus.

    Fyzicke ovladani je povoleno pouze pri
    configuration.actuation_enabled == True.
    Bez explicitni hodnoty True zustava cyklus
    pouze v dry-run rezimu.
    """
    cloud_config = load_cached_cloud_config()

    if not isinstance(cloud_config, dict):
        raise RuntimeError(
            "Cloudova konfigurace zatim neni dostupna."
        )

    pv_surplus_runtime = najdi_pv_surplus_runtime(
        cloud_config
    )

    if pv_surplus_runtime is None:
        return None

    if (
        not isinstance(fve_entities, list)
        or not fve_entities
    ):
        raise RuntimeError(
            "FVE entity pro rizeni prebytku "
            "nejsou dostupne."
        )

    control_entities = fve_entities

    evaluation_time = (
        time.monotonic()
        if now is None
        else float(now)
    )

    result = vyhodnotit_pv_surplus_dry_run(
        cloud_config=cloud_config,
        goodwe_entities=control_entities,
        now=evaluation_time,
    )

    if result is None:
        return None

    configuration = pv_surplus_runtime.get(
        "configuration"
    )

    if not isinstance(configuration, dict):
        raise RuntimeError(
            "PV surplus runtime nema platnou konfiguraci."
        )

    actuation_enabled = (
        configuration.get("actuation_enabled") is True
    )

    result["actuation_enabled"] = actuation_enabled
    result["actuator_result"] = None


    #
    # SPOT + PV SURPLUS ARBITRAZ
    #
    # Existuje pouze jeden fyzicky actuator.
    # PV a spot pouze vytvareji spolecny pozadavek.
    #
    pv_should_be_on = bool(
        result["should_be_on"]
    )

    spot_intent = load_spot_boiler_intent()

    #
    # Intent patri pouze vystupu, pro ktery
    # byl cloudem vytvoren.
    #
    if (
        isinstance(spot_intent, dict)
        and str(
            spot_intent.get(
                "output_reference"
            ) or ""
        ).strip()
        != str(
            result["output_reference"]
        ).strip()
    ):
        spot_intent = None

    combined_control = combine_boiler_requests(
        pv_should_be_on=pv_should_be_on,
        spot_intent=spot_intent,
    )

    result["pv_should_be_on"] = (
        combined_control["pv_should_be_on"]
    )

    result["spot_should_be_on"] = (
        combined_control["spot_should_be_on"]
    )

    result["control_source"] = (
        combined_control["source"]
    )

    result["spot_intent"] = spot_intent

    result["should_be_on"] = (
        combined_control["should_be_on"]
    )
    if not actuation_enabled:
        logging.info(
            "PV SURPLUS ACTUATOR | "
            "cil=%s | enabled=False | "
            "action=DRY_RUN_ONLY",
            result["target_name"],
        )

        return result

    actuator_result = provest_pv_surplus_action(
        output_reference=result["output_reference"],
        should_be_on=result["should_be_on"],
        actual_output_on=result["actual_output_on"],
    )

    result["actuator_result"] = actuator_result

    logging.info(
        "PV SURPLUS ACTUATOR | "
        "cil=%s | enabled=True | "
        "output=%s | action=%s | "
        "readback=%s",
        result["target_name"],
        result["output_reference"],
        actuator_result["action"],
        actuator_result.get("readback_state"),
    )

    return result


def vytvorit_cas_snapshotu() -> str:
    """Vrati aktualni UTC cas ve formatu ISO 8601."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
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

    runtime_configuration = najdi_goodwe_fve_runtime(
        cloud_config
    )

    if runtime_configuration is None:
        logging.info(
            "Aktivni GoodWe FVE telemetrie neni "
            "v cloudove konfiguraci povolena."
        )
        return local_interval

    snapshot = read_goodwe_et_snapshot(
        runtime_configuration
    )

    if not isinstance(snapshot, dict):
        raise RuntimeError(
            "GoodWe runtime reader nevratil platny snapshot."
        )

    entities = vytvorit_goodwe_fve_entity(snapshot)

    if not isinstance(entities, list) or not entities:
        raise RuntimeError(
            "Mapper GoodWe nevratil zadne FVE entity."
        )

    try:
        try:
            control_entities = read_goodwe_control_snapshot(
                runtime_configuration
            )
        except Exception as exc:
            logging.warning(
                "PV surplus control reader selhal; "
                "pouzivam FVE snapshot: %s",
                exc,
            )
            control_entities = entities

        vyhodnotit_pv_surplus_control_jednou(
            fve_entities=control_entities,
            now=time.monotonic(),
        )
    except Exception as exc:
        logging.warning(
            "PV surplus Decision Engine selhal: %s",
            exc,
        )

    captured_at = vytvorit_cas_snapshotu()
    snapshot_complete = snapshot.get("complete") is True

    response = cloud_client.submit_module_telemetry(
        device_uuid=str(identity["device_uuid"]),
        device_token=str(identity["device_token"]),
        module_key="photovoltaic",
        source="goodwe_rs485",
        captured_at=captured_at,
        entities=entities,
        snapshot_complete=snapshot_complete,
    )

    if not isinstance(response, dict) or not response.get("ok"):
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
            "Odeslani FVE telemetrie selhalo. "
            f"HTTP: {status_code}, chyba: {error}"
        )

    response_data = response.get("data")

    if not isinstance(response_data, dict):
        response_data = {}

    next_interval = ziskej_interval_telemetrie(
        response_data
        if "telemetry_interval_seconds" in response_data
        else cloud_config
    )

    logging.info(
        "FVE telemetrie odeslana. "
        "Prijato: %s, aktualizovano: %s, "
        "uplny snapshot: %s, dalsi odeslani za %s s.",
        response_data.get(
            "entities_received",
            len(entities),
        ),
        response_data.get(
            "entities_updated",
            len(entities),
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
            "GoodWe telemetrie zustava aktivni: %s",
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
