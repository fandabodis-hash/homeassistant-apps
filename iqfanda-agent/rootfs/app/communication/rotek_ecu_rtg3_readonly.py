"""Rotek ECU-RTG3/RTG3s read-only Modbus RTU probe.

Contract:
- FC03 only.
- Only documented, explicitly whitelisted register blocks.
- No Modbus writes, no generator start/stop, no Zigbee control.
- Stable /dev/serial/by-id identity must match Agent communicator inventory.
- Shared per-port Modbus lock prevents parallel access inside Agent.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from communication.modbus_bus_lock import ziskej_zamek_modbus_sbernice


PROFILE_ID = "rotek_ecu_rtg3_modbus_rtu_readonly_v1"
COMMUNICATION_STATE = Path(
    os.getenv("IQF_COMMUNICATION_STATE_PATH", "/config/communication.json")
)
SERIAL_PATH_PATTERN = re.compile(
    r"^/dev/serial/by-id/[A-Za-z0-9_.+\-]{8,180}$"
)
BAUDRATE = 9600
SLAVE_ID = 1
TIMEOUT_S = 1.0

ALLOWED_BLOCKS = (
    (0, 2),
    (9, 2),
    (14, 1),
    (18, 9),
)

ALLOWED_ADDRESSES = frozenset(
    address
    for start, count in ALLOWED_BLOCKS
    for address in range(start, start + count)
)


class RotekProbeError(RuntimeError):
    pass


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _load_inventory(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("communicators"), list):
        raise RotekProbeError("Komunikacni inventar Agenta neni dostupny.")
    return [item for item in data["communicators"] if isinstance(item, dict)]


def _is_usb_hub_alias(item: dict[str, Any]) -> bool:
    product = _str(item.get("product")).lower()
    vid = _str(item.get("vid")).upper()
    pid = _str(item.get("pid")).upper()
    return (
        "usb hub" in product
        or "usb2.1 hub" in product
        or (vid == "0BDA" and pid in {"5411", "0411"})
    )


def validate_probe_input(
    payload: dict[str, Any],
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RotekProbeError("Pozadavek neni objekt.")
    if payload.get("read_only") is not True:
        raise RotekProbeError("Rotek probe musi byt read-only.")
    if payload.get("control_enabled") is not False:
        raise RotekProbeError("Rotek probe nesmi povolit ovladani.")
    if payload.get("probe_profile_id") != PROFILE_ID:
        raise RotekProbeError("Neplatny Rotek profil.")
    if payload.get("communication_type") != "rs485":
        raise RotekProbeError("Generator nema RS485 komunikaci.")
    if payload.get("manufacturer") != "rotek":
        raise RotekProbeError("Nespravny vyrobce generatoru.")
    if type(payload.get("slave_id")) is not int or payload["slave_id"] != SLAVE_ID:
        raise RotekProbeError("Nepovolena Modbus adresa jednotky.")
    if type(payload.get("baudrate")) is not int or payload["baudrate"] != BAUDRATE:
        raise RotekProbeError("Nepovolena rychlost Modbus RTU.")

    communicator_id = _str(payload.get("communicator_id"))
    serial = _str(payload.get("communicator_serial_number"))
    path = _str(payload.get("preferred_path"))

    if not communicator_id or not serial or not SERIAL_PATH_PATTERN.fullmatch(path):
        raise RotekProbeError("Neni urcena stabilni identita prevodniku.")

    matches = [
        item for item in inventory
        if _str(item.get("communicator_id")) == communicator_id
    ]
    if len(matches) != 1:
        raise RotekProbeError("Komunikator neni jednoznacny.")

    selected = matches[0]
    if _is_usb_hub_alias(selected):
        raise RotekProbeError("Vybrany komunikator je USB hub, ne FTDI port.")

    stable = selected.get("stable_paths")
    capabilities = selected.get("capabilities")

    if selected.get("connected") is not True or selected.get("status") != "ready":
        raise RotekProbeError("Komunikator neni aktualne pripraven.")
    if (
        _str(selected.get("preferred_path")) != path
        or not isinstance(stable, list)
        or path not in stable
    ):
        raise RotekProbeError("Stabilni cesta neodpovida inventari.")
    if _str(selected.get("serial_number")) != serial:
        raise RotekProbeError("Seriove cislo prevodniku nesouhlasi.")
    if _str(selected.get("manufacturer")).upper() != "FTDI":
        raise RotekProbeError("Rotek profil vyzaduje potvrzeny FTDI prevodnik.")
    if _str(selected.get("type")) not in {"usb_serial", "usb_rs485"}:
        raise RotekProbeError("Vybrany komunikator neni USB seriovy prevodnik.")
    if (
        not isinstance(capabilities, list)
        or "serial" not in capabilities
        or not any(
            item in capabilities
            for item in ("rs485", "rs485_or_rs232", "modbus_rtu")
        )
    ):
        raise RotekProbeError("Vybrane zarizeni nema RS485 moznosti.")

    physical = [
        item for item in inventory
        if isinstance(item, dict)
        and _str(item.get("preferred_path")) == path
    ]
    real_serial = [
        item for item in physical
        if _str(item.get("type")) in {"usb_serial", "usb_rs485"}
        and not _is_usb_hub_alias(item)
        and bool(_str(item.get("serial_number")))
    ]
    if (
        len(real_serial) != 1
        or _str(real_serial[0].get("communicator_id")) != communicator_id
    ):
        raise RotekProbeError("Fyzicky seriovy port neni jednoznacny.")

    return {
        "communicator_id": communicator_id,
        "serial_number": serial,
        "preferred_path": path,
    }


def _read_block(client: Any, start: int, count: int) -> dict[int, int]:
    if (start, count) not in ALLOWED_BLOCKS:
        raise RotekProbeError("Nepovoleny blok registru.")
    try:
        response = client.read_holding_registers(
            address=start,
            count=count,
            device_id=SLAVE_ID,
        )
    except Exception as exc:
        raise RotekProbeError(
            f"Cteni registru {start}:{count} selhalo: {type(exc).__name__}."
        ) from None

    if response is None or response.isError():
        raise RotekProbeError(f"ECU neodpovedela na registry {start}:{count}.")

    values = getattr(response, "registers", None)
    if (
        not isinstance(values, (list, tuple))
        or len(values) != count
        or any(type(value) is not int or not 0 <= value <= 65535 for value in values)
    ):
        raise RotekProbeError(f"Neplatna odpoved registru {start}:{count}.")

    return {start + offset: value for offset, value in enumerate(values)}


def _scaled(
    readings: dict[int, int],
    address: int,
    multiplier: float,
    minimum: float,
    maximum: float,
    digits: int | None = None,
) -> float | int | None:
    raw = readings.get(address)
    if type(raw) is not int:
        return None
    value = raw * multiplier
    if not minimum <= value <= maximum:
        return None
    if digits is None:
        return int(value) if float(value).is_integer() else value
    return round(value, digits)


def _decode(readings: dict[int, int]) -> dict[str, Any]:
    energy = None
    if type(readings.get(0)) is int and type(readings.get(1)) is int:
        raw_energy = (readings[1] << 16) | readings[0]
        candidate = raw_energy * 0.1
        if 0 <= candidate <= 999_999_999:
            energy = round(candidate, 1)

    power_factor = _scaled(readings, 9, 0.01, 0, 1.50, 2)
    active_power = _scaled(readings, 10, 0.1, 0, 250, 1)
    remote_word = readings.get(14)
    if type(remote_word) is not int:
        remote_word = None

    voltage_l1 = _scaled(readings, 18, 1.0, 0, 300, 0)
    voltage_l2 = _scaled(readings, 19, 1.0, 0, 300, 0)
    voltage_l3 = _scaled(readings, 20, 1.0, 0, 300, 0)
    coolant = _scaled(readings, 21, 1.0, 0, 180, 0)
    oil = _scaled(readings, 22, 0.01, 0, 20, 2)
    fuel = _scaled(readings, 23, 1.0, 0, 100, 0)
    battery = _scaled(readings, 24, 0.1, 5, 30, 1)
    frequency = _scaled(readings, 25, 0.1, 0, 70, 1)
    rpm = _scaled(readings, 26, 1.0, 0, 4000, 0)

    essential_ok = (
        battery is not None
        and frequency is not None
        and rpm is not None
    )
    voltage_count = sum(
        value is not None
        for value in (voltage_l1, voltage_l2, voltage_l3)
    )

    return {
        "total_electric_energy_kwh": energy,
        "power_factor": power_factor,
        "generator_active_power_kw": active_power,
        "remote_start_input_word": remote_word,
        "remote_start_active": bool(remote_word & 0x0100) if remote_word is not None else None,
        "voltage_l1_v": voltage_l1,
        "voltage_l2_v": voltage_l2,
        "voltage_l3_v": voltage_l3,
        "coolant_temperature_c": coolant,
        "oil_pressure_bar": oil,
        "fuel_level_percent": fuel,
        "fuel_level_status": "available" if fuel is not None else "not_supported_or_invalid",
        "battery_voltage_v": battery,
        "frequency_hz": frequency,
        "rpm": rpm,
        "engine_running": bool(rpm is not None and rpm >= 300),
        "plausible": bool(essential_ok and voltage_count >= 1),
    }


def probe_rotek_ecu_rtg3_readonly(
    payload: dict[str, Any],
    *,
    inventory: list[dict[str, Any]] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if inventory is None:
        inventory = _load_inventory(COMMUNICATION_STATE)

    chosen = validate_probe_input(payload, inventory)

    if client_factory is None:
        from pymodbus.client import ModbusSerialClient
        client_factory = ModbusSerialClient

    path = chosen["preferred_path"]
    lock = ziskej_zamek_modbus_sbernice(path)

    if not lock.acquire(blocking=False):
        raise RotekProbeError("Seriovou sbernici pouziva jiny proces nebo adapter.")

    client = None
    readings: dict[int, int] = {}
    errors: dict[str, str] = {}

    try:
        client = client_factory(
            port=path,
            baudrate=BAUDRATE,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=TIMEOUT_S,
            retries=0,
        )
        if not client.connect():
            raise RotekProbeError("Seriovy port nelze otevrit.")

        for start, count in ALLOWED_BLOCKS:
            key = f"{start}:{count}"
            try:
                readings.update(_read_block(client, start, count))
            except RotekProbeError as exc:
                errors[key] = str(exc)

        decoded = _decode(readings)
        verified = bool(
            decoded["plausible"] is True
            and len(readings) >= 8
            and {24, 25, 26}.issubset(readings)
        )

        return {
            "profile_id": PROFILE_ID,
            "executor": "rotek_ecu_rtg3_readonly",
            "read_only": True,
            "write_performed": False,
            "communication_type": "rs485",
            "manufacturer": "rotek",
            "communicator_id": chosen["communicator_id"],
            "communicator_serial_number": chosen["serial_number"],
            "serial_path": path,
            "matched_device_id": SLAVE_ID,
            "baudrate": BAUDRATE,
            "function_code": 3,
            "communication_detected": bool(readings),
            "register_readable": bool(readings),
            "profile_verified": verified,
            "phase": "profile_identified" if verified else "register_response" if readings else "no_response",
            "raw_registers": {
                str(address): value for address, value in sorted(readings.items())
            },
            "register_errors": errors,
            "telemetry": decoded,
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        lock.release()
