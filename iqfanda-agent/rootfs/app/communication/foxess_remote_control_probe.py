"""Read-only FoxESS H3 remote-control register probe."""

from __future__ import annotations

from typing import Any

from pymodbus.client import ModbusSerialClient

from communication.goodwe_probe import _find_communicator
from communication.modbus_bus_lock import ziskej_zamek_modbus_sbernice


EXPECTED_PROFILE_ID = "foxess_h3_10_0_e"
EXPECTED_MODEL = "H3-10.0-E"
EXPECTED_PROTOCOL_VERSION = 115
EXPECTED_DEVICE_ID = 247

PROTOCOL_VERSION_REGISTER = 30028
WORK_MODE_REGISTER = 41000
MAX_SOC_REGISTER = 41010
REMOTE_ENABLE_REGISTER = 44000
REMOTE_TIMEOUT_REGISTER = 44001
REMOTE_ACTIVE_POWER_HIGH_REGISTER = 44002
REMOTE_ACTIVE_POWER_LOW_REGISTER = 44003
BATTERY_POWER_REGISTER = 31036
BATTERY_SOC_REGISTER = 31038

READ_BLOCKS = (
    (PROTOCOL_VERSION_REGISTER, 1),
    (WORK_MODE_REGISTER, 1),
    (MAX_SOC_REGISTER, 1),
    (REMOTE_ENABLE_REGISTER, 4),
    (BATTERY_POWER_REGISTER, 1),
    (BATTERY_SOC_REGISTER, 1),
)


def _select_runtime(cloud_config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cloud_config, dict):
        raise RuntimeError("Cloudova konfigurace neni dostupna.")

    runtime_configurations = cloud_config.get("module_runtime_configurations")
    if not isinstance(runtime_configurations, list):
        raise RuntimeError("Cloudova konfigurace nema runtime modulu.")

    matches = []

    for runtime in runtime_configurations:
        if not isinstance(runtime, dict):
            continue

        if str(runtime.get("module_key") or "").strip().lower() != "photovoltaic":
            continue

        profile_id = str(
            runtime.get("profile_id")
            or runtime.get("inverter_profile_id")
            or ""
        ).strip()

        manufacturer = str(runtime.get("manufacturer") or "").strip().lower()
        model = str(
            runtime.get("model")
            or runtime.get("identified_model")
            or ""
        ).strip()
        communication_type = str(
            runtime.get("communication_type")
            or ""
        ).strip().lower()

        if profile_id == EXPECTED_PROFILE_ID or (
            manufacturer == "foxess" and model == EXPECTED_MODEL
        ):
            if communication_type != "rs485":
                raise RuntimeError("FoxESS runtime nema RS485 transport.")
            matches.append(runtime)

    if len(matches) != 1:
        raise RuntimeError("FoxESS H3 runtime nebyl nalezen jednoznacne.")

    return matches[0]


def _decode_s16(value: int) -> int:
    raw = int(value) & 0xFFFF
    return raw - 0x10000 if raw & 0x8000 else raw


def _decode_s32(high_word: int, low_word: int) -> int:
    value = ((int(high_word) & 0xFFFF) << 16) | (int(low_word) & 0xFFFF)
    return value - 0x100000000 if value & 0x80000000 else value


def _read_block(*, client: Any, device_id: int, address: int, count: int) -> list[int]:
    response = client.read_holding_registers(
        address=address,
        count=count,
        device_id=device_id,
    )

    if response is None:
        raise RuntimeError(f"FoxESS read {address}/{count} vratil None.")

    is_error = getattr(response, "isError", None)
    if callable(is_error) and is_error():
        raise RuntimeError(f"FoxESS read {address}/{count} vratil Modbus chybu.")

    registers = getattr(response, "registers", None)
    if not isinstance(registers, list) or len(registers) != count:
        raise RuntimeError(f"FoxESS read {address}/{count} nema platny vysledek.")

    return [int(value) & 0xFFFF for value in registers]


def probe_foxess_remote_control_from_cloud_config(
    *,
    cloud_config: dict[str, Any],
) -> dict[str, Any]:
    runtime = _select_runtime(cloud_config)

    communicator_id = str(runtime.get("communicator_id") or "").strip()
    if not communicator_id:
        raise RuntimeError("FoxESS runtime nema communicator_id.")

    device_id = runtime.get("modbus_device_id")
    if device_id is None:
        device_id = runtime.get("matched_device_id")

    if int(device_id) != EXPECTED_DEVICE_ID:
        raise RuntimeError("FoxESS runtime nema ocekavanou Modbus adresu 247.")

    _communicator, serial_path = _find_communicator(communicator_id)
    bus_lock = ziskej_zamek_modbus_sbernice(serial_path)
    values: dict[int, int] = {}

    with bus_lock:
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
                raise RuntimeError("FoxESS control probe nelze otevrit seriovy port.")

            for address, count in READ_BLOCKS:
                block = _read_block(
                    client=client,
                    device_id=EXPECTED_DEVICE_ID,
                    address=address,
                    count=count,
                )
                for offset, raw in enumerate(block):
                    values[address + offset] = raw
        finally:
            client.close()

    protocol_version = values[PROTOCOL_VERSION_REGISTER]
    if protocol_version != EXPECTED_PROTOCOL_VERSION:
        raise RuntimeError("FoxESS Modbus protocol neni 115.")

    work_mode = values[WORK_MODE_REGISTER]
    max_soc = values[MAX_SOC_REGISTER]
    remote_enable = values[REMOTE_ENABLE_REGISTER]
    timeout_set = values[REMOTE_TIMEOUT_REGISTER]
    active_power_w = _decode_s32(
        values[REMOTE_ACTIVE_POWER_HIGH_REGISTER],
        values[REMOTE_ACTIVE_POWER_LOW_REGISTER],
    )
    battery_power_w = _decode_s16(values[BATTERY_POWER_REGISTER])
    battery_soc_percent = values[BATTERY_SOC_REGISTER]

    if remote_enable not in {0, 1}:
        raise RuntimeError("FoxESS remote_enable ma neocekavanou hodnotu.")
    if not 0 <= max_soc <= 100:
        raise RuntimeError("FoxESS max_soc je mimo rozsah.")
    if not 0 <= battery_soc_percent <= 100:
        raise RuntimeError("FoxESS battery SOC je mimo rozsah.")
    if not 0 <= work_mode <= 10:
        raise RuntimeError("FoxESS work mode je mimo ocekavany rozsah.")

    return {
        "schema_version": 1,
        "read_only": True,
        "profile_id": EXPECTED_PROFILE_ID,
        "protocol_version": protocol_version,
        "device_id": EXPECTED_DEVICE_ID,
        "serial_path_verified": serial_path.startswith("/dev/serial/by-id/"),
        "registers": {
            "work_mode_41000": work_mode,
            "max_soc_41010": max_soc,
            "remote_enable_44000": remote_enable,
            "remote_timeout_44001": timeout_set,
            "remote_active_power_w_44002_44003": active_power_w,
            "battery_power_w_31036": battery_power_w,
            "battery_soc_percent_31038": battery_soc_percent,
        },
        "write_performed": False,
        "candidate_control_map": {
            "remote_enable": 44000,
            "timeout_set": 44001,
            "active_power_high": 44002,
            "active_power_low": 44003,
            "work_mode": 41000,
            "max_soc": 41010,
            "battery_power": 31036,
            "battery_soc": 31038,
        },
    }
