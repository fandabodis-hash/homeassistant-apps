"""Read-only Modbus RTU reader pro FoxESS H3 legacy."""

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

FOXESS_H3_MODBUS_LOCK = threading.Lock()

FOXESS_H3_ALLOWED_DEVICE_IDS = (247,)

FOXESS_H3_IDENTIFICATION_BLOCK = {
    "address": 30000,
    "count": 12,
}

FOXESS_H3_VERSION_BLOCK = {
    "address": 30016,
    "count": 3,
}

FOXESS_H3_RUNTIME_BLOCKS = (
    {"address": 31000, "count": 16},
    {"address": 31016, "count": 10},
    {"address": 31026, "count": 13},
    {"address": 31041, "count": 1},
)


def decode_s16(value: int) -> int:
    """Prevede uint16 na int16."""
    value = int(value) & 0xFFFF

    if value & 0x8000:
        return value - 0x10000

    return value


def decode_model_low_byte(
    registers: list[int],
) -> str:
    """Dekoduje model H3 z dolniho bajtu registru."""
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


def je_podporovany_model(
    model: str,
) -> bool:
    """Povoli pouze legacy H3-10.0."""
    normalized = (
        str(model)
        .strip()
        .lower()
        .replace("_", "-")
        .replace(" ", "")
    )

    if "smart" in normalized or "pro" in normalized:
        return False

    return normalized.startswith(
        "h3-10.0"
    )


def _find_communicator(
    communicator_id: str,
) -> tuple[dict[str, Any], str]:
    """Najde komunikator a stabilni seriovou cestu."""
    state = nacti_json(
        COMMUNICATION_STATE_PATH
    )

    if not isinstance(state, dict):
        raise RuntimeError(
            "Komunikacni stav Agenta neni dostupny."
        )

    communicators = state.get(
        "communicators"
    )

    if not isinstance(communicators, list):
        raise RuntimeError(
            "Komunikacni stav neobsahuje komunikatory."
        )

    matches = [
        item
        for item in communicators
        if (
            isinstance(item, dict)
            and str(
                item.get("communicator_id") or ""
            ).strip()
            == communicator_id
        )
    ]

    if len(matches) != 1:
        raise RuntimeError(
            "Komunikator nebyl jednoznacne nalezen."
        )

    communicator = matches[0]

    if communicator.get("connected") is not True:
        raise RuntimeError(
            "Komunikator neni pripojen."
        )

    path = str(
        communicator.get("preferred_path") or ""
    ).strip()

    stable_paths = communicator.get(
        "stable_paths"
    )

    if not isinstance(stable_paths, list):
        stable_paths = []

    if (
        not path.startswith("/dev/serial/by-id/")
        or path not in stable_paths
    ):
        raise RuntimeError(
            "Komunikator nema stabilni seriovou cestu."
        )

    if not Path(path).exists():
        raise RuntimeError(
            "Stabilni seriova cesta neexistuje."
        )

    return communicator, path


def _read_block(
    *,
    client: ModbusSerialClient,
    device_id: int,
    address: int,
    count: int,
) -> list[int]:
    """Nacte jeden pevny FC03 blok."""
    response = client.read_holding_registers(
        address=address,
        count=count,
        device_id=device_id,
    )

    if response is None:
        raise RuntimeError(
            f"FoxESS registr {address}/{count} "
            "nevratil odpoved."
        )

    if response.isError():
        raise RuntimeError(
            f"FoxESS registr {address}/{count} "
            "vratil Modbus chybu."
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
            f"FoxESS registr {address}/{count} vratil "
            f"{len(registers)} registru."
        )

    return registers


