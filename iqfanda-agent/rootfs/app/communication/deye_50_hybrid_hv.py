"""Read-only live telemetry adapter for Deye 50 kW HV hybrid inverter."""

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

# Verified on F800711-TNG-00015 on 2026-10-05:
# FC03, 9600 8N1, Modbus ID 1.
#
# Live register semantics below follow the Deye SG01HP3 family map
# and are constrained to values that were physically readable on 00015.
LIVE_BLOCKS = (
    (580, 10),
    (590, 10),
    (600, 10),
    (610, 10),
    (620, 10),
    (630, 10),
    (640, 10),
    (650, 10),
    (670, 10),
    (680, 10),
    (690, 5),
)

HIGH_POWER_SCALE_W = 10.0


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


def _s16(
    value: int,
) -> int:
    normalized = (
        int(value)
        & 0xFFFF
    )

    if normalized & 0x8000:
        return (
            normalized
            - 0x10000
        )

    return normalized


def _u32_low_high(
    low: int,
    high: int,
) -> int:
    return (
        (
            (
                int(high)
                & 0xFFFF
            )
            << 16
        )
        |
        (
            int(low)
            & 0xFFFF
        )
    )


def _s32_low_high(
    low: int,
    high: int,
) -> int:
    value = _u32_low_high(
        low,
        high,
    )

    if value & 0x80000000:
        return (
            value
            - 0x100000000
        )

    return value


def _u32_high_low(
    high: int,
    low: int,
) -> int:
    return (
        (
            (
                int(high)
                & 0xFFFF
            )
            << 16
        )
        |
        (
            int(low)
            & 0xFFFF
        )
    )


def _rated_power_candidates(
    registers: dict[int, int],
) -> list[dict[str, Any]]:
    candidates: list[
        dict[str, Any]
    ] = []

    for first, second in (
        (16, 17),
        (20, 21),
    ):
        if (
            first not in registers
            or second not in registers
        ):
            continue

        a = (
            int(registers[first])
            & 0xFFFF
        )

        b = (
            int(registers[second])
            & 0xFFFF
        )

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
                    "word_order":
                        order,
                    "raw":
                        raw,
                    "rated_power_w":
                        raw * 0.1,
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

    return "".join(
        parts
    )


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

    verified = (
        device_type
        in DEVICE_TYPE_CANDIDATES
        and reported_modbus_id
        == device_id
        and phases == 3
        and matching_power
        is not None
    )

    return {
        "verified":
            verified,
        "device_type_raw":
            device_type,
        "device_type_hex":
            f"0x{device_type:04X}",
        "reported_modbus_id":
            reported_modbus_id,
        "expected_modbus_id":
            device_id,
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
        "mppts":
            mppts,
        "rated_power_candidates":
            power_candidates,
        "matched_rated_power":
            matching_power,
    }


