"""Strictly read-only live register survey for Deye 50 kW HV."""

from __future__ import annotations

import time
from typing import Any

from pymodbus.client import ModbusSerialClient

from communication.goodwe_probe import _find_communicator
from communication.modbus_bus_lock import ziskej_zamek_modbus_sbernice


PROFILE_ID = "deye_50_hybrid_hv"
MODEL = "Deye 50 hybrid HV"
DEVICE_ID = 1

SURVEY_BLOCKS = tuple(
    [(0, 23)]
    + [(address, 10) for address in range(500, 700, 10)]
)


def _select_runtime(
    cloud_config: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(cloud_config, dict):
        raise RuntimeError(
            "Cloudova konfigurace neni dostupna."
        )

    runtimes = cloud_config.get(
        "module_runtime_configurations"
    )

    if not isinstance(runtimes, list):
        raise RuntimeError(
            "Cloudova konfigurace nema runtime modulu."
        )

    matches = []

    for runtime in runtimes:
        if not isinstance(runtime, dict):
            continue

        if (
            str(runtime.get("module_key") or "")
            .strip()
            .lower()
            != "photovoltaic"
        ):
            continue

        profile_id = str(
            runtime.get("profile_id")
            or runtime.get("inverter_profile_id")
            or ""
        ).strip()

        manufacturer = str(
            runtime.get("manufacturer")
            or ""
        ).strip().lower()

        model = str(
            runtime.get("model")
            or runtime.get("identified_model")
            or ""
        ).strip()

        if not (
            profile_id == PROFILE_ID
            or (
                manufacturer == "deye"
                and model == MODEL
            )
        ):
            continue

        if (
            str(
                runtime.get("communication_type")
                or ""
            ).strip().lower()
            != "rs485"
        ):
            raise RuntimeError(
                "Deye 50 HV runtime nema RS485 transport."
            )

        matches.append(runtime)

    if len(matches) != 1:
        raise RuntimeError(
            "Deye 50 HV runtime nebyl nalezen jednoznacne."
        )

    runtime = matches[0]

    communicator_id = str(
        runtime.get("communicator_id")
        or ""
    ).strip()

    if not communicator_id:
        raise RuntimeError(
            "Deye 50 HV runtime nema communicator_id."
        )

    device_id = runtime.get(
        "modbus_device_id"
    )

    if device_id is None:
        device_id = runtime.get(
            "matched_device_id"
        )

    if int(device_id or 0) != DEVICE_ID:
        raise RuntimeError(
            "Deye 50 HV runtime nema Modbus ID 1."
        )

    return {
        **runtime,
        "modbus_device_id": DEVICE_ID,
    }


def _read_block(
    *,
    client: Any,
    address: int,
    count: int,
) -> tuple[list[int] | None, str | None]:
    last_error = None

    for attempt in range(2):
        try:
            response = client.read_holding_registers(
                address=address,
                count=count,
                device_id=DEVICE_ID,
            )

            if response is None:
                last_error = "no_response"

            else:
                is_error = getattr(
                    response,
                    "isError",
                    None,
                )

                if callable(is_error) and is_error():
                    last_error = str(response)

                else:
                    registers = getattr(
                        response,
                        "registers",
                        None,
                    )

                    if (
                        isinstance(registers, list)
                        and len(registers) == count
                    ):
                        return [
                            int(value) & 0xFFFF
                            for value in registers
                        ], None

                    last_error = (
                        "invalid_register_count"
                    )

        except Exception as exc:
            last_error = (
                type(exc).__name__
                + ": "
                + str(exc)
            )

        if attempt == 0:
            time.sleep(0.15)

    return None, last_error


def probe_deye_50_hv_register_survey_from_cloud_config(
    *,
    cloud_config: dict[str, Any],
) -> dict[str, Any]:
    runtime = _select_runtime(
        cloud_config
    )

    communicator_id = str(
        runtime["communicator_id"]
    ).strip()

    _communicator, serial_path = (
        _find_communicator(
            communicator_id
        )
    )

    bus_lock = ziskej_zamek_modbus_sbernice(
        serial_path
    )

    client = ModbusSerialClient(
        port=serial_path,
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=1.5,
        retries=1,
    )

    raw: dict[str, dict[str, Any]] = {}
    blocks: list[dict[str, Any]] = []

    with bus_lock:
        try:
            if not client.connect():
                raise RuntimeError(
                    "Deye 50 HV survey nelze otevrit seriovy port."
                )

            for address, count in SURVEY_BLOCKS:
                values, error = _read_block(
                    client=client,
                    address=address,
                    count=count,
                )

                blocks.append(
                    {
                        "address": address,
                        "count": count,
                        "ok": values is not None,
                        "error": error,
                    }
                )

                if values is None:
                    continue

                for offset, value in enumerate(
                    values
                ):
                    register = address + offset

                    signed = (
                        value - 0x10000
                        if value & 0x8000
                        else value
                    )

                    raw[str(register)] = {
                        "u16": value,
                        "s16": signed,
                        "hex": f"0x{value:04X}",
                    }

        finally:
            client.close()

    if not all(
        str(address) in raw
        for address in range(0, 23)
    ):
        raise RuntimeError(
            "Deye 50 HV identity blok 0-22 nebyl cely precten."
        )

    live_success_count = sum(
        1
        for block in blocks
        if (
            block["address"] >= 500
            and block["ok"] is True
        )
    )

    if live_success_count == 0:
        raise RuntimeError(
            "Deye 50 HV live survey 500-699 nevratil zadny blok."
        )

    return {
        "schema_version": 1,
        "read_only": True,
        "write_performed": False,
        "profile_id": PROFILE_ID,
        "manufacturer": "deye",
        "model": MODEL,
        "communication_type": "rs485",
        "modbus_device_id": DEVICE_ID,
        "serial_path": serial_path,
        "survey_range": "0-22,500-699",
        "blocks": blocks,
        "registers": raw,
        "live_success_count": live_success_count,
    }