def vytvor_snapshot_z_registru(
    *,
    device_id: int,
    model_registers: list[int],
    version_registers: list[int],
    registers: dict[int, int],
) -> dict[str, Any]:
    """Dekoduje fyzicky overenou H3 mapu."""
    required = set(range(31000, 31039))
    required.add(31041)

    missing = sorted(
        required - set(registers)
    )

    if missing:
        raise ValueError(
            f"FoxESS snapshot nema registry: {missing}"
        )

    if len(version_registers) != 3:
        raise ValueError(
            "FoxESS version blok nema tri registry."
        )

    model = decode_model_low_byte(
        model_registers
    )

    if not je_podporovany_model(model):
        raise ValueError(
            f"Nepodporovany FoxESS model: {model!r}"
        )

    def u16(address: int) -> int:
        return int(registers[address]) & 0xFFFF

    def s16(address: int) -> int:
        return decode_s16(registers[address])

    inverter_l1 = s16(31012)
    inverter_l2 = s16(31013)
    inverter_l3 = s16(31014)

    eps_l1 = s16(31022)
    eps_l2 = s16(31023)
    eps_l3 = s16(31024)

    load_l1 = s16(31029)
    load_l2 = s16(31030)
    load_l3 = s16(31031)

    return {
        "read_only": True,
        "complete": True,
        "manufacturer": "foxess",
        "model": model,
        "device_id": device_id,
        "versions": {
            "master_raw": int(version_registers[0]),
            "slave_raw": int(version_registers[1]),
            "manager_raw": int(version_registers[2]),
        },
        "pv1": {
            "voltage_v": u16(31000) * 0.1,
            "current_a": s16(31001) * 0.1,
            "power_w": s16(31002),
        },
        "pv2": {
            "voltage_v": u16(31003) * 0.1,
            "current_a": s16(31004) * 0.1,
            "power_w": s16(31005),
        },
        "grid": {
            "voltage_l1_v": u16(31006) * 0.1,
            "voltage_l2_v": u16(31007) * 0.1,
            "voltage_l3_v": u16(31008) * 0.1,
            "frequency_hz": u16(31015) * 0.01,
        },
        "inverter": {
            "current_l1_a": s16(31009) * 0.1,
            "current_l2_a": s16(31010) * 0.1,
            "current_l3_a": s16(31011) * 0.1,
            "power_l1_raw_w": inverter_l1,
            "power_l2_raw_w": inverter_l2,
            "power_l3_raw_w": inverter_l3,
            "total_power_raw_w": (
                inverter_l1
                + inverter_l2
                + inverter_l3
            ),
            "temperature_c": s16(31032) * 0.1,
            "ambient_temperature_c": s16(31033) * 0.1,
            "state_code": u16(31041),
        },
        "backup": {
            "voltage_l1_v": u16(31016) * 0.1,
            "voltage_l2_v": u16(31017) * 0.1,
            "voltage_l3_v": u16(31018) * 0.1,
            "current_l1_a": s16(31019) * 0.1,
            "current_l2_a": s16(31020) * 0.1,
            "current_l3_a": s16(31021) * 0.1,
            "power_l1_w": eps_l1,
            "power_l2_w": eps_l2,
            "power_l3_w": eps_l3,
            "total_power_w": (
                eps_l1
                + eps_l2
                + eps_l3
            ),
            "frequency_hz": u16(31025) * 0.01,
        },
        "meter": {
            "physically_connected": True,
            "direction_verified": False,
            "power_l1_raw_w": s16(31026),
            "power_l2_raw_w": s16(31027),
            "power_l3_raw_w": s16(31028),
        },
        "load": {
            "power_l1_w": load_l1,
            "power_l2_w": load_l2,
            "power_l3_w": load_l3,
            "total_power_w": (
                load_l1
                + load_l2
                + load_l3
            ),
        },
        "battery": {
            "voltage_v": s16(31034) * 0.1,
            "current_a": s16(31035) * 0.1,
            "power_w": s16(31036),
            "temperature_c": s16(31037) * 0.1,
            "soc_percent": u16(31038),
        },
        "registers": {
            str(address): int(value) & 0xFFFF
            for address, value
            in sorted(registers.items())
        },
    }


def read_foxess_h3_snapshot(
    runtime_configuration: dict[str, Any],
) -> dict[str, Any]:
    """Nacte provozni read-only H3 snapshot."""
    if not isinstance(runtime_configuration, dict):
        raise ValueError(
            "Runtime konfigurace FoxESS nema platny format."
        )

    module_key = str(
        runtime_configuration.get("module_key") or ""
    ).strip().lower()

    manufacturer = str(
        runtime_configuration.get("manufacturer") or ""
    ).strip().lower()

    communication_type = str(
        runtime_configuration.get("communication_type") or ""
    ).strip().lower()

    communicator_id = str(
        runtime_configuration.get("communicator_id") or ""
    ).strip()

    device_id = runtime_configuration.get(
        "modbus_device_id"
    )

    if module_key != "photovoltaic":
        raise ValueError(
            "FoxESS reader podporuje pouze photovoltaic."
        )

    if manufacturer != "foxess":
        raise ValueError(
            "FoxESS reader podporuje pouze FoxESS."
        )

    if communication_type != "rs485":
        raise ValueError(
            "FoxESS H3 reader vyzaduje RS485."
        )

    if not communicator_id:
        raise ValueError(
            "FoxESS runtime nema communicator_id."
        )

    if (
        type(device_id) is not int
        or device_id not in FOXESS_H3_ALLOWED_DEVICE_IDS
    ):
        raise ValueError(
            "FoxESS runtime obsahuje nepovolenou Modbus adresu."
        )

    if runtime_configuration.get(
        "telemetry_enabled"
    ) is not True:
        raise ValueError(
            "FoxESS telemetrie neni povolena."
        )

    if runtime_configuration.get(
        "read_only"
    ) is not True:
        raise ValueError(
            "FoxESS runtime neni pouze cteci."
        )

    with FOXESS_H3_MODBUS_LOCK:
        _communicator, path = _find_communicator(
            communicator_id
        )

        client = ModbusSerialClient(
            port=path,
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
                    "Seriovy port FoxESS H3 nelze otevrit."
                )

            model_registers = _read_block(
                client=client,
                device_id=device_id,
                **FOXESS_H3_IDENTIFICATION_BLOCK,
            )

            version_registers = _read_block(
                client=client,
                device_id=device_id,
                **FOXESS_H3_VERSION_BLOCK,
            )

            registers: dict[int, int] = {}

            for block in FOXESS_H3_RUNTIME_BLOCKS:
                values = _read_block(
                    client=client,
                    device_id=device_id,
                    **block,
                )

                for offset, value in enumerate(values):
                    registers[
                        block["address"] + offset
                    ] = value

        finally:
            client.close()

    return vytvor_snapshot_z_registru(
        device_id=device_id,
        model_registers=model_registers,
        version_registers=version_registers,
        registers=registers,
    )