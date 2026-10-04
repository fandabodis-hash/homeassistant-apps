"""Read-only identity/runtime adapter for Deye 50 kW HV hybrid inverter."""

from __future__ import annotations

import time
from typing import Any


DEVICE_ID = 1
DEVICE_TYPE_CANDIDATES = {
    0x0006,
    0x0008,
    0x0601,
}
RATED_POWER_MIN_W = 45000.0
RATED_POWER_MAX_W = 55000.0


def _read_block(
    client: Any,
    *,
    device_id: int,
    address: int,
    count: int,
) -> tuple[list[int] | None, str | None]:
    error: str | None = None

    for attempt in range(2):
        try:
            response = client.read_holding_registers(
                address=address,
                count=count,
                device_id=device_id,
            )

            if response is None:
                error = "no_response"
            elif response.isError():
                error = str(response)
            else:
                values = [
                    int(value) & 0xFFFF
                    for value in getattr(
                        response,
                        "registers",
                        [],
                    )
                ]

                if len(values) == count:
                    return values, None

                error = (
                    "invalid_length_"
                    + str(len(values))
                )

        except Exception as exc:
            error = (
                type(exc).__name__
                + ": "
                + str(exc)
            )

        if attempt == 0:
            time.sleep(0.15)

    return None, error


def _u32_low_high(
    low: int,
    high: int,
) -> int:
    return (
        ((int(high) & 0xFFFF) << 16)
        | (int(low) & 0xFFFF)
    )


def _u32_high_low(
    high: int,
    low: int,
) -> int:
    return (
        ((int(high) & 0xFFFF) << 16)
        | (int(low) & 0xFFFF)
    )


