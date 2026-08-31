"""Cloudovy vykonavatel prikazu TNG IQ FANDA Agentu."""

import json
from datetime import datetime, timezone
import logging
import os
import time
from pathlib import Path
from typing import Any

from communication.inverter_control_adapter import (
    execute_battery_control_from_cloud_config,
)
from communication.inverter_adapter import (
    probe_inverter_modbus,
)
from agent_updater import (
    AgentUpdateError,
    clear_pending_agent_update,
    load_pending_agent_update,
    pending_update_age_seconds,
    read_installed_agent_version,
    save_pending_agent_update,
    trigger_agent_update,
    update_pending_agent_update,
    utc_now_iso,
    validate_update_target,
)
from device_config import load_cached_cloud_config
from host.cloud_client import cloud_client
from spot_battery_intent import save_spot_battery_intent
from spot_boiler_intent import save_spot_boiler_intent
from pv_surplus_target_control import (
    apply_pv_surplus_target_intent,
    reconcile_expired_pv_surplus_target_intents,
)
from zigbee_manager import (
    HomeAssistantApiError,
    call_home_assistant_service,
    find_existing_temperature_devices,
    get_entity_device_id,
    get_entity_state,
    get_home_assistant_entity_ids,
    get_zha_devices,
    open_zigbee_permit,
    trigger_zha_topology_update,
    wait_for_new_device,
)


DEVICE_CONFIG_PATH = Path(
    os.getenv(
        "IQF_DEVICE_CONFIG_PATH",
        "/config/device.json",
    )
)

CLAIM_INTERVAL_SECONDS = 2
ERROR_RETRY_SECONDS = 15


def load_device_identity() -> dict[str, Any]:
    """Nacte cloudovou identitu a token zarizeni."""

    if not DEVICE_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Konfiguracni soubor zarizeni neexistuje: "
            f"{DEVICE_CONFIG_PATH}"
        )

    with DEVICE_CONFIG_PATH.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        identity = json.load(file)

    for field_name in (
        "device_uuid",
        "device_token",
    ):
        if not identity.get(field_name):
            raise ValueError(
                f"V konfiguraci zarizeni chybi pole: "
                f"{field_name}"
            )

    return identity


