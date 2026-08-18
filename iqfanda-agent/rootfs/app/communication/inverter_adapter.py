"""Univerzalni read-only adapter Modbus profilu stridacu."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any


PROFILE_DIRECTORY = (
    Path(__file__).resolve().parent
    / "inverter_profiles"
)

CATALOG_PATH = (
    PROFILE_DIRECTORY
    / "catalog.json"
)

COMMUNICATION_STATE_PATH = Path(
    os.getenv(
        "IQF_COMMUNICATION_STATE_PATH",
        "/config/communication.json",
    )
)


def _normalize_text(
    value: object,
) -> str:
    return str(
        value or ""
    ).strip().lower()


def _read_json(
    path: Path,
) -> dict[str, Any]:
    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(
            f"JSON {path} nema platny objekt."
        )

    return data


def load_inverter_catalog() -> dict[str, Any]:
    """Nacte katalog dostupnych profilu stridacu."""
    catalog = _read_json(
        CATALOG_PATH
    )

    if catalog.get("schema_version") != 1:
        raise ValueError(
            "Nepodporovana verze katalogu stridacu."
        )

    profiles = catalog.get(
        "profiles"
    )

    if not isinstance(profiles, list):
        raise ValueError(
            "Katalog stridacu nema seznam profilu."
        )

    return catalog


def list_inverter_profiles() -> list[dict[str, Any]]:
    """Vrati profily pro instalacni rucni vyber."""
    catalog = load_inverter_catalog()

    result: list[dict[str, Any]] = []

    for item in catalog["profiles"]:
        if not isinstance(item, dict):
            continue

        result.append(
            dict(item)
        )

    return result


def load_inverter_profile(
    profile_id: str,
) -> dict[str, Any]:
    """Nacte jeden profil podle stabilniho profile_id."""
    normalized_id = str(
        profile_id or ""
    ).strip()

    if not normalized_id:
        raise ValueError(
            "profile_id nesmi byt prazdne."
        )

    catalog = load_inverter_catalog()

    matches = [
        item
        for item in catalog["profiles"]
        if (
            isinstance(item, dict)
            and str(
                item.get("profile_id") or ""
            ).strip()
            == normalized_id
        )
    ]

    if len(matches) != 1:
        raise ValueError(
            f"Profil {normalized_id!r} "
            "nebyl jednoznacne nalezen."
        )

    filename = str(
        matches[0].get("file") or ""
    ).strip()

    if not filename:
        raise ValueError(
            "Profil nema definovany soubor."
        )

    if (
        "/" in filename
        or "\\" in filename
        or filename.startswith(".")
    ):
        raise ValueError(
            "Profil obsahuje nepovolenou cestu."
        )

    profile = _read_json(
        PROFILE_DIRECTORY
        / filename
    )

    validate_inverter_profile(
        profile
    )

    if (
        str(
            profile.get("profile_id")
            or ""
        ).strip()
        != normalized_id
    ):
        raise ValueError(
            "profile_id katalogu a profilu se neshoduje."
        )

    return profile


def select_inverter_profile(
    *,
    manufacturer: str,
    model: str,
) -> dict[str, Any]:
    """Vybere profil podle rucne zvoleneho vyrobce a modelu."""
    normalized_manufacturer = _normalize_text(
        manufacturer
    )

    normalized_model = _normalize_text(
        model
    )

    if not normalized_manufacturer:
        raise ValueError(
            "Vyrobce stridace nesmi byt prazdny."
        )

    if not normalized_model:
        raise ValueError(
            "Model stridace nesmi byt prazdny."
        )

    matches: list[dict[str, Any]] = []

    for item in list_inverter_profiles():
        item_manufacturer = _normalize_text(
            item.get("manufacturer")
        )

        models = item.get(
            "models"
        )

        if not isinstance(models, list):
            continue

        normalized_models = {
            _normalize_text(value)
            for value in models
        }

        if (
            item_manufacturer
            == normalized_manufacturer
            and normalized_model
            in normalized_models
        ):
            matches.append(
                item
            )

    if len(matches) != 1:
        raise ValueError(
            "Pro vybrany vyrobce/model nebyl "
            "nalezen jednoznacny Modbus profil."
        )

    return load_inverter_profile(
        str(
            matches[0]["profile_id"]
        )
    )


def validate_inverter_profile(
    profile: dict[str, Any],
) -> None:
    """Overi zakladni bezpecnostni kontrakt profilu."""
    if profile.get("schema_version") != 1:
        raise ValueError(
            "Nepodporovana verze profilu."
        )

    for field in (
        "profile_id",
        "manufacturer",
        "models",
        "protocol",
        "read_blocks",
        "entities",
    ):
        if field not in profile:
            raise ValueError(
                f"Profil nema pole {field}."
            )

    models = profile.get(
        "models"
    )

    if (
        not isinstance(models, list)
        or not models
    ):
        raise ValueError(
            "Profil nema modely."
        )

    protocol = profile.get(
        "protocol"
    )

    if not isinstance(protocol, dict):
        raise ValueError(
            "Profil nema platny protokol."
        )

    if (
        _normalize_text(
            protocol.get("type")
        )
        != "modbus_rtu"
    ):
        raise ValueError(
            "Adapter zatim podporuje pouze Modbus RTU."
        )

    function_code = protocol.get(
        "function_code"
    )

    if function_code not in {
        3,
        4,
    }:
        raise ValueError(
            "Profil obsahuje nepovoleny Modbus function code."
        )

    allowed_device_ids = protocol.get(
        "allowed_device_ids"
    )

    if (
        not isinstance(
            allowed_device_ids,
            list,
        )
        or not allowed_device_ids
    ):
        raise ValueError(
            "Profil nema povolene Modbus adresy."
        )

    for device_id in allowed_device_ids:
        if (
            type(device_id) is not int
            or device_id < 1
            or device_id > 247
        ):
            raise ValueError(
                "Profil ma neplatnou Modbus adresu."
            )

    blocks = profile.get(
        "read_blocks"
    )

    if not isinstance(blocks, list):
        raise ValueError(
            "Profil nema read_blocks."
        )

    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError(
                "Read block nema platny format."
            )

        address = block.get(
            "address"
        )

        count = block.get(
            "count"
        )

        if (
            type(address) is not int
            or address < 0
        ):
            raise ValueError(
                "Read block ma neplatnou adresu."
            )

        if (
            type(count) is not int
            or count < 1
            or count > 125
        ):
            raise ValueError(
                "Read block ma neplatnou delku."
            )

    entities = profile.get(
        "entities"
    )

    if not isinstance(entities, list):
        raise ValueError(
            "Profil nema entity."
        )

    entity_keys: list[str] = []

    for entity in entities:
        if not isinstance(entity, dict):
            raise ValueError(
                "Definice entity nema platny format."
            )

        key = str(
            entity.get("entity_key") or ""
        ).strip()

        if not key:
            raise ValueError(
                "Entita nema entity_key."
            )

        entity_keys.append(
            key
        )

    if len(entity_keys) != len(
        set(entity_keys)
    ):
        raise ValueError(
            "Profil obsahuje duplicitni entity_key."
        )


def decode_s16(
    value: int,
) -> int:
    value = int(value) & 0xFFFF

    if value & 0x8000:
        return value - 0x10000

    return value


def decode_u32(
    *,
    first_word: int,
    second_word: int,
    word_order: str,
) -> int:
    first = int(first_word) & 0xFFFF
    second = int(second_word) & 0xFFFF

    if word_order == "big":
        high = first
        low = second

    elif word_order == "little":
        high = second
        low = first

    else:
        raise ValueError(
            "Neznamy word_order."
        )

    return (
        (high << 16)
        | low
    )


def decode_s32(
    *,
    first_word: int,
    second_word: int,
    word_order: str,
) -> int:
    value = decode_u32(
        first_word=first_word,
        second_word=second_word,
        word_order=word_order,
    )

    if value & 0x80000000:
        return value - 0x100000000

    return value


def _definition_register_addresses(
    definition: dict[str, Any],
) -> list[int]:
    """Vrati skutecne raw registry definice entity."""
    configured = definition.get(
        "addresses"
    )

    if configured is None:
        address = definition.get(
            "address"
        )

        if type(address) is not int:
            raise ValueError(
                "Entita nema platnou adresu."
            )

        return [
            address
        ]

    if (
        not isinstance(
            configured,
            list,
        )
        or not configured
        or len(configured) > 2
    ):
        raise ValueError(
            "Entita nema platny seznam addresses."
        )

    addresses: list[int] = []

    for value in configured:
        if (
            type(value) is not int
            or value < 0
        ):
            raise ValueError(
                "Entita obsahuje neplatnou raw adresu."
            )

        addresses.append(
            value
        )

    primary_address = definition.get(
        "address"
    )

    if (
        primary_address is not None
        and primary_address
        != addresses[0]
    ):
        raise ValueError(
            "address neodpovida prvni raw adrese."
        )

    return addresses


def decode_register_value(
    *,
    registers: dict[int, int],
    definition: dict[str, Any],
) -> int | float:
    """Dekoduje jednu hodnotu podle profilu."""
    raw_addresses = (
        _definition_register_addresses(
            definition
        )
    )

    register_type = str(
        definition.get(
            "register_type"
        )
        or ""
    ).strip().lower()

    if register_type in {
        "uint16",
        "int16",
    }:
        if len(raw_addresses) != 1:
            raise ValueError(
                "16bit entita musi mit jeden raw registr."
            )

    elif register_type in {
        "uint32",
        "int32",
    }:
        if len(raw_addresses) != 2:
            raise ValueError(
                "32bit entita musi mit dva raw registry."
            )

    else:
        raise ValueError(
            f"Nepodporovany register_type: "
            f"{register_type}"
        )

    for address in raw_addresses:
        if address not in registers:
            raise KeyError(
                f"Chybi registr {address}."
            )

    first_address = raw_addresses[0]

    raw = int(
        registers[
            first_address
        ]
    ) & 0xFFFF

    if register_type == "uint16":
        value: int | float = raw

    elif register_type == "int16":
        value = decode_s16(
            raw
        )

    else:
        second_address = (
            raw_addresses[1]
        )

        word_order = str(
            definition.get(
                "word_order"
            )
            or "big"
        ).strip().lower()

        if register_type == "uint32":
            value = decode_u32(
                first_word=raw,
                second_word=registers[
                    second_address
                ],
                word_order=word_order,
            )

        else:
            value = decode_s32(
                first_word=raw,
                second_word=registers[
                    second_address
                ],
                word_order=word_order,
            )

    has_scale = (
        "scale"
        in definition
    )

    has_divisor = (
        "divisor"
        in definition
    )

    if (
        has_scale
        and has_divisor
    ):
        raise ValueError(
            "Entita nesmi obsahovat soucasne "
            "scale a divisor."
        )

    scale = definition.get(
        "scale",
        1,
    )

    divisor = definition.get(
        "divisor"
    )

    offset = definition.get(
        "offset",
        0,
    )

    if type(scale) not in {
        int,
        float,
    }:
        raise ValueError(
            "Scale nema platny format."
        )

    if (
        has_divisor
        and (
            type(divisor)
            not in {
                int,
                float,
            }
            or not math.isfinite(
                float(divisor)
            )
            or float(divisor) == 0.0
        )
    ):
        raise ValueError(
            "Divisor nema platny format."
        )

    if type(offset) not in {
        int,
        float,
    }:
        raise ValueError(
            "Offset nema platny format."
        )

    if has_divisor:
        value = (
            value
            / divisor
        )

    else:
        value = (
            value
            * scale
        )

    value = (
        value
        + offset
    )

    value_type = str(
        definition.get(
            "value_type"
        )
        or "number"
    ).strip().lower()

    if value_type == "integer":
        if not float(value).is_integer():
            raise ValueError(
                "Integer entita po scale neni cele cislo."
            )

        return int(
            value
        )

    if value_type == "number":
        number = float(
            value
        )

        if not math.isfinite(
            number
        ):
            raise ValueError(
                "Entita nema konecnou hodnotu."
            )

        return number

    raise ValueError(
        "Nepodporovany value_type."
    )


def decode_profile_entities(
    *,
    profile: dict[str, Any],
    registers: dict[int, int],
) -> list[dict[str, Any]]:
    """Prevede registry profilu na obecne IQ FANDA entity."""
    validate_inverter_profile(
        profile
    )

    definitions = profile[
        "entities"
    ]

    result: list[dict[str, Any]] = []
    values_by_key: dict[str, int | float] = {}

    #
    # Nejprve prime registry.
    #
    for definition in definitions:
        if (
            definition.get("enabled")
            is False
        ):
            continue

        if "derived" in definition:
            continue

        key = str(
            definition[
                "entity_key"
            ]
        )

        value = decode_register_value(
            registers=registers,
            definition=definition,
        )

        values_by_key[
            key
        ] = value

        profile_attributes = definition.get(
            "attributes"
        )

        attributes = (
            dict(profile_attributes)
            if isinstance(
                profile_attributes,
                dict,
            )
            else {}
        )

        for attribute_name in (
            "device_class",
            "state_class",
            "sign_convention",
            "verification_status",
            "value_map",
        ):
            if attribute_name in definition:
                attributes[
                    attribute_name
                ] = definition[
                    attribute_name
                ]

        result.append({
            "entity_key": key,
            "category": definition.get(
                "category"
            ),
            "name": definition.get(
                "name"
            ),
            "value": value,
            "unit": definition.get(
                "unit"
            ),
            "value_type": definition.get(
                "value_type",
                "number",
            ),
            "quality": "good",
            "source_address": (
                definition[
                    "source_address"
                ]
                if "source_address"
                in definition
                else definition.get(
                    "address"
                )
            ),
            "attributes": attributes,
        })

    #
    # Potom odvozene entity.
    #
    for definition in definitions:
        if (
            definition.get("enabled")
            is False
        ):
            continue

        derived = definition.get(
            "derived"
        )

        if not isinstance(
            derived,
            dict,
        ):
            continue

        operation = str(
            derived.get(
                "operation"
            )
            or ""
        ).strip().lower()

        source_keys = derived.get(
            "entity_keys"
        )

        if (
            operation != "sum"
            or not isinstance(
                source_keys,
                list,
            )
            or not source_keys
        ):
            raise ValueError(
                "Nepodporovana odvozena entita."
            )

        source_values = []

        for source_key in source_keys:
            normalized_key = str(
                source_key or ""
            ).strip()

            if normalized_key not in values_by_key:
                raise ValueError(
                    "Odvozena entita odkazuje "
                    "na chybejici zdroj."
                )

            source_values.append(
                values_by_key[
                    normalized_key
                ]
            )

        value: int | float = sum(
            source_values
        )

        value_type = str(
            definition.get(
                "value_type"
            )
            or "number"
        ).strip().lower()

        if value_type == "integer":
            value = int(
                value
            )

        elif value_type == "number":
            value = float(
                value
            )

        else:
            raise ValueError(
                "Odvozena entita ma neplatny value_type."
            )

        key = str(
            definition[
                "entity_key"
            ]
        )

        values_by_key[
            key
        ] = value

        attributes = {
            "derived_from":
                list(source_keys)
        }

        for attribute_name in (
            "device_class",
            "state_class",
            "sign_convention",
            "verification_status",
        ):
            if attribute_name in definition:
                attributes[
                    attribute_name
                ] = definition[
                    attribute_name
                ]

        result.append({
            "entity_key": key,
            "category": definition.get(
                "category"
            ),
            "name": definition.get(
                "name"
            ),
            "value": value,
            "unit": definition.get(
                "unit"
            ),
            "value_type": value_type,
            "quality": "good",
            "source_address": None,
            "attributes": attributes,
        })

    keys = [
        entity["entity_key"]
        for entity in result
    ]

    if len(keys) != len(
        set(keys)
    ):
        raise RuntimeError(
            "Adapter vytvoril duplicitni entity."
        )

    return result


def decode_ascii_low_byte(
    registers: list[int],
) -> str:
    raw = bytes(
        int(value) & 0xFF
        for value in registers
    )

    return (
        raw.decode(
            "ascii",
            errors="ignore",
        )
        .replace("\x00", "")
        .strip()
    )


def _load_communication_state() -> dict[str, Any]:
    return _read_json(
        COMMUNICATION_STATE_PATH
    )


def _find_communicator_path(
    communicator_id: str,
) -> str:
    state = _load_communication_state()

    communicators = state.get(
        "communicators"
    )

    if not isinstance(
        communicators,
        list,
    ):
        raise RuntimeError(
            "Komunikacni stav nema komunikatory."
        )

    matches = [
        item
        for item in communicators
        if (
            isinstance(item, dict)
            and str(
                item.get(
                    "communicator_id"
                )
                or ""
            ).strip()
            == communicator_id
        )
    ]

    if len(matches) != 1:
        raise RuntimeError(
            "Komunikator nebyl jednoznacne nalezen."
        )

    communicator = matches[0]

    if (
        communicator.get(
            "connected"
        )
        is not True
    ):
        raise RuntimeError(
            "Komunikator neni pripojen."
        )

    path = str(
        communicator.get(
            "preferred_path"
        )
        or ""
    ).strip()

    stable_paths = communicator.get(
        "stable_paths"
    )

    if not isinstance(
        stable_paths,
        list,
    ):
        stable_paths = []

    if (
        not path.startswith(
            "/dev/serial/by-id/"
        )
        or path not in stable_paths
    ):
        raise RuntimeError(
            "Komunikator nema stabilni seriovou cestu."
        )

    if not Path(path).exists():
        raise RuntimeError(
            "Stabilni seriova cesta neexistuje."
        )

    return path


def read_inverter_snapshot(
    runtime_configuration: dict[str, Any],
) -> dict[str, Any]:
    """
    Nacte jeden read-only Modbus snapshot podle profilu.

    Zadna zapisova Modbus funkce zde neni povolena.
    """
    if not isinstance(
        runtime_configuration,
        dict,
    ):
        raise ValueError(
            "Runtime konfigurace nema platny format."
        )

    if (
        runtime_configuration.get(
            "read_only"
        )
        is not True
    ):
        raise ValueError(
            "Runtime konfigurace neni read-only."
        )

    manufacturer = str(
        runtime_configuration.get(
            "manufacturer"
        )
        or ""
    ).strip()

    model = str(
        runtime_configuration.get(
            "model"
        )
        or ""
    ).strip()

    profile = select_inverter_profile(
        manufacturer=manufacturer,
        model=model,
    )

    protocol = profile[
        "protocol"
    ]

    if (
        _normalize_text(
            runtime_configuration.get(
                "communication_type"
            )
        )
        != "rs485"
    ):
        raise ValueError(
            "Profil vyzaduje RS485."
        )

    communicator_id = str(
        runtime_configuration.get(
            "communicator_id"
        )
        or ""
    ).strip()

    if not communicator_id:
        raise ValueError(
            "Runtime nema communicator_id."
        )

    device_id = runtime_configuration.get(
        "modbus_device_id"
    )

    if (
        type(device_id) is not int
        or device_id
        not in protocol[
            "allowed_device_ids"
        ]
    ):
        raise ValueError(
            "Runtime obsahuje nepovolenou Modbus adresu."
        )

    serial_path = _find_communicator_path(
        communicator_id
    )

    #
    # Lazy import:
    # Windows fixture test nepotrebuje pymodbus.
    #
    from pymodbus.client import (
        ModbusSerialClient,
    )

    client = ModbusSerialClient(
        port=serial_path,
        baudrate=int(
            protocol["baudrate"]
        ),
        bytesize=int(
            protocol["bytesize"]
        ),
        parity=str(
            protocol["parity"]
        ),
        stopbits=int(
            protocol["stopbits"]
        ),
        timeout=float(
            protocol[
                "timeout_seconds"
            ]
        ),
        retries=int(
            protocol.get(
                "retries",
                0,
            )
        ),
    )

    registers: dict[int, int] = {}

    try:
        if not client.connect():
            raise RuntimeError(
                "Seriovy port stridace nelze otevrit."
            )

        function_code = int(
            protocol[
                "function_code"
            ]
        )

        for block in profile[
            "read_blocks"
        ]:
            address = int(
                block["address"]
            )

            count = int(
                block["count"]
            )

            if function_code == 3:
                response = (
                    client.read_holding_registers(
                        address=address,
                        count=count,
                        device_id=device_id,
                    )
                )

            elif function_code == 4:
                response = (
                    client.read_input_registers(
                        address=address,
                        count=count,
                        device_id=device_id,
                    )
                )

            else:
                raise RuntimeError(
                    "Nepovoleny function code."
                )

            if response is None:
                raise RuntimeError(
                    f"Registr {address}/{count} "
                    "nevratil odpoved."
                )

            if response.isError():
                raise RuntimeError(
                    f"Registr {address}/{count} "
                    "vratil Modbus chybu."
                )

            values = [
                int(value)
                for value in getattr(
                    response,
                    "registers",
                    [],
                )
            ]

            if len(values) != count:
                raise RuntimeError(
                    f"Registr {address}/{count} "
                    "vratil neplatny pocet hodnot."
                )

            for offset, value in enumerate(
                values
            ):
                registers[
                    address + offset
                ] = value

    finally:
        client.close()

    entities = decode_profile_entities(
        profile=profile,
        registers=registers,
    )

    return {
        "complete": True,
        "read_only": True,
        "profile_id": profile[
            "profile_id"
        ],
        "manufacturer": profile[
            "manufacturer"
        ],
        "model": model,
        "modbus_device_id": device_id,
        "serial_path": serial_path,
        "entities": entities,
        "registers": {
            str(address): value
            for address, value
            in sorted(
                registers.items()
            )
        },
    }