def _entity(
    *,
    key: str,
    category: str,
    name: str,
    value: Any,
    unit: str | None,
    value_type: str,
    source_address: int | None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged_attributes = {
        "vendor":
            "deye",
        "model_family":
            "sun_sg01hp3_20_50kw_hv",
        "verification_status":
            "field_register_survey_00015_20261005",
        "read_only":
            True,
    }

    if isinstance(
        attributes,
        dict,
    ):
        merged_attributes.update(
            attributes
        )

    return {
        "entity_key":
            key,
        "category":
            category,
        "name":
            name,
        "value":
            value,
        "unit":
            unit,
        "value_type":
            value_type,
        "quality":
            "good",
        "source_address":
            source_address,
        "attributes":
            merged_attributes,
    }


def _require(
    registers: dict[int, int],
    address: int,
) -> int:
    if address not in registers:
        raise RuntimeError(
            "Deye 50 HV chybi live registr "
            + str(address)
        )

    return (
        int(
            registers[
                address
            ]
        )
        & 0xFFFF
    )


def _live_entities(
    registers: dict[int, int],
) -> list[dict[str, Any]]:
    result: list[
        dict[str, Any]
    ] = []

    # Battery
    battery_temperature_c = round(
        (
            _require(
                registers,
                586,
            )
            - 1000
        )
        * 0.1,
        1,
    )

    battery_voltage_v = round(
        _require(
            registers,
            587,
        )
        * 0.1,
        1,
    )

    battery_soc = int(
        _require(
            registers,
            588,
        )
    )

    battery_power_w = int(
        round(
            _s16(
                _require(
                    registers,
                    590,
                )
            )
            * HIGH_POWER_SCALE_W
        )
    )

    battery_current_a = round(
        _s16(
            _require(
                registers,
                591,
            )
        )
        * 0.01,
        2,
    )

    if not (
        -50.0
        <= battery_temperature_c
        <= 100.0
    ):
        raise RuntimeError(
            "Deye 50 HV battery temperature mimo bezpecny rozsah."
        )

    if not (
        0.0
        < battery_voltage_v
        < 1000.0
    ):
        raise RuntimeError(
            "Deye 50 HV battery voltage mimo bezpecny rozsah."
        )

    if not (
        0
        <= battery_soc
        <= 100
    ):
        raise RuntimeError(
            "Deye 50 HV battery SOC mimo rozsah."
        )

    result.extend(
        [
            _entity(
                key="baterie.teplota",
                category="baterie_souhrn",
                name="Teplota baterie",
                value=battery_temperature_c,
                unit="°C",
                value_type="number",
                source_address=586,
                attributes={
                    "device_class":
                        "temperature",
                    "formula":
                        "(raw-1000)*0.1",
                },
            ),
            _entity(
                key="baterie.napeti",
                category="baterie_souhrn",
                name="Napětí baterie",
                value=battery_voltage_v,
                unit="V",
                value_type="number",
                source_address=587,
                attributes={
                    "device_class":
                        "voltage",
                    "formula":
                        "raw*0.1",
                },
            ),
            _entity(
                key="baterie.soc",
                category="baterie_souhrn",
                name="Stav nabití baterie",
                value=battery_soc,
                unit="%",
                value_type="integer",
                source_address=588,
                attributes={
                    "device_class":
                        "battery",
                },
            ),
            _entity(
                key="baterie.vykon",
                category="baterie_souhrn",
                name="Výkon baterie",
                value=battery_power_w,
                unit="W",
                value_type="integer",
                source_address=590,
                attributes={
                    "device_class":
                        "power",
                    "sign_convention":
                        "negative_charging_positive_discharging",
                    "formula":
                        "s16(raw)*10",
                },
            ),
            _entity(
                key="baterie.proud",
                category="baterie_souhrn",
                name="Proud baterie",
                value=battery_current_a,
                unit="A",
                value_type="number",
                source_address=591,
                attributes={
                    "device_class":
                        "current",
                    "formula":
                        "s16(raw)*0.01",
                },
            ),
        ]
    )

    # Grid phase voltages and frequency
    for phase, address in (
        (1, 598),
        (2, 599),
        (3, 600),
    ):
        value = round(
            _require(
                registers,
                address,
            )
            * 0.1,
            1,
        )

        result.append(
            _entity(
                key=(
                    "smartmeter.napeti_l"
                    + str(
                        phase
                    )
                ),
                category="smartmeter",
                name=(
                    "Napětí sítě L"
                    + str(
                        phase
                    )
                ),
                value=value,
                unit="V",
                value_type="number",
                source_address=address,
                attributes={
                    "device_class":
                        "voltage",
                    "formula":
                        "raw*0.1",
                },
            )
        )

    result.append(
        _entity(
            key="smartmeter.frekvence",
            category="smartmeter",
            name="Frekvence sítě",
            value=round(
                _require(
                    registers,
                    609,
                )
                * 0.01,
                2,
            ),
            unit="Hz",
            value_type="number",
            source_address=609,
            attributes={
                "device_class":
                    "frequency",
                "formula":
                    "raw*0.01",
            },
        )
    )

    # Grid power. Deye raw is positive for import on this family.
    # Fanda normalizes grid as negative import / positive export.
    grid_pairs = (
        (
            "smartmeter.vykon_l1",
            "Výkon sítě L1",
            622,
            687,
        ),
        (
            "smartmeter.vykon_l2",
            "Výkon sítě L2",
            623,
            688,
        ),
        (
            "smartmeter.vykon_l3",
            "Výkon sítě L3",
            624,
            689,
        ),
        (
            "smartmeter.vykon_celkem",
            "Výkon sítě celkem",
            625,
            690,
        ),
    )

    for (
        key,
        name,
        low_address,
        high_address,
    ) in grid_pairs:
        raw_power = _s32_low_high(
            _require(
                registers,
                low_address,
            ),
            _require(
                registers,
                high_address,
            ),
        )

        normalized_power = (
            -raw_power
        )

        result.append(
            _entity(
                key=key,
                category="smartmeter",
                name=name,
                value=int(
                    normalized_power
                ),
                unit="W",
                value_type="integer",
                source_address=low_address,
                attributes={
                    "device_class":
                        "power",
                    "sign_convention":
                        "negative_import_positive_export",
                    "raw_sign_convention":
                        "positive_import_negative_export",
                    "source_registers": [
                        low_address,
                        high_address,
                    ],
                    "formula":
                        "-s32(low,high)",
                },
            )
        )

    # Load voltages
    for phase, address in (
        (1, 644),
        (2, 645),
        (3, 646),
    ):
        value = round(
            _require(
                registers,
                address,
            )
            * 0.1,
            1,
        )

        result.append(
            _entity(
                key=(
                    "spotreba.napeti_l"
                    + str(
                        phase
                    )
                ),
                category="spotreba",
                name=(
                    "Napětí spotřeby L"
                    + str(
                        phase
                    )
                ),
                value=value,
                unit="V",
                value_type="number",
                source_address=address,
                attributes={
                    "device_class":
                        "voltage",
                    "formula":
                        "raw*0.1",
                },
            )
        )

    # Dedicated load consumption power registers.
    load_pairs = (
        (
            "spotreba.vykon_l1",
            "Spotřeba L1",
            650,
            656,
        ),
        (
            "spotreba.vykon_l2",
            "Spotřeba L2",
            651,
            657,
        ),
        (
            "spotreba.vykon_l3",
            "Spotřeba L3",
            652,
            658,
        ),
        (
            "spotreba.vykon_celkem",
            "Spotřeba celkem",
            653,
            659,
        ),
    )

    for (
        key,
        name,
        low_address,
        high_address,
    ) in load_pairs:
        power_w = _s32_low_high(
            _require(
                registers,
                low_address,
            ),
            _require(
                registers,
                high_address,
            ),
        )

        result.append(
            _entity(
                key=key,
                category="spotreba",
                name=name,
                value=int(
                    power_w
                ),
                unit="W",
                value_type="integer",
                source_address=low_address,
                attributes={
                    "device_class":
                        "power",
                    "sign_convention":
                        "positive_consumption",
                    "source_registers": [
                        low_address,
                        high_address,
                    ],
                    "formula":
                        "s32(low,high)",
                },
            )
        )

    # PV1..PV4. On 50 kW SG01HP3 family the power scale is 10 W.
    pv_power_addresses = (
        672,
        673,
        674,
        675,
    )

    pv_powers: list[int] = []

    for index, address in enumerate(
        pv_power_addresses,
        start=1,
    ):
        power_w = int(
            round(
                _require(
                    registers,
                    address,
                )
                * HIGH_POWER_SCALE_W
            )
        )

        pv_powers.append(
            power_w
        )

        result.append(
            _entity(
                key=(
                    "pv"
                    + str(
                        index
                    )
                    + ".vykon"
                ),
                category="pv_vstup",
                name=(
                    "Výkon PV"
                    + str(
                        index
                    )
                ),
                value=power_w,
                unit="W",
                value_type="integer",
                source_address=address,
                attributes={
                    "device_class":
                        "power",
                    "formula":
                        "raw*10",
                    "daylight_confirmation":
                        "pending",
                },
            )
        )

    for index, (
        voltage_address,
        current_address,
    ) in enumerate(
        (
            (676, 677),
            (678, 679),
            (680, 681),
            (682, 683),
        ),
        start=1,
    ):
        result.append(
            _entity(
                key=(
                    "pv"
                    + str(
                        index
                    )
                    + ".napeti"
                ),
                category="pv_vstup",
                name=(
                    "Napětí PV"
                    + str(
                        index
                    )
                ),
                value=round(
                    _require(
                        registers,
                        voltage_address,
                    )
                    * 0.1,
                    1,
                ),
                unit="V",
                value_type="number",
                source_address=voltage_address,
                attributes={
                    "device_class":
                        "voltage",
                    "formula":
                        "raw*0.1",
                    "daylight_confirmation":
                        "pending",
                },
            )
        )

        result.append(
            _entity(
                key=(
                    "pv"
                    + str(
                        index
                    )
                    + ".proud"
                ),
                category="pv_vstup",
                name=(
                    "Proud PV"
                    + str(
                        index
                    )
                ),
                value=round(
                    _require(
                        registers,
                        current_address,
                    )
                    * 0.1,
                    1,
                ),
                unit="A",
                value_type="number",
                source_address=current_address,
                attributes={
                    "device_class":
                        "current",
                    "formula":
                        "raw*0.1",
                    "daylight_confirmation":
                        "pending",
                },
            )
        )

    result.append(
        _entity(
            key="pv.vykon_celkem",
            category="pv_vstup",
            name="Výkon FVE celkem",
            value=int(
                sum(
                    pv_powers
                )
            ),
            unit="W",
            value_type="integer",
            source_address=672,
            attributes={
                "device_class":
                    "power",
                "derived_from": [
                    "pv1.vykon",
                    "pv2.vykon",
                    "pv3.vykon",
                    "pv4.vykon",
                ],
                "daylight_confirmation":
                    "pending",
            },
        )
    )

    keys = [
        item[
            "entity_key"
        ]
        for item in result
    ]

    if len(keys) != len(
        set(
            keys
        )
    ):
        raise RuntimeError(
            "Deye 50 HV mapper vytvoril duplicitni entity."
        )

    return result


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

    if allowed != [
        DEVICE_ID,
    ]:
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

    registers: dict[
        int,
        int
    ] = {}

    errors: list[
        dict[str, Any]
    ] = []

    bus_lock.acquire()

    try:
        if not client.connect():
            raise RuntimeError(
                "Deye 50 HV RS485 port nelze otevrit."
            )

        identity_values, identity_error = (
            _read_block(
                client,
                device_id=DEVICE_ID,
                address=0,
                count=23,
            )
        )

        if identity_values is None:
            raise RuntimeError(
                "Deye 50 HV identity blok 0-22 selhal: "
                + str(
                    identity_error
                )
            )

        for offset, value in enumerate(
            identity_values
        ):
            registers[
                offset
            ] = int(
                value
            )

        for address, count in LIVE_BLOCKS:
            values, error = _read_block(
                client,
                device_id=DEVICE_ID,
                address=address,
                count=count,
            )

            if values is None:
                errors.append(
                    {
                        "address":
                            address,
                        "count":
                            count,
                        "error":
                            error,
                    }
                )
                continue

            for offset, value in enumerate(
                values
            ):
                registers[
                    address
                    + offset
                ] = int(
                    value
                )

    finally:
        try:
            client.close()
        finally:
            bus_lock.release()

    if errors:
        raise RuntimeError(
            "Deye 50 HV live read nebyl uplny: "
            + str(
                errors
            )
        )

    identity = (
        _identity_from_registers(
            registers,
            device_id=DEVICE_ID,
        )
    )

    if (
        identity[
            "verified"
        ]
        is not True
    ):
        raise RuntimeError(
            "Deye 50 HV identity nebyla potvrzena."
        )

    entities = (
        _live_entities(
            registers
        )
    )

    matched_power = identity.get(
        "matched_rated_power"
    )

    if isinstance(
        matched_power,
        dict,
    ):
        entities.append(
            _entity(
                key="stridac.jmenovity_vykon",
                category="stridac",
                name="Jmenovitý výkon střídače",
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
                source_address=int(
                    matched_power[
                        "registers"
                    ][0]
                ),
                attributes={
                    "diagnostic":
                        True,
                    "identity_profile":
                        True,
                },
            )
        )

    entities.append(
        _entity(
            key="stridac.pocet_fazi",
            category="stridac",
            name="Počet fází střídače",
            value=int(
                identity[
                    "phases"
                ]
            ),
            unit=None,
            value_type="integer",
            source_address=22,
            attributes={
                "diagnostic":
                    True,
                "identity_profile":
                    True,
            },
        )
    )

    entities.append(
        _entity(
            key="stridac.pocet_mppt",
            category="stridac",
            name="Počet MPPT",
            value=int(
                identity[
                    "mppts"
                ]
            ),
            unit=None,
            value_type="integer",
            source_address=22,
            attributes={
                "diagnostic":
                    True,
                "identity_profile":
                    True,
            },
        )
    )

    return {
        "complete":
            True,
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
            "deye_50_hybrid_hv_live_rs485",
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
                    {
                        str(
                            address
                        ):
                            value
                        for (
                            address,
                            value,
                        )
                        in sorted(
                            registers.items()
                        )
                    },
                "errors": [],
            }
        ],
        "entities":
            entities,
    }
