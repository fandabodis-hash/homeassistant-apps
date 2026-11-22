"""F106 R1 / FANDA 00019 Midea MHC-V8WD2N7-E30 / read-only Modbus RTU.

This module is inert unless explicitly called. No auto-start, no Modbus writes,
no Agent version bump, no cloud commands, no port scanning, no Zigbee control.
The register map is a *candidate* pending verification against the actual heat pump.
"""
from __future__ import annotations

import math
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

SERIAL = "F800711-TNG-00019"
MODEL = "MHC-V8WD2N7-E30"
FTDI_PORT = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02YF7T-if00-port0"
BAUDRATE = 9600
BLOCKS = ((100, 11), (124, 1), (129, 1), (138, 14), (199, 1))
SENSOR_ADDRESSES = (100, 101, 104, 105, 107, 124, 129, 138, 143, 144, 150, 151, 199)
STOP_BITS = 1
PARITY = "N"

class MideaProbeError(RuntimeError):
    """Stop a probe rather than try different devices or write functions."""

def u16(raw: Any) -> int:
    if type(raw) is not int or not (0 <= raw <= 0xFFFF):
        raise MideaProbeError("INVALID_MODBUS_REGISTER_VALUE")
    return raw

def signed(raw: int) -> int:
    raw = u16(raw)
    return raw - 65536 if raw >= 32768 else raw

def decode(raw: dict[int, int]) -> dict[str, Any]:
    """Pure decoding; no serial IO or side effects. Zero can be a valid value."""
    values = {int(k): u16(v) for k, v in raw.items()}
    def value(address): return values.get(address)
    def temp(address): return signed(value(address)) if value(address) is not None else None
    def ratio(address, div):
        v = value(address)
        return round(v / div, 3) if v is not None else None
    energy = None
    if value(143) is not None and value(144) is not None:
        energy = round(((value(143) << 16) + value(144)) / 100, 2)
    run = value(101)
    return {
        "compressor_frequency_hz": value(100),
        "operating_state_raw": run,
        "operating_state": {0: "off", 2: "cooling", 3: "heating"}.get(run, "unknown"),
        "water_return_c": temp(104),
        "water_flow_c": temp(105),
        "outside_temperature_c": temp(107),
        "fault_code_raw": value(124),
        "internal_pump_on": None if value(129) is None else bool(value(129) & (1 << 3)),
        "water_flow_m3h": ratio(138, 100),
        "energy_total_kwh": energy,
        "electric_power_kw_candidate": ratio(150, 100),
        "cop_candidate": ratio(151, 100),
        "mode_raw": value(199),
        "verification": "UNVERIFIED_REGISTER_MAP_READ_ONLY",
    }

def validate_target(*, serial_number: str, port_path: str, unit_id: int, function_code: int) -> None:
    if serial_number != SERIAL: raise MideaProbeError("ONLY_00019_ALLOWED")
    if port_path != FTDI_PORT: raise MideaProbeError("FTDI_ID_MISMATCH_STOP")
    if type(unit_id) is not int or unit_id not in (1, 16): raise MideaProbeError("EXPLICIT_UNIT_ID_1_OR_16_REQUIRED")
    if type(function_code) is not int or function_code not in (3, 4): raise MideaProbeError("FC03_OR_FC04_ONLY")
    
def probe_once(
    *,
    serial_number: str,
    port_path: str,
    unit_id: int,
    function_code: int,
    client_factory: Callable[..., Any] | None = None,
    lock_factory: Callable[[str], Any] | None = None,
    check_path: bool = True,
) -> dict[str, Any]:
    """Bounded FC03/FC04 read-only probe. Never scans units/addresses indiscriminately."""
    validate_target(serial_number=serial_number, port_path=port_path,
                    unit_id=unit_id, function_code=function_code)
    if check_path and not Path(port_path).exists():
        raise MideaProbeError("FTDI_SERIAL_DEVICE_NOT_PRESENT")
    if client_factory is None:
        from pymodbus.client import ModbusSerialClient
        client_factory = ModbusSerialClient
    if lock_factory is None:
        from communication.modbus_bus_lock import ziskej_zamek_modbus_sbernice
        lock_factory = ziskej_zamek_modbus_sbernice
    client = client_factory(port=port_path, baudrate=BAUDRATE, bytesize=8,
                            parity=PARITY, stopbits=STOP_BITS, timeout=1.2, retries=0)
    lock = lock_factory(port_path)
    raw = {}
    blocks = []
    holding_status = None
    holding_mode = None
    with lock:
        try:
            if client.connect() is not True:
                raise MideaProbeError("PORT_NOT_OPEN")
            for address, count in BLOCKS:
                try:
                    if function_code == 3:
                        response = client.read_holding_registers(address=address, count=count, device_id=unit_id)
                    else:
                        response = client.read_input_registers(address=address, count=count, device_id=unit_id)
                    if response is None or response.isError():
                        blocks.append({"address": address, "count": count, "status": "no_response"})
                    else:
                        got = getattr(response, "registers", None)
                        if not isinstance(got, list) or len(got) != count:
                            raise MideaProbeError("INVALID_REGISTER_COUNT")
                        for index, reg in enumerate(got):
                            raw[address + index] = u16(reg)
                        blocks.append({"address": address, "count": count, "status": "ok"})
                except Exception as error:
                    # Preserve only type to avoid leaking low-level driver/port details.
                    blocks.append({"address": address, "count": count, "status": type(error).__name__})
                time.sleep(0.12)
            # F114: FC03 holding 0/1 (power bit and requested mode), read-only.
            # A failed optional holding read does not erase valid input values.
            try:
                status_response = client.read_holding_registers(
                    address=0, count=2, device_id=unit_id
                )
                if status_response is not None and not status_response.isError():
                    got = getattr(status_response, "registers", None)
                    if isinstance(got, list) and len(got) == 2:
                        holding_status, holding_mode = map(u16, got)
                        blocks.append({"address": 0, "count": 2, "function_code": 3, "status": "ok"})
                    else:
                        blocks.append({"address": 0, "count": 2, "function_code": 3, "status": "invalid_count"})
                else:
                    blocks.append({"address": 0, "count": 2, "function_code": 3, "status": "no_response"})
            except Exception as error:
                blocks.append({"address": 0, "count": 2, "function_code": 3, "status": type(error).__name__})
        finally:
            client.close()
    # No physical verification if the supply/return water block did not respond.
    if 104 not in raw or 105 not in raw:
        raise MideaProbeError("NO_WATER_TEMPERATURE_RESPONSE_NO_PROFILE_VERIFICATION")
    return {
        "serial_number": SERIAL,
        "model": MODEL,
        "port_identity_verified": True,
        "modbus_unit_id": unit_id,
        "modbus_function_code": function_code,
        "read_only": True,
        "writes_performed": False,
        "no_auto_control": True,
        "blocks": blocks,
        "read_register_count": len(raw),
        "raw_registers": {str(k): v for k, v in sorted(raw.items()) if k in SENSOR_ADDRESSES},
        "holding_status_available": holding_status is not None,
        "measurements": {
            **decode(raw),
            "zone1_enabled": None if holding_status is None else bool(holding_status & 0x0002),
            "mode_setting": holding_mode,
        },
    }
