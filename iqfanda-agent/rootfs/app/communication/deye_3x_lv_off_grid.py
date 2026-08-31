"""Read-only adapter pro specialni instalaci 3xDEYE LV off grid."""

from __future__ import annotations

import time
from typing import Any


DEVICE_LAYOUT = (
    {
        "device_id": 1,
        "role": "master",
        "ac_bus": "ac_bus_1",
        "pv_start": 1,
    },
    {
        "device_id": 2,
        "role": "slave",
        "ac_bus": "ac_bus_1",
        "pv_start": 3,
    },
    {
        "device_id": 3,
        "role": "master",
        "ac_bus": "ac_bus_2",
        "pv_start": 5,
    },
)


def _s16(value: int) -> int:
    value = int(value) & 0xFFFF
    return (
        value - 0x10000
        if value & 0x8000
        else value
    )


def _u32_low_high(
    low: int,
    high: int,
) -> int:
    return (
        ((int(high) & 0xFFFF) << 16)
        | (int(low) & 0xFFFF)
    )


def _entity(
    key: str,
    category: str,
    name: str,
    value: Any,
    unit: str | None,
    value_type: str,
    *,
    quality: str = "good",
    source_address: int | None = None,
    device_class: str | None = None,
) -> dict[str, Any]:

    attributes: dict[str, Any] = {}

    if device_class:
        attributes["device_class"] = (
            device_class
        )

    return {
        "entity_key": key,
        "category": category,
        "name": name,
        "value": value,
        "unit": unit,
        "value_type": value_type,
        "quality": quality,
        "source_address": source_address,
        "attributes": attributes,
    }


