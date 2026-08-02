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