def require_cloud_success(
    response: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    """Overi sjednocenou odpoved cloudoveho klienta."""

    if not isinstance(response, dict):
        raise RuntimeError(
            f"{operation}: cloudovy klient vratil "
            "neplatny format."
        )

    if not response.get("ok"):
        raise RuntimeError(
            f"{operation}: "
            f"{response.get('error') or 'neznamy problem'}"
        )

    data = response.get("data")

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise RuntimeError(
            f"{operation}: cloud vratil neplatna data."
        )

    return data


def claim_command(
    identity: dict[str, Any],
) -> dict[str, Any] | None:
    """Vyzvedne nejstarsi dostupny prikaz zarizeni."""

    response = cloud_client.claim_device_command(
        device_token=identity["device_token"],
    )

    data = require_cloud_success(
        response,
        "Vyzvednuti prikazu",
    )

    command = data.get("command")

    if command is None:
        return None

    if not isinstance(command, dict):
        raise ValueError(
            "Cloud vratil neplatny format prikazu."
        )

    for field_name in (
        "id",
        "command_type",
        "payload",
    ):
        if field_name not in command:
            raise ValueError(
                f"V prijatem prikazu chybi pole: "
                f"{field_name}"
            )

    return command


def submit_command_result(
    *,
    identity: dict[str, Any],
    command_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Odesle prubezny nebo konecny stav prikazu."""

    response = cloud_client.submit_device_command_result(
        device_token=identity["device_token"],
        command_id=command_id,
        status=status,
        result=result,
        error_message=error_message,
    )

    return require_cloud_success(
        response,
        f"Ulozeni stavu {status}",
    )


def normalize_duration_seconds(
    raw_duration: Any,
) -> int:
    """Overi delku Zigbee parovaciho rezimu."""

    try:
        duration = int(raw_duration)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Delka Zigbee parovani musi byt cele cislo."
        ) from exc

    if duration < 1 or duration > 254:
        raise ValueError(
            "Delka Zigbee parovani musi byt "
            "v rozsahu 1 az 254 sekund."
        )

    return duration


def execute_zigbee_permit_join(
    *,
    identity: dict[str, Any],
    command_id: str,
    command_payload: dict[str, Any],
) -> None:
    """
    Otevre parovani, ceka na nove zarizeni
    a overi entity podle instalacniho kontextu.
    """

    duration_seconds = normalize_duration_seconds(
        command_payload.get(
            "duration_seconds",
            180,
        )
    )

    expected_device_type = str(
        command_payload.get(
            "expected_device_type",
            "",
        )
        or ""
    ).strip()

    building_module_id = str(
        command_payload.get(
            "building_module_id",
            "",
        )
        or ""
    ).strip()

    pv_surplus_module_id = str(
        command_payload.get(
            "pv_surplus_module_id",
            "",
        )
        or ""
    ).strip()

    energy_target_id = str(
        command_payload.get(
            "energy_target_id",
            "",
        )
        or ""
    ).strip()

    entity_role = str(
        command_payload.get(
            "entity_role",
            "",
        )
        or ""
    ).strip().lower()

    replacement_mode = bool(
        command_payload.get(
            "replacement_mode",
            False,
        )
    )

    current_device_id = str(
        command_payload.get(
            "current_device_id",
            "",
        )
        or ""
    ).strip()

    # ======================================================
    # PHASE 25 INFRASTRUCTURE PAIRING
    # ======================================================
    infrastructure_context = bool(
        command_payload.get(
            "infrastructure_mode",
            False,
        )
    )
    building_context = bool(
        building_module_id
    )

    energy_context_values = (
        pv_surplus_module_id,
        energy_target_id,
        entity_role,
    )

    energy_context_count = sum(
        bool(value)
        for value in energy_context_values
    )

    energy_target_context = (
        energy_context_count
        == len(energy_context_values)
    )

    if infrastructure_context:
        if building_context or energy_context_count:
            raise ValueError(
                "Zigbee prikaz obsahuje vice "
                "instalacnich kontextu."
            )

        # ==================================================
        # PHASE26_GENERIC_ZIGBEE_EXPECTED_TYPE_0102
        # ==================================================
        if expected_device_type not in {
            "zigbee_device",
            "zigbee_router_repeater",
        }:
            raise ValueError(
                "Obecne Zigbee parovani vyzaduje typ "
                "zigbee_device."
            )

        if replacement_mode or current_device_id:
            raise ValueError(
                "Infrastrukturni Zigbee parovani nepodporuje "
                "rezim vymeny."
            )

    if not infrastructure_context:
        if building_context and energy_context_count:
            raise ValueError(
                "Zigbee prikaz obsahuje vice instalacnich kontextu."
            )

        if not building_context and energy_context_count == 0:
            raise ValueError(
                "Zigbee prikaz neobsahuje instalacni kontext."
            )

        if (
            not building_context
            and not energy_target_context
        ):
            raise ValueError(
                "Kontext energetickeho cile neni uplny."
            )

        if building_context:
            if (
                expected_device_type
                != "indoor_temperature_humidity_sensor"
            ):
                raise ValueError(
                    "Modul Budova vyzaduje vnitrni teplotni cidlo."
                )
        else:
            expected_type_by_role = {
                "water_temperature":
                    "energy_target_temperature_sensor",
                "output_switch":
                    "energy_target_switch",
            }

            expected_type = expected_type_by_role.get(
                entity_role
            )

            if expected_type is None:
                raise ValueError(
                    "Nepodporovana role energetickeho cile."
                )

            if expected_device_type != expected_type:
                raise ValueError(
                    "Role energetickeho cile neodpovida "
                    "ocekavanemu typu Zigbee zarizeni."
                )

            if replacement_mode:
                raise ValueError(
                    "Vymena zatim neni pro energeticky cil podporovana."
                )

            if current_device_id:
                raise ValueError(
                    "current_device_id nelze pouzit pro energeticky cil."
                )

    if replacement_mode and not current_device_id:
        raise ValueError(
            "V režimu výměny musí být předáno "
            "current_device_id původního zařízení."
        )

    try:
        all_existing_devices = (
            find_existing_temperature_devices()
            if building_context
            else []
        )

        existing_devices = (
            []
            if replacement_mode
            else all_existing_devices
        )

        if len(existing_devices) > 1:
            candidate_names = [
                str(
                    candidate.get(
                        "device",
                        {},
                    ).get("name")
                    or candidate.get(
                        "device",
                        {},
                    ).get("model")
                    or "Nezname Zigbee zarizeni"
                )
                for candidate in existing_devices
                if isinstance(candidate, dict)
            ]

            raise HomeAssistantApiError(
                (
                    "Bylo nalezeno vice vhodnych "
                    "ZHA teplotnich zarizeni: "
                    + ", ".join(candidate_names)
                    + ". Je nutne doplnit vyber "
                    "konkretniho zarizeni."
                )
            )

        if len(existing_devices) == 1:
            discovery_result = existing_devices[0]
            discovery_source = (
                "existing_home_assistant_device"
            )
            permit_result = {
                "service": None,
            }

            submit_command_result(
                identity=identity,
                command_id=command_id,
                status="running",
                result={
                    "worker": "command_worker",
                    "executor": "zigbee_manager",
                    "phase": "existing_device_found",
                    "candidate_count": 1,
                    "expected_device_type": (
                        expected_device_type
                    ),
                    "building_module_id": (
                        building_module_id
                    ),
                },
            )

            logging.info(
                (
                    "Bylo nalezeno existujici vhodne "
                    "ZHA teplotni zarizeni. Prikaz: %s"
                ),
                command_id,
            )

        else:
            discovery_source = "newly_paired_device"

            entity_ids_before = (
                get_home_assistant_entity_ids()
            )

            zha_devices_before = (
                get_zha_devices()
            )

            zha_device_count_before = sum(
                1
                for item in zha_devices_before
                if (
                    isinstance(item, dict)
                    and not item.get(
                        "active_coordinator"
                    )
                )
            )

            logging.info(
                (
                    "Pred Zigbee parovanim bylo nalezeno "
                    "%s Home Assistant entit. Prikaz: %s"
                ),
                len(entity_ids_before),
                command_id,
            )

            submit_command_result(
                identity=identity,
                command_id=command_id,
                status="running",
                result={
                    "worker": "command_worker",
                    "executor": "zigbee_manager",
                    "phase": "snapshot_created",
                    "entity_count_before": len(
                        entity_ids_before
                    ),
                    "zha_device_count_before": (
                        zha_device_count_before
                    ),
                    "duration_seconds": (
                        duration_seconds
                    ),
                    "expected_device_type": (
                        expected_device_type
                    ),
                    "building_module_id": (
                        building_module_id
                    ),
                },
            )

            permit_result = open_zigbee_permit(
                duration_seconds=duration_seconds,
            )

            submit_command_result(
                identity=identity,
                command_id=command_id,
                status="running",
                result={
                    "worker": "command_worker",
                    "executor": "zigbee_manager",
                    "phase": "waiting_for_device",
                    "service": (
                        permit_result["service"]
                    ),
                    "duration_seconds": (
                        duration_seconds
                    ),
                    "entity_count_before": len(
                        entity_ids_before
                    ),
                    "zha_device_count_before": (
                        zha_device_count_before
                    ),
                    "expected_device_type": (
                        expected_device_type
                    ),
                    "building_module_id": (
                        building_module_id
                    ),
                },
            )

            logging.info(
                (
                    "Zigbee parovaci rezim byl otevren "
                    "na %s sekund. Cekam na nove "
                    "zarizeni. Prikaz: %s"
                ),
                duration_seconds,
                command_id,
            )

            discovery_result = wait_for_new_device(
                entity_ids_before=entity_ids_before,
                timeout_seconds=duration_seconds,
                excluded_device_ids=(
                    {current_device_id}
                    if current_device_id
                    else set()
                ),
                zha_devices_before=(
                    zha_devices_before
                ),
            )

        device = discovery_result.get(
            "device"
        )

        entities = discovery_result.get(
            "entities"
        )

        temperature_entity = discovery_result.get(
            "temperature_entity"
        )

        humidity_entity = discovery_result.get(
            "humidity_entity"
        )

        battery_entity = discovery_result.get(
            "battery_entity"
        )

        if not isinstance(device, dict):
            raise HomeAssistantApiError(
                "Nove zarizeni nema platna metadata."
            )

        if not isinstance(entities, list):
            raise HomeAssistantApiError(
                "Nove zarizeni nema platny seznam entit."
            )

        switch_entity = None
        power_entity = None
        energy_entity = None
        current_entity = None
        voltage_entity = None

        if infrastructure_context:
            # ==================================================
            # PHASE25_INFRASTRUCTURE_DEVICE_TYPE_VERIFY_0101
            # ==================================================
            #
            # Infrastructure pairing may succeed only when
            # the device really reports ZHA device_type Router.
            #
            paired_device_id = str(
                device.get("device_id")
                or ""
            ).strip()

            if not paired_device_id:
                raise HomeAssistantApiError(
                    "Nalezen\u00e9 Zigbee za\u0159\u00edzen\u00ed "
                    "nem\u00e1 platn\u00e9 HA device_id."
                )

            current_zha_devices = (
                get_zha_devices()
            )

            matched_zha_devices = [
                item
                for item in current_zha_devices
                if (
                    isinstance(item, dict)
                    and not item.get(
                        "active_coordinator"
                    )
                    and str(
                        item.get(
                            "device_reg_id"
                        )
                        or ""
                    ).strip()
                    == paired_device_id
                )
            ]

            if len(matched_zha_devices) != 1:
                raise HomeAssistantApiError(
                    "Nalezen\u00e9 Zigbee za\u0159\u00edzen\u00ed "
                    "nelze jednozna\u010dn\u011b ov\u011b\u0159it "
                    "v ZHA invent\u00e1\u0159i."
                )

            matched_zha_device = (
                matched_zha_devices[0]
            )

            actual_zha_device_type = str(
                matched_zha_device.get(
                    "device_type"
                )
                or ""
            ).strip()

            # ==================================================
            # PHASE26_GENERIC_ZIGBEE_PAIRING_0102
            # ==================================================
            # Konfigurace Zigbee prijima libovolne podporovane
            # non-coordinator ZHA zarizeni. Skutecny ZHA typ se
            # pouze zaznamena do vysledku; neni filtrem parovani.

            device = {
                **device,
                "zha_device_type": (
                    actual_zha_device_type
                ),
                "ieee": matched_zha_device.get(
                    "ieee"
                ),
                "nwk": matched_zha_device.get(
                    "nwk"
                ),
            }

        elif (
            energy_target_context
            and entity_role == "output_switch"
        ):
            switch_candidates = [
                item
                for item in entities
                if (
                    isinstance(item, dict)
                    and str(
                        item.get("entity_id") or ""
                    ).strip().startswith("switch.")
                )
            ]

            if len(switch_candidates) != 1:
                raise HomeAssistantApiError(
                    "Nove zarizeni musi obsahovat "
                    "prave jednu switch entitu."
                )

            switch_entity = (
                switch_candidates[0]
            )

            def find_measurement_entity(
                device_class: str,
            ) -> dict[str, Any] | None:
                candidates = [
                    item
                    for item in entities
                    if (
                        isinstance(item, dict)
                        and str(
                            item.get("entity_id") or ""
                        ).strip().startswith("sensor.")
                        and str(
                            item.get("device_class") or ""
                        ).strip().lower()
                        == device_class
                    )
                ]

                if len(candidates) > 1:
                    raise HomeAssistantApiError(
                        "Zigbee spinaci prvek obsahuje "
                        "vice mericich entit device_class "
                        f"{device_class}."
                    )

                if candidates:
                    return candidates[0]

                return None

            power_entity = (
                find_measurement_entity(
                    "power"
                )
            )

            energy_entity = (
                find_measurement_entity(
                    "energy"
                )
            )

            current_entity = (
                find_measurement_entity(
                    "current"
                )
            )

            voltage_entity = (
                find_measurement_entity(
                    "voltage"
                )
            )

        else:
            if not isinstance(
                temperature_entity,
                dict,
            ):
                raise HomeAssistantApiError(
                    "Nove zarizeni nema dostupnou platnou "
                    "teplotni entitu."
                )

        result = {
            "worker": "command_worker",
            "executor": "zigbee_manager",
            "phase": "device_verified",
            "expected_device_type": expected_device_type,
            "infrastructure_mode": infrastructure_context,
            "building_module_id": (
                building_module_id
                if building_context
                else None
            ),
            "pv_surplus_module_id": (
                pv_surplus_module_id
                if energy_target_context
                else None
            ),
            "energy_target_id": (
                energy_target_id
                if energy_target_context
                else None
            ),
            "entity_role": (
                entity_role
                if energy_target_context
                else None
            ),
            "device": device,
            "entities": entities,
            "entity_count": len(entities),
            "temperature_entity": temperature_entity,
            "humidity_entity": (
                humidity_entity
                if isinstance(humidity_entity, dict)
                else None
            ),
            "battery_entity": (
                battery_entity
                if isinstance(battery_entity, dict)
                else None
            ),
            "switch_entity": (
                switch_entity
                if isinstance(switch_entity, dict)
                else None
            ),
            "power_entity": (
                power_entity
                if isinstance(power_entity, dict)
                else None
            ),
            "energy_entity": (
                energy_entity
                if isinstance(energy_entity, dict)
                else None
            ),
            "current_entity": (
                current_entity
                if isinstance(current_entity, dict)
                else None
            ),
            "voltage_entity": (
                voltage_entity
                if isinstance(voltage_entity, dict)
                else None
            ),
            "system_roles": (
                {}
                if infrastructure_context
                else (
                {
                    "building_indoor_temperature": (
                        temperature_entity.get(
                            "entity_id"
                        )
                    ),
                    "building_indoor_humidity": (
                        humidity_entity.get(
                            "entity_id"
                        )
                        if isinstance(
                            humidity_entity,
                            dict,
                        )
                        else None
                    ),
                    "device_battery": (
                        battery_entity.get(
                            "entity_id"
                        )
                        if isinstance(
                            battery_entity,
                            dict,
                        )
                        else None
                    ),
                }
                if building_context
                else (
                    {
                        "water_temperature": (
                            temperature_entity.get(
                                "entity_id"
                            )
                        ),
                        "device_battery": (
                            battery_entity.get(
                                "entity_id"
                            )
                            if isinstance(
                                battery_entity,
                                dict,
                            )
                            else None
                        ),
                    }
                    if entity_role
                    == "water_temperature"
                    else {
                        "output_switch": (
                            switch_entity.get(
                                "entity_id"
                            )
                        ),
                        "output_power": (
                            power_entity.get(
                                "entity_id"
                            )
                            if isinstance(
                                power_entity,
                                dict,
                            )
                            else None
                        ),
                        "output_energy": (
                            energy_entity.get(
                                "entity_id"
                            )
                            if isinstance(
                                energy_entity,
                                dict,
                            )
                            else None
                        ),
                        "output_current": (
                            current_entity.get(
                                "entity_id"
                            )
                            if isinstance(
                                current_entity,
                                dict,
                            )
                            else None
                        ),
                        "output_voltage": (
                            voltage_entity.get(
                                "entity_id"
                            )
                            if isinstance(
                                voltage_entity,
                                dict,
                            )
                            else None
                        ),
                    }
                )
            )
            ),
            "replacement_mode": replacement_mode,
            "replaced_device_id": (
                current_device_id
                if replacement_mode
                else None
            ),
            "discovery_source": discovery_source,
            "service": permit_result.get("service"),
            "duration_seconds": duration_seconds,
        }

        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="succeeded",
            result=result,
            error_message=None,
        )

        if infrastructure_context:
            logging.info(
                (
                    "Nove Zigbee infrastrukturni zarizeni "
                    "bylo naparovano. Device ID: %s, "
                    "pocet entit: %s, prikaz: %s"
                ),
                device.get("device_id"),
                len(entities),
                command_id,
            )
        elif (
            energy_target_context
            and entity_role == "output_switch"
        ):
            logging.info(
                (
                    "Nove Zigbee zarizeni bylo overeno. "
                    "Device ID: %s, switch: %s, "
                    "power: %s, energy: %s, "
                    "current: %s, voltage: %s, "
                    "pocet entit: %s, prikaz: %s"
                ),
                device.get("device_id"),
                switch_entity.get("entity_id"),
                (
                    power_entity.get("entity_id")
                    if isinstance(
                        power_entity,
                        dict,
                    )
                    else "neni dostupna"
                ),
                (
                    energy_entity.get("entity_id")
                    if isinstance(
                        energy_entity,
                        dict,
                    )
                    else "neni dostupna"
                ),
                (
                    current_entity.get("entity_id")
                    if isinstance(
                        current_entity,
                        dict,
                    )
                    else "neni dostupna"
                ),
                (
                    voltage_entity.get("entity_id")
                    if isinstance(
                        voltage_entity,
                        dict,
                    )
                    else "neni dostupna"
                ),
                len(entities),
                command_id,
            )
        else:
            logging.info(
                (
                    "Nove Zigbee zarizeni bylo overeno. "
                    "Device ID: %s, teplota: %s, "
                    "vlhkost: %s, baterie: %s, "
                    "pocet entit: %s, prikaz: %s"
                ),
                device.get("device_id"),
                temperature_entity.get("entity_id"),
                (
                    humidity_entity.get("entity_id")
                    if isinstance(
                        humidity_entity,
                        dict,
                    )
                    else "neni dostupna"
                ),
                (
                    battery_entity.get("entity_id")
                    if isinstance(
                        battery_entity,
                        dict,
                    )
                    else "neni dostupna"
                ),
                len(entities),
                command_id,
            )

    except (
        HomeAssistantApiError,
        ValueError,
    ) as exc:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": "zigbee_manager",
                "phase": "device_discovery_failed",
                "duration_seconds": duration_seconds,
                "expected_device_type": (
                    expected_device_type
                ),
                "building_module_id": (
                    building_module_id
                    if building_context
                    else None
                ),
                "pv_surplus_module_id": (
                    pv_surplus_module_id
                    if energy_target_context
                    else None
                ),
                "energy_target_id": (
                    energy_target_id
                    if energy_target_context
                    else None
                ),
                "entity_role": (
                    entity_role
                    if energy_target_context
                    else None
                ),
            },
            error_message=str(exc),
        )

        logging.error(
            "Zigbee parovani pro prikaz %s selhalo: %s",
            command_id,
            exc,
        )



def execute_photovoltaic_modbus_probe(
    *,
    identity: dict[str, Any],
    command_id: str,
    command_payload: dict[str, Any],
) -> None:
    """Provede profilovy read-only Modbus RTU test stridace."""

    try:
        probe_result = probe_inverter_modbus(
            command_payload
        )

        if not isinstance(
            probe_result,
            dict,
        ):
            raise RuntimeError(
                "Inverter adapter vratil "
                "neplatny probe vysledek."
            )

    except Exception as exc:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": "inverter_adapter",
                "phase": "probe_failed",
                "read_only": True,
                "error_type": (
                    type(exc).__name__
                ),
            },
            error_message=str(exc),
        )

        logging.exception(
            "Inverter Modbus probe "
            "pro prikaz %s selhal.",
            command_id,
        )

        return

    verified = (
        probe_result.get(
            "profile_verified"
        )
        is True
    )

    probe_result[
        "worker"
    ] = "command_worker"

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status=(
            "succeeded"
            if verified
            else "failed"
        ),
        result=probe_result,
        error_message=(
            None
            if verified
            else (
                "Vybrany stridac nebyl "
                "profilove overen."
            )
        ),
    )

    if verified:
        logging.info(
            "Inverter Modbus komunikace "
            "byla potvrzena. "
            "Prikaz=%s profil=%s "
            "slave=%s phase=%s.",
            command_id,
            probe_result.get(
                "profile_id"
            ),
            probe_result.get(
                "matched_device_id"
            ),
            probe_result.get(
                "phase"
            ),
        )

    else:
        logging.warning(
            "Inverter Modbus komunikace "
            "nebyla profilove potvrzena. "
            "Prikaz=%s profil=%s.",
            command_id,
            probe_result.get(
                "profile_id"
            ),
        )


def execute_spot_boiler_intent(
    *,
    identity: dict[str, Any],
    command_id: str,
    command_payload: dict[str, Any],
) -> None:
    """
    Ulozi spotovy pozadavek.

    Zamerne zde nevola Home Assistant service.
    Fyzicky vystup zustava pouze v jednom
    spolecnem actuatoru.
    """

    try:
        stored = save_spot_boiler_intent(
            command_payload
        )

    except (
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": "spot_boiler_intent",
                "phase": "intent_rejected",
                "payload_received": command_payload,
            },
            error_message=str(exc),
        )

        logging.error(
            "Spot boiler intent %s selhal: %s",
            command_id,
            exc,
        )
        return

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status="succeeded",
        result={
            "worker": "command_worker",
            "executor": "spot_boiler_intent",
            "phase": "intent_stored",
            "resource_key": stored[
                "resource_key"
            ],
            "output_reference": stored[
                "output_reference"
            ],
            "action": stored["action"],
            "desired_on": stored["desired_on"],
            "valid_until": stored[
                "valid_until"
            ],
        },
        error_message=None,
    )

    logging.info(
        "Spot boiler intent ulozen. "
        "Prikaz=%s action=%s output=%s valid_until=%s",
        command_id,
        stored["action"],
        stored["output_reference"],
        stored["valid_until"],
    )


def execute_pv_surplus_target_intent(
    *,
    identity: dict[str, Any],
    command_id: str,
    command_payload: dict[str, Any],
) -> None:
    try:
        applied = apply_pv_surplus_target_intent(command_payload)
    except Exception as exc:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": "pv_surplus_target_intent",
                "phase": "target_control_failed",
                "physical_control_active": True,
                "payload_received": command_payload,
            },
            error_message=str(exc),
        )
        logging.exception(
            "PV surplus target intent %s selhal.",
            command_id,
        )
        return

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status="succeeded",
        result={
            "worker": "command_worker",
            "executor": "pv_surplus_target_intent",
            "phase": "target_state_verified",
            "physical_control_active": True,
            "target_id": applied["target_id"],
            "resource_key": applied["resource_key"],
            "output_reference": applied["output_reference"],
            "action": applied["action"],
            "desired_on": applied["desired_on"],
            "reason": applied["reason"],
            "service_called": applied["service_called"],
            "readback_verified": applied["readback_verified"],
            "readback_state": applied["readback_state"],
            "valid_until": applied["valid_until"],
        },
        error_message=None,
    )

    logging.info(
        "PV target control | command=%s target=%s "
        "action=%s output=%s readback=%s",
        command_id,
        applied["target_id"],
        applied["action"],
        applied["output_reference"],
        applied["readback_state"],
    )


def execute_spot_battery_intent(
    *,
    identity: dict[str, Any],
    command_id: str,
    command_payload: dict[str, Any],
) -> None:
    """
    Overi spot battery intent a provede
    fyzicke rizeni baterie pres univerzalni control adapter.

    Povolene akce:
    - auto
    - charge_grid

    Discharge zustava hard-block.
    """

    try:
        stored = save_spot_battery_intent(
            command_payload
        )

        cloud_config = (
            load_cached_cloud_config()
        )

        if not isinstance(
            cloud_config,
            dict,
        ):
            raise RuntimeError(
                "Cloudova konfigurace "
                "neni lokalne dostupna."
            )

        applied = (
            execute_battery_control_from_cloud_config(
                cloud_config=cloud_config,
                action=stored["action"],
                allowed_charge_power_w=stored[
                    "allowed_charge_power_w"
                ],
                target_soc_percent=stored[
                    "target_soc_percent"
                ],
            )
        )

    except Exception as exc:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": "spot_battery_intent",
                "phase": "battery_control_apply_failed",
                "physical_control_active": True,
                "payload_received": (
                    command_payload
                ),
            },
            error_message=str(exc),
        )

        logging.exception(
            "Spot battery control prikaz %s selhal.",
            command_id,
        )
        return

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status="succeeded",
        result={
            "worker": "command_worker",
            "executor": "spot_battery_intent",
            "phase": "battery_control_applied",
            "physical_control_active": True,
            "real_modbus_write": (
                applied.write_performed
            ),
            "verified": applied.verified,
            "action": applied.action,
            "requested_charge_power_w": (
                stored[
                    "requested_charge_power_w"
                ]
            ),
            "allowed_charge_power_w": (
                applied.applied_power_w
            ),
            "target_soc_percent": stored[
                "target_soc_percent"
            ],
            "current_soc_percent": stored[
                "current_soc_percent"
            ],
            "control_capability": (
                applied.capability
            ),
            "valid_until": stored[
                "valid_until"
            ],
        },
        error_message=None,
    )

    logging.info(
        "Spot battery control | "
        "prikaz=%s capability=%s "
        "action=%s applied=%s W "
        "SOC=%s/%s verified=%s "
        "REAL_WRITE=%s",
        command_id,
        applied.capability,
        applied.action,
        applied.applied_power_w,
        stored["current_soc_percent"],
        stored["target_soc_percent"],
        applied.verified,
        applied.write_performed,
    )




AGENT_UPDATE_TIMEOUT_SECONDS = 15 * 60


def reconcile_pending_agent_update(
    *,
    identity: dict[str, Any],
) -> bool:
    """
    Dokonci update az po startu
    noveho Agentu a overeni VERSION.
    """

    marker = load_pending_agent_update()

    if marker is None:
        return False

    command_id = str(
        marker.get("command_id")
        or ""
    ).strip()

    target_version = str(
        marker.get("target_version")
        or ""
    ).strip()

    if not command_id or not target_version:
        raise RuntimeError(
            "Pending update marker "
            "nema command_id nebo target_version."
        )

    installed_version = (
        read_installed_agent_version()
    )

    if installed_version == target_version:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="succeeded",
            result={
                "worker": "command_worker",
                "executor": (
                    "home_assistant_core_"
                    "agent_update"
                ),
                "phase": (
                    "version_confirmed_"
                    "after_restart"
                ),
                "source_version": (
                    marker.get(
                        "source_version"
                    )
                ),
                "target_version": (
                    target_version
                ),
                "installed_version": (
                    installed_version
                ),
                "update_entity_id": (
                    marker.get(
                        "update_entity_id"
                    )
                ),
                "backup": marker.get(
                    "backup"
                ),
            },
            error_message=None,
        )

        clear_pending_agent_update()

        logging.info(
            "Agent update %s potvrzen "
            "po restartu, verze=%s.",
            command_id,
            installed_version,
        )

        return True

    age_seconds = (
        pending_update_age_seconds(
            marker
        )
    )

    if (
        age_seconds is not None
        and age_seconds
        >= AGENT_UPDATE_TIMEOUT_SECONDS
    ):
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": (
                    "home_assistant_core_"
                    "agent_update"
                ),
                "phase": "update_timeout",
                "target_version": (
                    target_version
                ),
                "installed_version": (
                    installed_version
                ),
                "update_entity_id": (
                    marker.get(
                        "update_entity_id"
                    )
                ),
                "trigger_error": (
                    marker.get(
                        "trigger_error"
                    )
                ),
            },
            error_message=(
                "Po aktualizaci nebyla "
                "v casovem limitu potvrzena "
                f"verze {target_version}."
            ),
        )

        clear_pending_agent_update()

        return True

    return True


def execute_agent_update(
    *,
    identity: dict[str, Any],
    command_id: str,
    command_payload: dict[str, Any],
) -> None:
    """Spusti update pres Home Assistant Core."""

    try:
        target_version = str(
            command_payload.get(
                "target_version"
            )
            or ""
        ).strip()

        backup = command_payload.get(
            "backup",
            True,
        )

        if not target_version:
            raise ValueError(
                "Agent update nema target_version."
            )

        if type(backup) is not bool:
            raise ValueError(
                "Pole backup musi byt boolean."
            )

        if not backup:
            raise ValueError(
                "Vzdaleny Agent update "
                "vyzaduje backup=true."
            )

        if (
            load_pending_agent_update()
            is not None
        ):
            raise ValueError(
                "Jiny Agent update jiz probiha."
            )

        validation = (
            validate_update_target(
                target_version
            )
        )

    except Exception as exc:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": (
                    "home_assistant_core_"
                    "agent_update"
                ),
                "phase": (
                    "update_validation_failed"
                ),
                "target_version": (
                    str(
                        command_payload.get(
                            "target_version"
                        )
                        or ""
                    ).strip()
                ),
            },
            error_message=str(exc),
        )

        return

    current_version = str(
        validation[
            "current_version"
        ]
    )

    if validation[
        "already_current"
    ]:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="succeeded",
            result={
                "worker": "command_worker",
                "executor": (
                    "home_assistant_core_"
                    "agent_update"
                ),
                "phase": "already_current",
                "target_version": (
                    target_version
                ),
                "installed_version": (
                    current_version
                ),
            },
            error_message=None,
        )

        return

    entity_id = str(
        validation[
            "update_entity_id"
        ]
    )

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status="running",
        result={
            "worker": "command_worker",
            "executor": (
                "home_assistant_core_"
                "agent_update"
            ),
            "phase": "update_validated",
            "source_version": (
                current_version
            ),
            "target_version": (
                target_version
            ),
            "update_entity_id": (
                entity_id
            ),
            "backup": True,
        },
        error_message=None,
    )

    #
    # Marker MUSI existovat jeste pred
    # volanim update.install.
    #
    save_pending_agent_update(
        {
            "schema_version": 2,
            "command_id": command_id,
            "source_version": (
                current_version
            ),
            "target_version": (
                target_version
            ),
            "backup": True,
            "update_entity_id": (
                entity_id
            ),
            "executor": (
                "home_assistant_core_"
                "agent_update"
            ),
            "requested_at": (
                utc_now_iso()
            ),
            "core_update_request_started_at": (
                utc_now_iso()
            ),
        }
    )

    try:
        trigger_agent_update(
            entity_id=entity_id,
            backup=True,
        )

    except Exception as exc:
        #
        # Toto NENI automaticky failure.
        #
        # Update mohl byt prijat Corem
        # a HTTP spojeni mohlo zmizet
        # prave proto, ze Supervisor
        # zastavil stary Agent.
        #
        try:
            update_pending_agent_update(
                {
                    "trigger_error": str(
                        exc
                    ),
                    "trigger_error_at": (
                        utc_now_iso()
                    ),
                }
            )

        except Exception:
            logging.exception(
                "Nepodarilo se doplnit "
                "trigger_error do markeru."
            )

        try:
            submit_command_result(
                identity=identity,
                command_id=command_id,
                status="running",
                result={
                    "worker": (
                        "command_worker"
                    ),
                    "executor": (
                        "home_assistant_core_"
                        "agent_update"
                    ),
                    "phase": (
                        "core_update_request_"
                        "uncertain"
                    ),
                    "source_version": (
                        current_version
                    ),
                    "target_version": (
                        target_version
                    ),
                    "update_entity_id": (
                        entity_id
                    ),
                    "detail": str(exc),
                },
                error_message=None,
            )

        except Exception:
            logging.exception(
                "Stav uncertain se "
                "nepodarilo odeslat."
            )

        logging.warning(
            "Core update request skoncil "
            "bez potvrzeni. Marker zustava. "
            "command=%s target=%s error=%s",
            command_id,
            target_version,
            exc,
        )

        return

    #
    # Pokud stary Agent jeste zije,
    # Core request se dokazal vratit.
    #
    try:
        update_pending_agent_update(
            {
                "core_update_request_"
                "returned_at": (
                    utc_now_iso()
                ),
            }
        )

    except Exception:
        logging.exception(
            "Core request se vratil, "
            "ale marker se nepodarilo "
            "aktualizovat."
        )

    try:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="running",
            result={
                "worker": "command_worker",
                "executor": (
                    "home_assistant_core_"
                    "agent_update"
                ),
                "phase": (
                    "core_update_service_"
                    "requested"
                ),
                "source_version": (
                    current_version
                ),
                "target_version": (
                    target_version
                ),
                "update_entity_id": (
                    entity_id
                ),
                "backup": True,
            },
            error_message=None,
        )

    except Exception:
        logging.exception(
            "Posledni running stav "
            "se nepodarilo odeslat. "
            "Marker zustava."
        )

    logging.warning(
        "Home Assistant Core update "
        "byl vyzvan. command=%s "
        "source=%s target=%s entity=%s",
        command_id,
        current_version,
        target_version,
        entity_id,
    )



# ==========================================================
# PHASE25_ZIGBEE_TOPOLOGY_REFRESH_0100
# ==========================================================


def execute_zigbee_topology_refresh(
    *,
    identity: dict[str, Any],
    command_id: str,
    command_payload: dict[str, Any],
) -> None:
    """
    Spusti bezpečnou aktualizaci ZHA topologie.

    Nic neparuje, nic nemaze, nemeni sitovy
    klic a nerestartuje ZHA.
    """

    source = str(
        command_payload.get(
            "source",
            "admin_zigbee_infrastructure",
        )
        or "admin_zigbee_infrastructure"
    ).strip()

    try:
        devices_before = get_zha_devices()

        end_devices_before = [
            item
            for item in devices_before
            if (
                isinstance(item, dict)
                and not item.get(
                    "active_coordinator"
                )
            )
        ]

        trigger = (
            trigger_zha_topology_update()
        )

        if (
            not isinstance(trigger, dict)
            or trigger.get(
                "scan_started"
            )
            is not True
        ):
            raise RuntimeError(
                "ZHA topology scan nebyl potvrzen."
            )

    except Exception as exc:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker":
                    "command_worker",
                "executor":
                    "zigbee_topology_refresh",
                "phase":
                    "topology_scan_failed",
                "source":
                    source,
            },
            error_message=str(exc),
        )

        logging.exception(
            "Zigbee topology refresh %s selhal.",
            command_id,
        )

        return

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status="succeeded",
        result={
            "worker":
                "command_worker",
            "executor":
                "zigbee_topology_refresh",
            "phase":
                "topology_scan_started",
            "source":
                source,
            "home_assistant_command":
                "zha/topology/update",
            "scan_started":
                True,
            "device_count_before":
                len(end_devices_before),
        },
        error_message=None,
    )

    logging.info(
        "Zigbee topology scan spusten. "
        "Prikaz=%s zarizeni=%s source=%s",
        command_id,
        len(end_devices_before),
        source,
    )


# ==========================================================
# PHASE25_ZIGBEE_SWITCH_CONTROL
# ==========================================================


def execute_zigbee_switch_set(
    *,
    identity: dict[str, Any],
    command_id: str,
    command_payload: dict[str, Any],
) -> None:
    """
    Provede jeden Zigbee switch povel s read-back kontrolou.

    Prikaz nikdy nevytvari manual override.
    Automaticke rizeni TNG IQ FANDA zustava nadrizene.
    """

    entity_id = str(
        command_payload.get(
            "entity_id",
            "",
        )
        or ""
    ).strip()

    target_state = str(
        command_payload.get(
            "target_state",
            "",
        )
        or ""
    ).strip().lower()

    expected_device_id = str(
        command_payload.get(
            "device_reg_id",
            "",
        )
        or ""
    ).strip()

    ieee = str(
        command_payload.get(
            "ieee",
            "",
        )
        or ""
    ).strip().lower()

    source = str(
        command_payload.get(
            "source",
            "unknown",
        )
        or "unknown"
    ).strip()

    deadline_raw = str(
        command_payload.get(
            "deadline_at",
            "",
        )
        or ""
    ).strip()

    try:
        if not entity_id.startswith(
            "switch."
        ):
            raise ValueError(
                "Zigbee switch control smi "
                "ovladat pouze switch.* entitu."
            )

        if target_state not in {
            "on",
            "off",
        }:
            raise ValueError(
                "target_state musi byt on nebo off."
            )

        if deadline_raw:
            deadline = datetime.fromisoformat(
                deadline_raw.replace(
                    "Z",
                    "+00:00",
                )
            )

            if deadline.tzinfo is None:
                deadline = deadline.replace(
                    tzinfo=timezone.utc,
                )

            if (
                datetime.now(timezone.utc)
                > deadline
            ):
                raise RuntimeError(
                    "Deadline Zigbee spinaciho "
                    "prikazu 180 sekund byl prekrocen."
                )

        actual_device_id = (
            get_entity_device_id(
                entity_id
            )
        )

        if (
            expected_device_id
            and actual_device_id
            != expected_device_id
        ):
            raise RuntimeError(
                "Switch entita nepatri "
                "ocekavanemu Zigbee zarizeni."
            )

        before = get_entity_state(
            entity_id
        )

        before_state = str(
            before.get("state")
            or ""
        ).strip().lower()

        service_called = False

        if before_state != target_state:
            call_home_assistant_service(
                domain="switch",
                service=(
                    "turn_on"
                    if target_state == "on"
                    else "turn_off"
                ),
                payload={
                    "entity_id":
                        entity_id,
                },
            )

            service_called = True

        readback_state = None

        # Maximalne 5 sekund na fyzicke potvrzeni stavu.
        for _ in range(20):
            state = get_entity_state(
                entity_id
            )

            readback_state = str(
                state.get("state")
                or ""
            ).strip().lower()

            if (
                readback_state
                == target_state
            ):
                break

            time.sleep(0.25)

        if (
            readback_state
            != target_state
        ):
            raise RuntimeError(
                "Zigbee switch nebyl potvrzen "
                "read-back kontrolou."
            )

    except Exception as exc:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker":
                    "command_worker",
                "executor":
                    "zigbee_switch_set",
                "phase":
                    "switch_control_failed",
                "entity_id":
                    entity_id,
                "ieee":
                    ieee,
                "target_state":
                    target_state,
                "source":
                    source,
                "manual_override":
                    False,
                "fanda_has_priority":
                    True,
            },
            error_message=str(exc),
        )

        logging.exception(
            "Zigbee switch control %s selhal.",
            command_id,
        )

        return

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status="succeeded",
        result={
            "worker":
                "command_worker",
            "executor":
                "zigbee_switch_set",
            "phase":
                "switch_state_verified",
            "entity_id":
                entity_id,
            "ieee":
                ieee,
            "device_reg_id":
                expected_device_id
                or actual_device_id,
            "source":
                source,
            "requested_state":
                target_state,
            "previous_state":
                before_state,
            "readback_state":
                readback_state,
            "service_called":
                service_called,
            "readback_verified":
                True,
            "manual_override":
                False,
            "fanda_has_priority":
                True,
            "max_execution_delay_seconds":
                180,
            "deadline_at":
                deadline_raw or None,
        },
        error_message=None,
    )

    logging.info(
        "Zigbee switch | command=%s "
        "entity=%s target=%s "
        "source=%s readback=%s",
        command_id,
        entity_id,
        target_state,
        source,
        readback_state,
    )



def execute_command(
    *,
    identity: dict[str, Any],
    command: dict[str, Any],
) -> None:
    """Provede prijaty cloudovy prikaz."""

    command_id = str(command["id"])
    command_type = str(command["command_type"])
    command_payload = command.get("payload") or {}

    if not isinstance(command_payload, dict):
        command_payload = {}

    logging.info(
        "Prikaz prevzat. ID: %s, typ: %s, payload: %s",
        command_id,
        command_type,
        json.dumps(
            command_payload,
            ensure_ascii=False,
            sort_keys=True,
        ),
    )

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status="running",
        result={
            "worker": "command_worker",
            "phase": "validation",
        },
    )

    if command_type == "zigbee_topology_refresh":
        execute_zigbee_topology_refresh(
            identity=identity,
            command_id=command_id,
            command_payload=command_payload,
        )
        return

    if command_type == "zigbee_switch_set":
        execute_zigbee_switch_set(
            identity=identity,
            command_id=command_id,
            command_payload=command_payload,
        )
        return

    if command_type == "zigbee_permit_join":
        execute_zigbee_permit_join(
            identity=identity,
            command_id=command_id,
            command_payload=command_payload,
        )
        return

    if command_type == "spot_boiler_intent":
        execute_spot_boiler_intent(
            identity=identity,
            command_id=command_id,
            command_payload=command_payload,
        )
        return

    if command_type == "pv_surplus_target_intent":
        execute_pv_surplus_target_intent(
            identity=identity,
            command_id=command_id,
            command_payload=command_payload,
        )
        return

    if command_type == "spot_battery_intent":
        execute_spot_battery_intent(
            identity=identity,
            command_id=command_id,
            command_payload=command_payload,
        )
        return

    if command_type == "photovoltaic_modbus_probe":
        execute_photovoltaic_modbus_probe(
            identity=identity,
            command_id=command_id,
            command_payload=command_payload,
        )
        return

    if command_type == "agent_update":
        execute_agent_update(
            identity=identity,
            command_id=command_id,
            command_payload=command_payload,
        )
        return

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status="failed",
        result={
            "worker": "command_worker",
            "phase": "unsupported_command",
        },
        error_message=(
            f"Nepodporovany typ prikazu: {command_type}"
        ),
    )


def process_once() -> bool:
    """Provede jeden pokus o vyzvednuti prikazu."""

    identity = load_device_identity()

    expiry_results = reconcile_expired_pv_surplus_target_intents()

    for item in expiry_results:
        if item.get("action") == "fail_safe_off":
            logging.warning(
                "PV target fail-safe OFF | resource=%s output=%s",
                item.get("resource_key"),
                item.get("output_reference"),
            )

    if reconcile_pending_agent_update(
        identity=identity,
    ):
        return False

    command = claim_command(identity)

    if command is None:
        return False

    execute_command(
        identity=identity,
        command=command,
    )

    return True


def main() -> None:
    """Spusti nepretrzite zpracovani cloudovych prikazu."""

    logging.info(
        "Command Worker TNG IQ FANDA byl spusten."
    )

    while True:
        sleep_seconds = CLAIM_INTERVAL_SECONDS

        try:
            process_once()

        except (
            FileNotFoundError,
            ValueError,
            RuntimeError,
        ) as exc:
            logging.error("%s", exc)
            sleep_seconds = ERROR_RETRY_SECONDS

        except Exception:
            logging.exception(
                "Command Worker skoncil neocekavanou chybou."
            )
            sleep_seconds = ERROR_RETRY_SECONDS

        time.sleep(sleep_seconds)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    main()
