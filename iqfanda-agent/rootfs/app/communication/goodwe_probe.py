"""Bezpecny cteci test menice GoodWe pres Modbus RTU."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pymodbus.client import ModbusSerialClient

from communication.json_utils import nacti_json


COMMUNICATION_STATE_PATH = Path(
    os.getenv(
        "IQF_COMMUNICATION_STATE_PATH",
        "/config/communication.json",
    )
)

ALLOWED_DEVICE_IDS = (
    247,
    1,
)

PROBE_BLOCKS = (
    {
        "name": "et_operating_data",
        "address": 35100,
        "count": 1,
    },
    {
        "name": "legacy_identification",
        "address": 0x0200,
        "count": 1,
    },
)


ET_SNAPSHOT_BLOCKS = (
    {
        "name": "protocol_version",
        "address": 35000,
        "count": 1,
    },
    {
        "name": "serial_number",
        "address": 35003,
        "count": 8,
    },
    {
        "name": "device_type",
        "address": 35011,
        "count": 5,
    },
    {
        "name": "operating_data",
        "address": 35100,
        "count": 11,
    },
)


def _required_text(
    payload: dict[str, Any],
    field_name: str,
) -> str:
    """Nacte a overi povinny textovy parametr."""

    value = str(
        payload.get(field_name) or ""
    ).strip()

    if not value:
        raise ValueError(
            f"V prikazu chybi pole: {field_name}"
        )

    return value


def _find_communicator(
    communicator_id: str,
) -> tuple[dict[str, Any], str]:
    """Najde vybrany seriovy komunikator a jeho stabilni cestu."""

    state = nacti_json(
        COMMUNICATION_STATE_PATH
    )

    if not isinstance(state, dict):
        raise RuntimeError(
            "Komunikacni stav Agenta neni dostupny."
        )

    communicators = state.get("communicators")

    if not isinstance(communicators, list):
        raise RuntimeError(
            "Komunikacni stav neobsahuje komunikatory."
        )

    matches = [
        communicator
        for communicator in communicators
        if (
            isinstance(communicator, dict)
            and str(
                communicator.get("communicator_id")
                or ""
            ).strip()
            == communicator_id
        )
    ]

    if len(matches) != 1:
        raise RuntimeError(
            "Vybrany komunikator nebyl jednoznacne nalezen."
        )

    communicator = matches[0]

    if not bool(communicator.get("connected")):
        raise RuntimeError(
            "Vybrany komunikator neni pripojen."
        )

    capabilities = communicator.get(
        "capabilities"
    )

    if not isinstance(capabilities, list):
        capabilities = []

    normalized_capabilities = {
        str(capability).strip().lower()
        for capability in capabilities
    }

    if "serial" not in normalized_capabilities:
        raise RuntimeError(
            "Vybrany komunikator neni seriove zarizeni."
        )

    preferred_path = str(
        communicator.get("preferred_path")
        or ""
    ).strip()

    stable_paths = communicator.get(
        "stable_paths"
    )

    if not isinstance(stable_paths, list):
        stable_paths = []

    if (
        not preferred_path.startswith(
            "/dev/serial/by-id/"
        )
        or preferred_path not in stable_paths
    ):
        raise RuntimeError(
            "Komunikator nema platnou stabilni seriovou cestu."
        )

    if not Path(preferred_path).exists():
        raise RuntimeError(
            "Stabilni seriova cesta komunikatoru neexistuje."
        )

    return communicator, preferred_path


def _decode_ascii_registers(
    registers: list[int],
) -> str:
    """Prevede dvojice bajtu registru na bezpecny ASCII text."""

    raw = bytearray()

    for register in registers:
        value = int(register) & 0xFFFF
        raw.append((value >> 8) & 0xFF)
        raw.append(value & 0xFF)

    return (
        bytes(raw)
        .decode("ascii", errors="ignore")
        .replace("\x00", "")
        .strip()
    )


def _decode_u32(
    registers: list[int],
    index: int,
) -> int:
    """Slozi dve po sobe jdouci 16bitova slova."""

    high_word = int(registers[index]) & 0xFFFF
    low_word = int(registers[index + 1]) & 0xFFFF

    return (high_word << 16) | low_word


def _read_et_snapshot(
    *,
    client: ModbusSerialClient,
    device_id: int,
) -> dict[str, Any]:
    """
    Nacte omezeny diagnosticky snimek GoodWe ET.

    Pouziva vyhradne funkci 03H a pevne read-only registry.
    """

    snapshot: dict[str, Any] = {
        "read_only": True,
        "device_id": device_id,
        "complete": False,
        "blocks": [],
        "identification": {},
        "operating_data": {},
    }

    for block in ET_SNAPSHOT_BLOCKS:
        block_result: dict[str, Any] = {
            "name": block["name"],
            "address": block["address"],
            "count": block["count"],
        }

        try:
            response = client.read_holding_registers(
                address=block["address"],
                count=block["count"],
                device_id=device_id,
            )
        except Exception as exc:
            block_result.update({
                "outcome": "client_exception",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            snapshot["blocks"].append(block_result)
            continue

        if response is None:
            block_result["outcome"] = "no_response"
            snapshot["blocks"].append(block_result)
            continue

        response_type = type(response).__name__

        if response.isError():
            block_result.update({
                "outcome": "modbus_error",
                "response_type": response_type,
            })

            exception_code = getattr(
                response,
                "exception_code",
                None,
            )

            if exception_code is not None:
                block_result["exception_code"] = int(
                    exception_code
                )

            snapshot["blocks"].append(block_result)
            continue

        registers = [
            int(value)
            for value in getattr(
                response,
                "registers",
                [],
            )
        ]

        if len(registers) != block["count"]:
            block_result.update({
                "outcome": "invalid_register_count",
                "response_type": response_type,
                "registers": registers,
            })
            snapshot["blocks"].append(block_result)
            continue

        block_result.update({
            "outcome": "register_response",
            "response_type": response_type,
            "registers": registers,
        })
        snapshot["blocks"].append(block_result)

    successful_blocks = {
        block["name"]: block["registers"]
        for block in snapshot["blocks"]
        if block.get("outcome") == "register_response"
    }

    protocol_registers = successful_blocks.get(
        "protocol_version"
    )

    if protocol_registers:
        snapshot["identification"][
            "protocol_version_raw"
        ] = protocol_registers[0]

    serial_registers = successful_blocks.get(
        "serial_number"
    )

    if serial_registers:
        snapshot["identification"]["serial_number"] = (
            _decode_ascii_registers(serial_registers)
        )

    type_registers = successful_blocks.get(
        "device_type"
    )

    if type_registers:
        snapshot["identification"]["device_type"] = (
            _decode_ascii_registers(type_registers)
        )

    operating_registers = successful_blocks.get(
        "operating_data"
    )

    if (
        isinstance(operating_registers, list)
        and len(operating_registers) == 11
    ):
        snapshot["operating_data"] = {
            "register_address": 35100,
            "raw_registers": operating_registers,
            "rtc": {
                "year": (
                    2000
                    + (
                        operating_registers[0]
                        >> 8
                    )
                ),
                "month": (
                    operating_registers[0]
                    & 0xFF
                ),
                "day": (
                    operating_registers[1]
                    >> 8
                ),
                "hour": (
                    operating_registers[1]
                    & 0xFF
                ),
                "minute": (
                    operating_registers[2]
                    >> 8
                ),
                "second": (
                    operating_registers[2]
                    & 0xFF
                ),
            },
            "pv1": {
                "voltage_raw": operating_registers[3],
                "voltage_v": round(
                    operating_registers[3] / 10,
                    1,
                ),
                "current_raw": operating_registers[4],
                "current_a": round(
                    operating_registers[4] / 10,
                    1,
                ),
                "power_raw_u32": _decode_u32(
                    operating_registers,
                    5,
                ),
            },
            "pv2": {
                "voltage_raw": operating_registers[7],
                "voltage_v": round(
                    operating_registers[7] / 10,
                    1,
                ),
                "current_raw": operating_registers[8],
                "current_a": round(
                    operating_registers[8] / 10,
                    1,
                ),
                "power_raw_u32": _decode_u32(
                    operating_registers,
                    9,
                ),
            },
        }

    snapshot["complete"] = (
        len(successful_blocks)
        == len(ET_SNAPSHOT_BLOCKS)
    )

    return snapshot


def probe_goodwe_modbus(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Provede pouze cteci Modbus RTU test.

    Funkce nepouziva zadnou Modbus zapisovou operaci.
    Adresy zarizeni ani registru nejsou ovladatelne payloadem.
    """

    if not isinstance(payload, dict):
        raise ValueError(
            "Payload Modbus testu nema platny format."
        )

    manufacturer = _required_text(
        payload,
        "manufacturer",
    ).lower()

    if manufacturer != "goodwe":
        raise ValueError(
            "Tento test je povolen pouze pro GoodWe."
        )

    communication_type = _required_text(
        payload,
        "communication_type",
    ).lower()

    if communication_type != "rs485":
        raise ValueError(
            "GoodWe test vyzaduje komunikaci RS485."
        )

    inverter_model = _required_text(
        payload,
        "inverter_model",
    )

    communicator_id = _required_text(
        payload,
        "communicator_id",
    )

    communicator, serial_path = (
        _find_communicator(
            communicator_id
        )
    )

    result: dict[str, Any] = {
        "executor": "goodwe_probe",
        "phase": "probe_started",
        "read_only": True,
        "manufacturer": "goodwe",
        "inverter_model": inverter_model,
        "communicator_id": communicator_id,
        "communicator_product": (
            communicator.get("product")
        ),
        "serial_path": serial_path,
        "serial_parameters": {
            "baudrate": 9600,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "timeout_seconds": 1.0,
        },
        "device_ids_tested": list(
            ALLOWED_DEVICE_IDS
        ),
        "communication_detected": False,
        "register_readable": False,
        "matched_device_id": None,
        "attempts": [],
    }

    first_exception_result: dict[str, Any] | None = None

    client = ModbusSerialClient(
        port=serial_path,
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=1.0,
        retries=0,
    )

    try:
        if not client.connect():
            result["phase"] = "serial_open_failed"
            return result

        for device_id in ALLOWED_DEVICE_IDS:
            for block in PROBE_BLOCKS:
                attempt: dict[str, Any] = {
                    "device_id": device_id,
                    "block": block["name"],
                    "address": block["address"],
                    "count": block["count"],
                }

                try:
                    response = (
                        client.read_holding_registers(
                            address=block["address"],
                            count=block["count"],
                            device_id=device_id,
                        )
                    )
                except Exception as exc:
                    attempt.update({
                        "outcome": "client_exception",
                        "error_type": (
                            type(exc).__name__
                        ),
                        "error": str(exc),
                    })
                    result["attempts"].append(
                        attempt
                    )
                    continue

                if response is None:
                    attempt["outcome"] = (
                        "no_response"
                    )
                    result["attempts"].append(
                        attempt
                    )
                    continue

                response_type = type(
                    response
                ).__name__

                if not response.isError():
                    registers = [
                        int(value)
                        for value in getattr(
                            response,
                            "registers",
                            [],
                        )
                    ]

                    attempt.update({
                        "outcome": "register_response",
                        "response_type": response_type,
                        "registers": registers,
                    })
                    result["attempts"].append(
                        attempt
                    )

                    result.update({
                        "phase": "register_response",
                        "communication_detected": True,
                        "register_readable": True,
                        "matched_device_id": device_id,
                        "matched_block": block["name"],
                        "raw_registers": registers,
                    })

                    if block["name"] == "et_operating_data":
                        result["et_snapshot"] = (
                            _read_et_snapshot(
                                client=client,
                                device_id=device_id,
                            )
                        )

                    return result

                exception_code = getattr(
                    response,
                    "exception_code",
                    None,
                )

                if exception_code is not None:
                    attempt.update({
                        "outcome": "modbus_exception_response",
                        "response_type": response_type,
                        "exception_code": int(
                            exception_code
                        ),
                    })
                    result["attempts"].append(
                        attempt
                    )

                    if first_exception_result is None:
                        first_exception_result = {
                            "phase": (
                                "modbus_exception_response"
                            ),
                            "communication_detected": True,
                            "register_readable": False,
                            "matched_device_id": device_id,
                            "matched_block": block["name"],
                            "exception_code": int(
                                exception_code
                            ),
                        }

                    continue

                attempt.update({
                    "outcome": "modbus_io_error",
                    "response_type": response_type,
                    "error": str(response),
                })
                result["attempts"].append(
                    attempt
                )

        if first_exception_result is not None:
            result.update(first_exception_result)
            return result

        result["phase"] = "no_modbus_response"
        return result

    finally:
        client.close()