def _read_block(
    client: Any,
    device_id: int,
    address: int,
    count: int,
) -> tuple[list[int] | None, str | None]:

    error: str | None = None

    for attempt in range(2):

        try:
            response = (
                client.read_holding_registers(
                    address=address,
                    count=count,
                    device_id=device_id,
                )
            )

            if response is None:
                error = "no_response"

            elif response.isError():
                error = str(response)

            else:
                values = [
                    int(value)
                    for value
                    in getattr(
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
            error = str(exc)

        if attempt == 0:
            time.sleep(0.15)

    return None, error


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
            "Deye runtime neni read-only."
        )

    protocol = profile["protocol"]

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
            protocol["timeout_seconds"]
        ),
        retries=int(
            protocol.get(
                "retries",
                0,
            )
        ),
    )

    inverters: list[dict[str, Any]] = []

    bus_lock.acquire()

    try:
        if not client.connect():
            raise RuntimeError(
                "Deye RS485 port nelze otevrit."
            )

        for layout in DEVICE_LAYOUT:

            device_id = int(
                layout["device_id"]
            )

            registers: dict[int, int] = {}
            errors: list[str] = []
            core_ok = True

            blocks = (
                (526, 10, False, "energy"),
                (630, 7, True, "inverter"),
                (650, 4, True, "load"),
                (672, 8, True, "pv"),
            )

            for (
                address,
                count,
                required,
                name,
            ) in blocks:

                values, error = _read_block(
                    client,
                    device_id,
                    address,
                    count,
                )

                if values is None:

                    if required:
                        core_ok = False

                    errors.append(
                        name
                        + ":"
                        + str(error)
                    )

                    continue

                for offset, value in enumerate(
                    values
                ):
                    registers[
                        address + offset
                    ] = value

            inverters.append({
                **layout,
                "online": core_ok,
                "registers": registers,
                "errors": errors,
            })

    finally:
        try:
            client.close()
        finally:
            bus_lock.release()

    online = [
        item
        for item in inverters
        if item["online"] is True
    ]

    if not online:
        raise RuntimeError(
            "Neodpovida zadny Deye menic."
        )

    complete = (
        len(online)
        == len(DEVICE_LAYOUT)
    )

    aggregate_quality = (
        "good"
        if complete
        else "partial"
    )

    entities: list[dict[str, Any]] = []


    # --------------------------------------------------
    # 6 fyzickych PV vstupu
    #
    # Deye:
    # 672 = PV1 power
    # 673 = PV2 power
    # 676 = PV1 voltage
    # 677 = PV1 current
    # 678 = PV2 voltage
    # 679 = PV2 current
    # --------------------------------------------------

    for item in online:

        registers = item["registers"]

        pv_inputs = (
            (
                int(item["pv_start"]),
                676,
                677,
                672,
            ),
            (
                int(item["pv_start"]) + 1,
                678,
                679,
                673,
            ),
        )

        for (
            pv_number,
            voltage_reg,
            current_reg,
            power_reg,
        ) in pv_inputs:

            required = (
                voltage_reg,
                current_reg,
                power_reg,
            )

            if not all(
                address in registers
                for address in required
            ):
                continue

            prefix = (
                "pv"
                + str(pv_number)
            )

            entities.extend([
                _entity(
                    prefix + ".napeti",
                    "pv_vstup",
                    "Napětí " + prefix.upper(),
                    registers[voltage_reg] * 0.1,
                    "V",
                    "number",
                    source_address=voltage_reg,
                    device_class="voltage",
                ),
                _entity(
                    prefix + ".proud",
                    "pv_vstup",
                    "Proud " + prefix.upper(),
                    registers[current_reg] * 0.1,
                    "A",
                    "number",
                    source_address=current_reg,
                    device_class="current",
                ),
                _entity(
                    prefix + ".vykon",
                    "pv_vstup",
                    "Výkon " + prefix.upper(),
                    int(registers[power_reg]),
                    "W",
                    "integer",
                    source_address=power_reg,
                    device_class="power",
                ),
            ])


    # --------------------------------------------------
    # jednotlive menice + soucet
    # --------------------------------------------------

    inverter_powers: list[int] = []

    for item in online:

        registers = item["registers"]

        if 636 not in registers:
            continue

        device_id = int(
            item["device_id"]
        )

        power = _s16(
            registers[636]
        )

        inverter_powers.append(
            power
        )

        entities.append(
            _entity(
                (
                    "stridac"
                    + str(device_id)
                    + ".vykon"
                ),
                "stridac",
                (
                    "Výkon střídače "
                    + str(device_id)
                ),
                power,
                "W",
                "integer",
                source_address=636,
                device_class="power",
            )
        )

    if inverter_powers:
        entities.append(
            _entity(
                "stridac.vykon_celkem",
                "stridac",
                "Celkový výkon střídačů",
                sum(inverter_powers),
                "W",
                "integer",
                quality=aggregate_quality,
                device_class="power",
            )
        )


    # --------------------------------------------------
    # dva AC okruhy
    # --------------------------------------------------

    for bus, key, name, expected in (
        (
            "ac_bus_1",
            "spotreba.okruh1.vykon",
            "Spotřeba AC okruh 1",
            2,
        ),
        (
            "ac_bus_2",
            "spotreba.okruh2.vykon",
            "Spotřeba AC okruh 2",
            1,
        ),
    ):

        powers = [
            _s16(
                item["registers"][653]
            )
            for item in online
            if (
                item["ac_bus"] == bus
                and 653
                in item["registers"]
            )
        ]

        if powers:
            entities.append(
                _entity(
                    key,
                    "spotreba",
                    name,
                    sum(powers),
                    "W",
                    "integer",
                    quality=(
                        "good"
                        if len(powers)
                        == expected
                        else "partial"
                    ),
                    device_class="power",
                )
            )


    all_loads = [
        _s16(
            item["registers"][653]
        )
        for item in online
        if 653 in item["registers"]
    ]

    if all_loads:
        entities.append(
            _entity(
                "spotreba.vykon_celkem",
                "spotreba",
                "Spotřeba celkem",
                sum(all_loads),
                "W",
                "integer",
                quality=aggregate_quality,
                device_class="power",
            )
        )


    # --------------------------------------------------
    # energie
    # Deye 32bit counters: LOW word + HIGH word
    # --------------------------------------------------

    pv_today = [
        item["registers"][529] * 0.1
        for item in online
        if 529 in item["registers"]
    ]

    if pv_today:
        entities.append(
            _entity(
                "vyroba.energie_dnes",
                "vyroba",
                "Výroba energie dnes",
                sum(pv_today),
                "kWh",
                "number",
                quality=(
                    "good"
                    if len(pv_today) == 3
                    else "partial"
                ),
            )
        )

    pv_total = [
        (
            _u32_low_high(
                item["registers"][534],
                item["registers"][535],
            )
            * 0.1
        )
        for item in online
        if (
            534 in item["registers"]
            and 535 in item["registers"]
        )
    ]

    if pv_total:
        entities.append(
            _entity(
                "vyroba.energie_celkem",
                "vyroba",
                "Výroba energie celkem",
                sum(pv_total),
                "kWh",
                "number",
                quality=(
                    "good"
                    if len(pv_total) == 3
                    else "partial"
                ),
            )
        )


    # --------------------------------------------------
    # tato konkretni konfigurace je fyzicky OFF-GRID
    # --------------------------------------------------

    entities.append(
        _entity(
            "sit.pripojeno",
            "sit",
            "Připojení k distribuční síti",
            False,
            None,
            "boolean",
        )
    )


    # ZAMERNE NEPUBLIKUJEME:
    #
    # baterie.*
    # smartmeter.*
    #
    # SOC a ostatni bateriova data budou
    # z prime komunikace Pylontech.
    #
    # Generator bude samostatny Rotek zdroj.

    return {
        "complete": complete,
        "read_only": True,
        "profile_id":
            profile["profile_id"],
        "manufacturer": "deye",
        "model":
            "3xDEYE LV off grid",
        "telemetry_source":
            "deye_3x_lv_off_grid_rs485",
        "serial_path": serial_path,
        "modbus_device_ids": [
            1,
            2,
            3,
        ],
        "grid_connected": False,
        "shared_dc_bus": True,
        "battery_source":
            "external_pylontech_module",
        "generator_source":
            "external_rotek_module",
        "topology": {
            "ac_bus_1": {
                "master": 1,
                "slave": 2,
            },
            "ac_bus_2": {
                "master": 3,
            },
        },
        "inverters": inverters,
        "entities": entities,
    }
