"""Bezpecne rizeni GoodWe EMS pro spotove nabijeni baterie."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pymodbus.client import ModbusSerialClient

from communication.goodwe_probe import (
    _find_communicator,
)
from communication.modbus_bus_lock import (
    ziskej_zamek_modbus_sbernice,
)


EMS_MODE_REGISTER = 47511
EMS_POWER_REGISTER = 47512

EMS_MODE_AUTO = 1
EMS_MODE_CHARGE_FROM_GRID = 4

MAXIMUM_CHARGE_POWER_W = 5000

SUPPORTED_DEVICE_IDS = {
    247,
}

SUPPORTED_ACTIONS = {
    "auto",
    "charge_grid",
}


@dataclass(frozen=True)
class GoodweEmsResult:
    action: str
    requested_power_w: int
    applied_power_w: int
    ems_mode: int
    ems_power_register: int
    verified: bool
    write_performed: bool = False


def _validate_device_id(
    device_id: int,
) -> int:
    normalized = int(
        device_id
    )

    if normalized not in SUPPORTED_DEVICE_IDS:
        raise ValueError(
            f"Nepovolena GoodWe Modbus adresa: "
            f"{normalized}"
        )

    return normalized


def _validate_action(
    action: str,
) -> str:
    normalized = str(
        action or ""
    ).strip()

    if normalized not in SUPPORTED_ACTIONS:
        raise ValueError(
            f"Nepodporovana EMS akce: "
            f"{normalized}"
        )

    return normalized


def _validate_charge_power(
    power_w: int,
) -> int:
    normalized = int(
        power_w
    )

    if normalized < 0:
        raise ValueError(
            "Nabijeci vykon nesmi byt zaporny."
        )

    if normalized > MAXIMUM_CHARGE_POWER_W:
        raise ValueError(
            "Nabijeci vykon prekrocil "
            "bezpecnostni limit 5000 W."
        )

    return normalized


def _write_single_register(
    *,
    client: Any,
    device_id: int,
    address: int,
    value: int,
) -> None:
    result = client.write_register(
        address=address,
        value=value,
        device_id=device_id,
    )

    if result is None:
        raise RuntimeError(
            f"GoodWe write {address} vratil None."
        )

    is_error = getattr(
        result,
        "isError",
        None,
    )

    if callable(is_error) and is_error():
        raise RuntimeError(
            f"GoodWe write {address} selhal: "
            f"{result}"
        )


def _read_single_register(
    *,
    client: Any,
    device_id: int,
    address: int,
) -> int:
    result = client.read_holding_registers(
        address=address,
        count=1,
        device_id=device_id,
    )

    if result is None:
        raise RuntimeError(
            f"GoodWe read {address} vratil None."
        )

    is_error = getattr(
        result,
        "isError",
        None,
    )

    if callable(is_error) and is_error():
        raise RuntimeError(
            f"GoodWe read {address} selhal: "
            f"{result}"
        )

    registers = getattr(
        result,
        "registers",
        None,
    )

    if (
        not isinstance(registers, list)
        or len(registers) != 1
    ):
        raise RuntimeError(
            f"GoodWe read {address} nema "
            "platny readback."
        )

    return int(
        registers[0]
    )


def _verify_register(
    *,
    client: Any,
    device_id: int,
    address: int,
    expected: int,
) -> None:
    actual = _read_single_register(
        client=client,
        device_id=device_id,
        address=address,
    )

    if actual != expected:
        raise RuntimeError(
            f"GoodWe registr {address}: "
            f"ocekavano {expected}, "
            f"nacteno {actual}."
        )


def preview_goodwe_ems_action(
    *,
    device_id: int,
    action: str,
    allowed_charge_power_w: int,
) -> GoodweEmsResult:
    """
    Pure dry-run EMS rozhodnuti.

    Nepripojuje serial port.
    Nevytvari Modbus klienta.
    Nevola write_register ani read.
    """

    device_id = _validate_device_id(
        device_id
    )

    action = _validate_action(
        action
    )

    power_w = _validate_charge_power(
        allowed_charge_power_w
    )

    if action == "auto":
        if power_w != 0:
            raise ValueError(
                "Akce auto musi mit vykon 0 W."
            )

        return GoodweEmsResult(
            action="auto",
            requested_power_w=0,
            applied_power_w=0,
            ems_mode=EMS_MODE_AUTO,
            ems_power_register=0,
            verified=True,
        )

    if power_w <= 0:
        raise ValueError(
            "charge_grid vyzaduje "
            "kladny vykon."
        )

    return GoodweEmsResult(
        action="charge_grid",
        requested_power_w=power_w,
        applied_power_w=power_w,
        ems_mode=EMS_MODE_CHARGE_FROM_GRID,
        ems_power_register=power_w,
        verified=True,
    )


def apply_goodwe_ems_action(
    *,
    client: Any,
    device_id: int,
    action: str,
    allowed_charge_power_w: int,
) -> GoodweEmsResult:
    """
    Provede jednu atomicitou chranenou EMS zmenu.

    Funkce sama klienta nevytvari.
    Proto ji lze plne testovat nad fake klientem.

    Bezpecnostni poradi:
      AUTO:
        47511 = 1
        47512 = 0

      CHARGE_GRID:
        47512 = vykon
        readback vykonu
        47511 = 4
        readback rezimu

    Pri chybe charge_grid se funkce pokusi
    vratit GoodWe do AUTO.
    """

    device_id = _validate_device_id(
        device_id
    )

    action = _validate_action(
        action
    )

    power_w = _validate_charge_power(
        allowed_charge_power_w
    )

    if action == "auto":
        if power_w != 0:
            raise ValueError(
                "Akce auto musi mit vykon 0 W."
            )

        _write_single_register(
            client=client,
            device_id=device_id,
            address=EMS_MODE_REGISTER,
            value=EMS_MODE_AUTO,
        )

        _verify_register(
            client=client,
            device_id=device_id,
            address=EMS_MODE_REGISTER,
            expected=EMS_MODE_AUTO,
        )

        _write_single_register(
            client=client,
            device_id=device_id,
            address=EMS_POWER_REGISTER,
            value=0,
        )

        _verify_register(
            client=client,
            device_id=device_id,
            address=EMS_POWER_REGISTER,
            expected=0,
        )

        return GoodweEmsResult(
            action="auto",
            requested_power_w=0,
            applied_power_w=0,
            ems_mode=EMS_MODE_AUTO,
            ems_power_register=0,
            verified=True,
        )

    if power_w <= 0:
        raise ValueError(
            "charge_grid vyzaduje "
            "kladny vykon."
        )

    try:
        #
        # Fyzicky overene poradi:
        # nejdrive vykon, potom aktivace
        # IMPORT_AC.
        #
        _write_single_register(
            client=client,
            device_id=device_id,
            address=EMS_POWER_REGISTER,
            value=power_w,
        )

        _verify_register(
            client=client,
            device_id=device_id,
            address=EMS_POWER_REGISTER,
            expected=power_w,
        )

        _write_single_register(
            client=client,
            device_id=device_id,
            address=EMS_MODE_REGISTER,
            value=EMS_MODE_CHARGE_FROM_GRID,
        )

        _verify_register(
            client=client,
            device_id=device_id,
            address=EMS_MODE_REGISTER,
            expected=EMS_MODE_CHARGE_FROM_GRID,
        )

    except Exception:
        #
        # FAIL-SAFE:
        # pri chybe se vzdy pokusime vratit AUTO.
        #
        #
        # Kazdy bezpecnostni zapis se zkousi
        # nezavisle. Selhani 47511 nesmi zabranit
        # pokusu o vynulovani 47512 a naopak.
        #
        try:
            _write_single_register(
                client=client,
                device_id=device_id,
                address=EMS_MODE_REGISTER,
                value=EMS_MODE_AUTO,
            )

        except Exception:
            pass

        try:
            _write_single_register(
                client=client,
                device_id=device_id,
                address=EMS_POWER_REGISTER,
                value=0,
            )

        except Exception:
            pass

        raise

    return GoodweEmsResult(
        action="charge_grid",
        requested_power_w=power_w,
        applied_power_w=power_w,
        ems_mode=EMS_MODE_CHARGE_FROM_GRID,
        ems_power_register=power_w,
        verified=True,
    )


def _select_goodwe_runtime_configuration(
    cloud_config: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(cloud_config, dict):
        raise RuntimeError(
            "Cloudova konfigurace neni dostupna."
        )

    runtime_configurations = cloud_config.get(
        "module_runtime_configurations"
    )

    if not isinstance(
        runtime_configurations,
        list,
    ):
        raise RuntimeError(
            "Cloudova konfigurace neobsahuje "
            "runtime konfigurace modulu."
        )

    matches = []

    for runtime in runtime_configurations:
        if not isinstance(runtime, dict):
            continue

        if (
            str(
                runtime.get("module_key")
                or ""
            ).strip().lower()
            == "photovoltaic"
            and str(
                runtime.get("manufacturer")
                or ""
            ).strip().lower()
            == "goodwe"
            and str(
                runtime.get("communication_type")
                or ""
            ).strip().lower()
            == "rs485"
        ):
            matches.append(runtime)

    if len(matches) != 1:
        raise RuntimeError(
            "GoodWe FVE runtime konfigurace "
            "nebyla nalezena jednoznacne."
        )

    runtime = matches[0]

    communicator_id = str(
        runtime.get("communicator_id")
        or ""
    ).strip()

    if not communicator_id:
        raise RuntimeError(
            "GoodWe runtime nema communicator_id."
        )

    device_id = runtime.get(
        "modbus_device_id"
    )

    _validate_device_id(
        device_id
    )

    return runtime


def execute_goodwe_ems_from_cloud_config(
    *,
    cloud_config: dict[str, Any],
    action: str,
    allowed_charge_power_w: int,
    target_soc_percent: float,
) -> GoodweEmsResult:
    """
    Provede fyzicke EMS rizeni pres stejnou
    RS485 cestu a stejny zamek jako telemetrie.

    DISCHARGE neni podporovan.
    Pred CHARGE_GRID znovu kontroluje lokalni
    SOC a BMS charge-current limit.
    """

    runtime = (
        _select_goodwe_runtime_configuration(
            cloud_config
        )
    )

    communicator_id = str(
        runtime["communicator_id"]
    ).strip()

    device_id = _validate_device_id(
        runtime["modbus_device_id"]
    )

    action = _validate_action(
        action
    )

    power_w = _validate_charge_power(
        allowed_charge_power_w
    )

    target_soc = float(
        target_soc_percent
    )

    if (
        target_soc < 0
        or target_soc > 100
    ):
        raise ValueError(
            "Target SOC je mimo rozsah 0 az 100 %."
        )

    _communicator, serial_path = (
        _find_communicator(
            communicator_id
        )
    )

    bus_lock = ziskej_zamek_modbus_sbernice(
        serial_path
    )

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
                raise RuntimeError(
                    "GoodWe EMS nelze otevrit "
                    "seriovy port."
                )

            current_mode = _read_single_register(
                client=client,
                device_id=device_id,
                address=EMS_MODE_REGISTER,
            )

            current_power = _read_single_register(
                client=client,
                device_id=device_id,
                address=EMS_POWER_REGISTER,
            )

            if action == "charge_grid":
                local_soc = _read_single_register(
                    client=client,
                    device_id=device_id,
                    address=37007,
                )

                charge_current_limit = (
                    _read_single_register(
                        client=client,
                        device_id=device_id,
                        address=37004,
                    )
                )

                if (
                    local_soc
                    >= target_soc
                ):
                    if (
                        current_mode
                        != EMS_MODE_AUTO
                        or current_power != 0
                    ):
                        apply_goodwe_ems_action(
                            client=client,
                            device_id=device_id,
                            action="auto",
                            allowed_charge_power_w=0,
                        )

                    raise RuntimeError(
                        "Lokalni SOC dosahl "
                        "cilove hodnoty."
                    )

                if charge_current_limit <= 0:
                    if (
                        current_mode
                        != EMS_MODE_AUTO
                        or current_power != 0
                    ):
                        apply_goodwe_ems_action(
                            client=client,
                            device_id=device_id,
                            action="auto",
                            allowed_charge_power_w=0,
                        )

                    raise RuntimeError(
                        "BMS nepovoluje nabijeci proud."
                    )

                desired_mode = (
                    EMS_MODE_CHARGE_FROM_GRID
                )

                desired_power = power_w

            else:
                desired_mode = EMS_MODE_AUTO
                desired_power = 0

            if (
                current_mode == desired_mode
                and current_power
                == desired_power
            ):
                return GoodweEmsResult(
                    action=action,
                    requested_power_w=power_w,
                    applied_power_w=power_w,
                    ems_mode=desired_mode,
                    ems_power_register=(
                        desired_power
                    ),
                    verified=True,
                    write_performed=False,
                )

            result = apply_goodwe_ems_action(
                client=client,
                device_id=device_id,
                action=action,
                allowed_charge_power_w=power_w,
            )

            return GoodweEmsResult(
                action=result.action,
                requested_power_w=(
                    result.requested_power_w
                ),
                applied_power_w=(
                    result.applied_power_w
                ),
                ems_mode=result.ems_mode,
                ems_power_register=(
                    result.ems_power_register
                ),
                verified=result.verified,
                write_performed=True,
            )

        finally:
            client.close()

def execute_goodwe_ems_with_client_factory(
    *,
    client_factory: Callable[[], Any],
    device_id: int,
    action: str,
    allowed_charge_power_w: int,
) -> GoodweEmsResult:
    """
    Wrapper pro pozdejsi realny Modbus klient.

    V G3 se testuje pouze s fake client_factory.
    """

    client = client_factory()

    if client is None:
        raise RuntimeError(
            "GoodWe client factory vratila None."
        )

    connected = client.connect()

    if connected is False:
        raise RuntimeError(
            "GoodWe Modbus klient se nepripojil."
        )

    try:
        return apply_goodwe_ems_action(
            client=client,
            device_id=device_id,
            action=action,
            allowed_charge_power_w=(
                allowed_charge_power_w
            ),
        )

    finally:
        close = getattr(
            client,
            "close",
            None,
        )

        if callable(close):
            close()
