"""Bezpecny cteci test menice GoodWe pres Modbus RTU."""

from __future__ import annotations

import os
import threading
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

GOODWE_MODBUS_LOCK = threading.Lock()


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
    {
        "name": "inverter_data",
        "address": 35121,
        "count": 68,
    },
    {
        "name": "energy_data",
        "address": 35189,
        "count": 23,
    },
    {
        "name": "meter_data",
        "address": 36000,
        "count": 45,
    },
    {
        "name": "bms_summary",
        "address": 37000,
        "count": 24,
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


def _decode_s16(
    value: int,
) -> int:
    """Prevede 16bitove slovo na znamenkove cele cislo."""

    normalized = int(value) & 0xFFFF

    if normalized & 0x8000:
        return normalized - 0x10000

    return normalized


def _decode_s32(
    registers: list[int],
    index: int,
) -> int:
    """Slozi dve slova na znamenkove 32bitove cislo."""

    value = _decode_u32(
        registers,
        index,
    )

    if value & 0x80000000:
        return value - 0x100000000

    return value


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
        "inverter_data": {},
        "energy_data": {},
        "meter_data": {},
        "bms_summary": {},
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

    for pv_name in ("pv1", "pv2"):
        pv_data = snapshot["operating_data"].get(
            pv_name
        )

        if isinstance(pv_data, dict):
            power_raw = pv_data.get(
                "power_raw_u32"
            )

            if isinstance(power_raw, int):
                pv_data["power_w"] = power_raw

    inverter_registers = successful_blocks.get(
        "inverter_data"
    )

    if (
        isinstance(inverter_registers, list)
        and len(inverter_registers) == 68
    ):
        snapshot["inverter_data"] = {
            "register_address": 35121,
            "raw_registers": inverter_registers,
            "grid": {
                "r": {
                    "voltage_v": round(
                        inverter_registers[0] / 10,
                        1,
                    ),
                    "current_a": round(
                        inverter_registers[1] / 10,
                        1,
                    ),
                    "frequency_hz": round(
                        inverter_registers[2] / 100,
                        2,
                    ),
                    "power_w": _decode_s16(
                        inverter_registers[4]
                    ),
                },
                "s": {
                    "voltage_v": round(
                        inverter_registers[5] / 10,
                        1,
                    ),
                    "current_a": round(
                        inverter_registers[6] / 10,
                        1,
                    ),
                    "frequency_hz": round(
                        inverter_registers[7] / 100,
                        2,
                    ),
                    "power_w": _decode_s16(
                        inverter_registers[9]
                    ),
                },
                "t": {
                    "voltage_v": round(
                        inverter_registers[10] / 10,
                        1,
                    ),
                    "current_a": round(
                        inverter_registers[11] / 10,
                        1,
                    ),
                    "frequency_hz": round(
                        inverter_registers[12] / 100,
                        2,
                    ),
                    "power_w": _decode_s16(
                        inverter_registers[14]
                    ),
                },
                "mode_raw": inverter_registers[15],
            },
            "inverter": {
                "total_power_w": _decode_s16(
                    inverter_registers[17]
                ),
                "active_power_w": _decode_s16(
                    inverter_registers[19]
                ),
                "reactive_power_var": _decode_s16(
                    inverter_registers[21]
                ),
                "apparent_power_va": _decode_s16(
                    inverter_registers[23]
                ),
            },
            "backup": {
                "r": {
                    "voltage_v": round(
                        inverter_registers[24] / 10,
                        1,
                    ),
                    "current_a": round(
                        inverter_registers[25] / 10,
                        1,
                    ),
                    "frequency_hz": round(
                        inverter_registers[26] / 100,
                        2,
                    ),
                    "mode_raw": inverter_registers[27],
                    "power_w": _decode_s16(
                        inverter_registers[29]
                    ),
                },
                "s": {
                    "voltage_v": round(
                        inverter_registers[30] / 10,
                        1,
                    ),
                    "current_a": round(
                        inverter_registers[31] / 10,
                        1,
                    ),
                    "frequency_hz": round(
                        inverter_registers[32] / 100,
                        2,
                    ),
                    "mode_raw": inverter_registers[33],
                    "power_w": _decode_s16(
                        inverter_registers[35]
                    ),
                },
                "t": {
                    "voltage_v": round(
                        inverter_registers[36] / 10,
                        1,
                    ),
                    "current_a": round(
                        inverter_registers[37] / 10,
                        1,
                    ),
                    "frequency_hz": round(
                        inverter_registers[38] / 100,
                        2,
                    ),
                    "mode_raw": inverter_registers[39],
                    "power_w": _decode_s16(
                        inverter_registers[41]
                    ),
                },
                "total_power_w": _decode_s16(
                    inverter_registers[49]
                ),
            },
            "load": {
                "r_power_w": _decode_s16(
                    inverter_registers[43]
                ),
                "s_power_w": _decode_s16(
                    inverter_registers[45]
                ),
                "t_power_w": _decode_s16(
                    inverter_registers[47]
                ),
                "total_power_w": _decode_s16(
                    inverter_registers[51]
                ),
                "backup_load_percent": round(
                    inverter_registers[52] / 100,
                    2,
                ),
            },
            "temperatures": {
                "air_c": round(
                    _decode_s16(
                        inverter_registers[53]
                    ) / 10,
                    1,
                ),
                "module_c": round(
                    _decode_s16(
                        inverter_registers[54]
                    ) / 10,
                    1,
                ),
                "radiator_c": round(
                    _decode_s16(
                        inverter_registers[55]
                    ) / 10,
                    1,
                ),
            },
            "dc_bus": {
                "positive_v": round(
                    inverter_registers[57] / 10,
                    1,
                ),
                "negative_v": round(
                    inverter_registers[58] / 10,
                    1,
                ),
            },
            "battery": {
                "voltage_v": round(
                    inverter_registers[59] / 10,
                    1,
                ),
                "current_a": round(
                    _decode_s16(
                        inverter_registers[60]
                    ) / 10,
                    1,
                ),
                "power_w": _decode_s16(
                    inverter_registers[62]
                ),
                "mode_raw": inverter_registers[63],
            },
            "warning_code_raw": (
                inverter_registers[64]
            ),
            "safety_country_raw": (
                inverter_registers[65]
            ),
            "work_mode_raw": (
                inverter_registers[66]
            ),
            "operation_mode_raw": (
                inverter_registers[67]
            ),
        }

    energy_registers = successful_blocks.get(
        "energy_data"
    )

    if (
        isinstance(energy_registers, list)
        and len(energy_registers) == 23
    ):
        snapshot["energy_data"] = {
            "register_address": 35189,
            "raw_registers": energy_registers,
            "error_message_raw": _decode_u32(
                energy_registers,
                0,
            ),
            "pv_total_kwh": round(
                _decode_u32(
                    energy_registers,
                    2,
                ) / 10,
                1,
            ),
            "pv_today_kwh": round(
                _decode_u32(
                    energy_registers,
                    4,
                ) / 10,
                1,
            ),
            "grid_sell_total_kwh": round(
                _decode_u32(
                    energy_registers,
                    6,
                ) / 10,
                1,
            ),
            "grid_feed_hours": _decode_u32(
                energy_registers,
                8,
            ),
            "grid_sell_today_kwh": round(
                energy_registers[10] / 10,
                1,
            ),
            "grid_buy_total_kwh": round(
                _decode_u32(
                    energy_registers,
                    11,
                ) / 10,
                1,
            ),
            "grid_buy_today_kwh": round(
                energy_registers[13] / 10,
                1,
            ),
            "load_total_kwh": round(
                _decode_u32(
                    energy_registers,
                    14,
                ) / 10,
                1,
            ),
            "load_today_kwh": round(
                energy_registers[16] / 10,
                1,
            ),
            "battery_charge_total_kwh": round(
                _decode_u32(
                    energy_registers,
                    17,
                ) / 10,
                1,
            ),
            "battery_charge_today_kwh": round(
                energy_registers[19] / 10,
                1,
            ),
            "battery_discharge_total_kwh": round(
                _decode_u32(
                    energy_registers,
                    20,
                ) / 10,
                1,
            ),
            "battery_discharge_today_kwh": round(
                energy_registers[22] / 10,
                1,
            ),
        }

    meter_registers = successful_blocks.get(
        "meter_data"
    )

    if (
        isinstance(meter_registers, list)
        and len(meter_registers) == 45
    ):
        snapshot["meter_data"] = {
            "register_address": 36000,
            "raw_registers": meter_registers,
            "connection_status_raw": (
                meter_registers[3]
            ),
            "communication_status_raw": (
                meter_registers[4]
            ),
            "frequency_hz": round(
                meter_registers[14] / 100,
                2,
            ),
            "power_factor": {
                "r": round(
                    meter_registers[10] / 100,
                    2,
                ),
                "s": round(
                    meter_registers[11] / 100,
                    2,
                ),
                "t": round(
                    meter_registers[12] / 100,
                    2,
                ),
                "total": round(
                    meter_registers[13] / 100,
                    2,
                ),
            },
            "active_power_w": {
                "r": _decode_s32(
                    meter_registers,
                    19,
                ),
                "s": _decode_s32(
                    meter_registers,
                    21,
                ),
                "t": _decode_s32(
                    meter_registers,
                    23,
                ),
                "total": _decode_s32(
                    meter_registers,
                    25,
                ),
            },
            "energy_total_sell_float_raw": (
                meter_registers[15:17]
            ),
            "energy_total_buy_float_raw": (
                meter_registers[17:19]
            ),
            "meter_type_raw": meter_registers[43],
            "software_version_raw": (
                meter_registers[44]
            ),
        }

    bms_registers = successful_blocks.get(
        "bms_summary"
    )

    if (
        isinstance(bms_registers, list)
        and len(bms_registers) == 24
    ):
        snapshot["bms_summary"] = {
            "register_address": 37000,
            "raw_registers": bms_registers,
            "drm_status_raw": bms_registers[0],
            "battery_type_index_raw": (
                bms_registers[1]
            ),
            "status_raw": bms_registers[2],
            "pack_temperature_c": round(
                bms_registers[3] / 10,
                1,
            ),
            "charge_current_limit_a": (
                bms_registers[4]
            ),
            "discharge_current_limit_a": (
                bms_registers[5]
            ),
            "error_code_raw": (
                (bms_registers[12] << 16)
                | bms_registers[6]
            ),
            "soc_percent": bms_registers[7],
            "soh_percent": bms_registers[8],
            "battery_strings": bms_registers[9],
            "warning_code_raw": (
                (bms_registers[13] << 16)
                | bms_registers[10]
            ),
            "battery_protocol_raw": (
                bms_registers[11]
            ),
            "software_version_raw": (
                bms_registers[14]
            ),
            "hardware_version_raw": (
                bms_registers[15]
            ),
            "maximum_cell_temperature_id": (
                bms_registers[16]
            ),
            "minimum_cell_temperature_id": (
                bms_registers[17]
            ),
            "maximum_cell_voltage_id": (
                bms_registers[18]
            ),
            "minimum_cell_voltage_id": (
                bms_registers[19]
            ),
            "maximum_cell_temperature_c": round(
                bms_registers[20] / 10,
                1,
            ),
            "minimum_cell_temperature_c": round(
                bms_registers[21] / 10,
                1,
            ),
            "maximum_cell_voltage_mv": (
                bms_registers[22]
            ),
            "minimum_cell_voltage_mv": (
                bms_registers[23]
            ),
        }

    snapshot["complete"] = (
        len(successful_blocks)
        == len(ET_SNAPSHOT_BLOCKS)
    )

    return snapshot


def _probe_goodwe_modbus_unlocked(
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


def read_goodwe_control_snapshot(
    runtime_configuration: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Nacte minimalni read-only GoodWe data pro rychle rizeni.

    Pouziva pouze pevne registry potrebne pro Decision Engine:
    - PV1 a PV2 vykon,
    - celkovy vykon smartmetru,
    - SOC baterie.
    """
    if not isinstance(runtime_configuration, dict):
        raise ValueError(
            "Runtime konfigurace GoodWe nema platny format."
        )

    module_key = str(
        runtime_configuration.get("module_key") or ""
    ).strip().lower()

    manufacturer = str(
        runtime_configuration.get("manufacturer") or ""
    ).strip().lower()

    inverter_model = str(
        runtime_configuration.get("model") or ""
    ).strip()

    communication_type = str(
        runtime_configuration.get(
            "communication_type"
        )
        or ""
    ).strip().lower()

    communicator_id = str(
        runtime_configuration.get("communicator_id") or ""
    ).strip()

    device_id = runtime_configuration.get(
        "modbus_device_id"
    )

    if module_key != "photovoltaic":
        raise ValueError(
            "Control reader podporuje pouze modul photovoltaic."
        )

    if manufacturer != "goodwe":
        raise ValueError(
            "Control reader podporuje pouze GoodWe."
        )

    if not inverter_model:
        raise ValueError(
            "Runtime konfigurace neobsahuje model menice."
        )

    if communication_type != "rs485":
        raise ValueError(
            "GoodWe control reader vyzaduje komunikaci RS485."
        )

    if not communicator_id:
        raise ValueError(
            "Runtime konfigurace neobsahuje communicator_id."
        )

    if runtime_configuration.get(
        "telemetry_enabled"
    ) is not True:
        raise ValueError(
            "Telemetrie modulu neni povolena."
        )

    if runtime_configuration.get("read_only") is not True:
        raise ValueError(
            "Runtime konfigurace neni pouze cteci."
        )

    if (
        type(device_id) is not int
        or device_id not in ALLOWED_DEVICE_IDS
    ):
        raise ValueError(
            "Runtime konfigurace obsahuje nepovolenou "
            "Modbus adresu."
        )

    blocks = (
        ("pv_power", 35105, 6),
        ("grid_power", 36025, 2),
        ("battery_soc", 37007, 1),
    )

    registers_by_name: dict[str, list[int]] = {}

    with GOODWE_MODBUS_LOCK:
        _communicator, serial_path = (
            _find_communicator(
                communicator_id
            )
        )

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
                raise RuntimeError(
                    "Seriovy port GoodWe nelze otevrit."
                )

            for name, address, count in blocks:
                try:
                    response = client.read_holding_registers(
                        address=address,
                        count=count,
                        device_id=device_id,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        "GoodWe control cteni registru "
                        f"{address}/{count} selhalo: {exc}"
                    ) from exc

                if response is None:
                    raise RuntimeError(
                        "GoodWe control cteni registru "
                        f"{address}/{count} nevratilo odpoved."
                    )

                if response.isError():
                    raise RuntimeError(
                        "GoodWe control cteni registru "
                        f"{address}/{count} vratilo Modbus chybu."
                    )

                registers = [
                    int(value)
                    for value in getattr(
                        response,
                        "registers",
                        [],
                    )
                ]

                if len(registers) != count:
                    raise RuntimeError(
                        "GoodWe control cteni registru "
                        f"{address}/{count} vratilo "
                        f"{len(registers)} registru."
                    )

                registers_by_name[name] = registers
        finally:
            client.close()

    pv_registers = registers_by_name["pv_power"]
    grid_registers = registers_by_name["grid_power"]
    soc_registers = registers_by_name["battery_soc"]

    pv1_power_w = _decode_u32(
        pv_registers,
        0,
    )

    pv2_power_w = _decode_u32(
        pv_registers,
        4,
    )

    grid_power_w = _decode_s32(
        grid_registers,
        0,
    )

    soc_percent = int(
        soc_registers[0]
    )

    return [
        {
            "entity_key": "baterie.soc",
            "value": soc_percent,
            "unit": "%",
            "quality": "good",
        },
        {
            "entity_key": "pv1.vykon",
            "value": pv1_power_w,
            "unit": "W",
            "quality": "good",
        },
        {
            "entity_key": "pv2.vykon",
            "value": pv2_power_w,
            "unit": "W",
            "quality": "good",
        },
        {
            "entity_key": "smartmeter.vykon_celkem",
            "value": grid_power_w,
            "unit": "W",
            "quality": "good",
        },
    ]


def read_goodwe_et_snapshot(
    runtime_configuration: dict[str, Any],
) -> dict[str, Any]:
    """
    Nacte provozni GoodWe ET snapshot podle cloudove konfigurace.

    Pouziva pouze pevne read-only registry a overenou Modbus adresu.
    """
    if not isinstance(runtime_configuration, dict):
        raise ValueError(
            "Runtime konfigurace GoodWe nema platny format."
        )

    module_key = str(
        runtime_configuration.get("module_key") or ""
    ).strip().lower()

    manufacturer = str(
        runtime_configuration.get("manufacturer") or ""
    ).strip().lower()

    inverter_model = str(
        runtime_configuration.get("model") or ""
    ).strip()

    communication_type = str(
        runtime_configuration.get(
            "communication_type"
        )
        or ""
    ).strip().lower()

    communicator_id = str(
        runtime_configuration.get("communicator_id") or ""
    ).strip()

    device_id = runtime_configuration.get(
        "modbus_device_id"
    )

    if module_key != "photovoltaic":
        raise ValueError(
            "Runtime reader podporuje pouze modul photovoltaic."
        )

    if manufacturer != "goodwe":
        raise ValueError(
            "Runtime reader podporuje pouze GoodWe."
        )

    if not inverter_model:
        raise ValueError(
            "Runtime konfigurace neobsahuje model menice."
        )

    if communication_type != "rs485":
        raise ValueError(
            "GoodWe runtime vyzaduje komunikaci RS485."
        )

    if not communicator_id:
        raise ValueError(
            "Runtime konfigurace neobsahuje communicator_id."
        )

    if runtime_configuration.get(
        "telemetry_enabled"
    ) is not True:
        raise ValueError(
            "Telemetrie modulu neni povolena."
        )

    if runtime_configuration.get("read_only") is not True:
        raise ValueError(
            "Runtime konfigurace neni pouze cteci."
        )

    if (
        type(device_id) is not int
        or device_id not in ALLOWED_DEVICE_IDS
    ):
        raise ValueError(
            "Runtime konfigurace obsahuje nepovolenou "
            "Modbus adresu."
        )

    with GOODWE_MODBUS_LOCK:
        _communicator, serial_path = (
            _find_communicator(
                communicator_id
            )
        )

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
                raise RuntimeError(
                    "Seriovy port GoodWe nelze otevrit."
                )

            snapshot = _read_et_snapshot(
                client=client,
                device_id=device_id,
            )
        finally:
            client.close()

    identification = snapshot.get(
        "identification"
    )

    if not isinstance(identification, dict):
        raise RuntimeError(
            "GoodWe snapshot neobsahuje identifikaci."
        )

    detected_model = str(
        identification.get("device_type") or ""
    ).strip()

    if (
        detected_model
        and detected_model != inverter_model
    ):
        raise RuntimeError(
            "Detekovany model GoodWe neodpovida "
            "runtime konfiguraci."
        )

    return snapshot


def probe_goodwe_modbus(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Provede instalacni GoodWe probe pod spolecnym zamkem.

    Zachovava puvodni verejny kontrakt command workeru.
    """
    with GOODWE_MODBUS_LOCK:
        return _probe_goodwe_modbus_unlocked(
            payload
        )
