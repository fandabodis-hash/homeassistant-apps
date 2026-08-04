"""Cloudovy vykonavatel prikazu TNG IQ FANDA Agentu."""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from communication.goodwe_probe import probe_goodwe_modbus
from host.cloud_client import cloud_client
from zigbee_manager import (
    HomeAssistantApiError,
    find_existing_temperature_devices,
    get_home_assistant_entity_ids,
    open_zigbee_permit,
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
    a overi jeho teplotni entitu.
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

    if replacement_mode and not current_device_id:
        raise ValueError(
            "V režimu výměny musí být předáno "
            "current_device_id původního zařízení."
        )

    try:
        all_existing_devices = (
            find_existing_temperature_devices()
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
            "building_module_id": building_module_id,
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
            "system_roles": {
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
            },
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
                "building_module_id": building_module_id,
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
    """Provede bezpecny pouze cteci test GoodWe Modbus RTU."""

    try:
        probe_result = probe_goodwe_modbus(
            command_payload
        )

        if not isinstance(probe_result, dict):
            raise RuntimeError(
                "GoodWe probe vratila neplatny format."
            )

    except Exception as exc:
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": "goodwe_probe",
                "phase": "probe_failed",
                "read_only": True,
                "error_type": type(exc).__name__,
            },
            error_message=str(exc),
        )

        logging.exception(
            "GoodWe Modbus probe pro prikaz %s selhala.",
            command_id,
        )
        return

    communication_detected = bool(
        probe_result.get(
            "communication_detected"
        )
    )

    probe_result["worker"] = "command_worker"

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status=(
            "succeeded"
            if communication_detected
            else "failed"
        ),
        result=probe_result,
        error_message=(
            None
            if communication_detected
            else (
                "Menic GoodWe neodpovedel na povolene "
                "cteci Modbus RTU dotazy."
            )
        ),
    )

    if communication_detected:
        logging.info(
            "GoodWe Modbus komunikace byla potvrzena. "
            "Prikaz: %s, adresa menice: %s, faze: %s.",
            command_id,
            probe_result.get("matched_device_id"),
            probe_result.get("phase"),
        )
    else:
        logging.warning(
            "GoodWe Modbus komunikace nebyla potvrzena. "
            "Prikaz: %s.",
            command_id,
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

    if command_type == "zigbee_permit_join":
        execute_zigbee_permit_join(
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