def _rated_power_candidates(
    registers: dict[int, int],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for first, second in (
        (16, 17),
        (20, 21),
    ):
        if (
            first not in registers
            or second not in registers
        ):
            continue

        a = int(registers[first]) & 0xFFFF
        b = int(registers[second]) & 0xFFFF

        variants = (
            (
                "low_high",
                _u32_low_high(
                    a,
                    b,
                ),
            ),
            (
                "high_low",
                _u32_high_low(
                    a,
                    b,
                ),
            ),
        )

        for order, raw in variants:
            candidates.append(
                {
                    "registers": [
                        first,
                        second,
                    ],
                    "word_order": order,
                    "raw": raw,
                    "rated_power_w": (
                        raw * 0.1
                    ),
                }
            )

    return candidates


def _decode_serial_hex(
    registers: dict[int, int],
) -> str:
    parts: list[str] = []

    for address in range(
        3,
        8,
    ):
        if address not in registers:
            continue

        parts.append(
            f"{int(registers[address]) & 0xFFFF:04X}"
        )

    return "".join(parts)


def _identity_from_registers(
    registers: dict[int, int],
    *,
    device_id: int,
) -> dict[str, Any]:
    device_type = (
        int(
            registers.get(
                0,
                -1,
            )
        )
        & 0xFFFF
    )

    reported_modbus_id = (
        int(
            registers.get(
                1,
                -1,
            )
        )
        & 0xFFFF
    )

    protocol_version = (
        int(
            registers.get(
                2,
                -1,
            )
        )
        & 0xFFFF
    )

    phase_mppt_raw = (
        int(
            registers.get(
                22,
                0,
            )
        )
        & 0xFFFF
    )

    phases = (
        phase_mppt_raw
        & 0x000F
    )

    mppts = (
        phase_mppt_raw
        >> 8
    ) & 0x000F

    power_candidates = (
        _rated_power_candidates(
            registers
        )
    )

    matching_power = next(
        (
            item
            for item
            in power_candidates
            if (
                RATED_POWER_MIN_W
                <= float(
                    item[
                        "rated_power_w"
                    ]
                )
                <= RATED_POWER_MAX_W
            )
        ),
        None,
    )

    device_family_match = (
        device_type
        in DEVICE_TYPE_CANDIDATES
    )

    modbus_id_match = (
        reported_modbus_id
        == device_id
    )

    three_phase_match = (
        phases == 3
    )

    rated_power_match = (
        matching_power
        is not None
    )

    verified = (
        device_family_match
        and modbus_id_match
        and three_phase_match
        and rated_power_match
    )

    return {
        "verified": verified,
        "device_type_raw":
            device_type,
        "device_type_hex":
            f"0x{device_type:04X}",
        "device_family_match":
            device_family_match,
        "reported_modbus_id":
            reported_modbus_id,
        "expected_modbus_id":
            device_id,
        "modbus_id_match":
            modbus_id_match,
        "protocol_version_raw":
            protocol_version,
        "serial_registers_hex":
            _decode_serial_hex(
                registers
            ),
        "phase_mppt_raw":
            phase_mppt_raw,
        "phases":
            phases,
        "three_phase_match":
            three_phase_match,
        "mppts":
            mppts,
        "rated_power_candidates":
            power_candidates,
        "rated_power_match":
            rated_power_match,
        "matched_rated_power":
            matching_power,
    }


def _entity(
    *,
    key: str,
    name: str,
    value: Any,
    unit: str | None,
    value_type: str,
) -> dict[str, Any]:
    return {
        "entity_key": key,
        "category": "stridac",
        "name": name,
        "value": value,
        "unit": unit,
        "value_type": value_type,
        "quality": "good",
        "source_address": None,
        "attributes": {
            "diagnostic": True,
            "identity_profile": True,
        },
    }


def read_snapshot(
    *,
    runtime_configuration: dict[str, Any],
    profile: dict[str, Any],
    serial_path: str,
    bus_lock: Any,
) -> dict[str, Any]:
    if (
        runtime_configuration.get(
            "read_only"
        )
        is not True
    ):
        raise ValueError(
            "Deye 50 HV runtime neni read-only."
        )

    protocol = profile[
        "protocol"
    ]

    if int(
        protocol.get(
            "function_code",
            -1,
        )
    ) != 3:
        raise ValueError(
            "Deye 50 HV profile musi pouzivat FC03."
        )

    allowed = protocol.get(
        "allowed_device_ids"
    )

    if allowed != [DEVICE_ID]:
        raise ValueError(
            "Deye 50 HV profile musi mit pouze Modbus ID 1."
        )

    from pymodbus.client import (
        ModbusSerialClient,
    )

    client = ModbusSerialClient(
        port=serial_path,
        baudrate=int(
            protocol[
                "baudrate"
            ]
        ),
        bytesize=int(
            protocol[
                "bytesize"
            ]
        ),
        parity=str(
            protocol[
                "parity"
            ]
        ),
        stopbits=int(
            protocol[
                "stopbits"
            ]
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
    error: str | None = None

    bus_lock.acquire()

    try:
        if not client.connect():
            raise RuntimeError(
                "Deye 50 HV RS485 port nelze otevrit."
            )

        values, error = _read_block(
            client,
            device_id=DEVICE_ID,
            address=0,
            count=23,
        )

        if values is not None:
            registers = {
                address: int(value)
                for address, value
                in enumerate(values)
            }

    finally:
        try:
            client.close()
        finally:
            bus_lock.release()

    if not registers:
        raise RuntimeError(
            "Deye 50 HV na Modbus ID 1 neodpovedel: "
            + str(error)
        )

    identity = _identity_from_registers(
        registers,
        device_id=DEVICE_ID,
    )

    matched_power = identity.get(
        "matched_rated_power"
    )

    entities: list[
        dict[str, Any]
    ] = []

    if identity["verified"]:
        if isinstance(
            matched_power,
            dict,
        ):
            entities.append(
                _entity(
                    key=(
                        "stridac."
                        "jmenovity_vykon"
                    ),
                    name=(
                        "Jmenovitý výkon "
                        "střídače"
                    ),
                    value=round(
                        float(
                            matched_power[
                                "rated_power_w"
                            ]
                        ),
                        1,
                    ),
                    unit="W",
                    value_type="number",
                )
            )

        entities.append(
            _entity(
                key="stridac.pocet_fazi",
                name="Počet fází střídače",
                value=int(
                    identity[
                        "phases"
                    ]
                ),
                unit=None,
                value_type="integer",
            )
        )

        entities.append(
            _entity(
                key="stridac.pocet_mppt",
                name="Počet MPPT",
                value=int(
                    identity[
                        "mppts"
                    ]
                ),
                unit=None,
                value_type="integer",
            )
        )

    return {
        "complete":
            bool(
                identity[
                    "verified"
                ]
            ),
        "read_only":
            True,
        "profile_id":
            profile[
                "profile_id"
            ],
        "manufacturer":
            "deye",
        "model":
            "Deye 50 hybrid HV",
        "telemetry_source":
            "deye_50_hybrid_hv_identity_rs485",
        "serial_path":
            serial_path,
        "modbus_device_ids": [
            DEVICE_ID,
        ],
        "grid_connected":
            True,
        "identity":
            identity,
        "inverters": [
            {
                "device_id":
                    DEVICE_ID,
                "online":
                    True,
                "registers":
                    registers,
                "errors":
                    (
                        []
                        if identity[
                            "verified"
                        ]
                        else [
                            (
                                "identity_not_verified:"
                                + str(
                                    identity
                                )
                            )
                        ]
                    ),
            }
        ],
        "entities":
            entities,
    }
