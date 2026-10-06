"""FoxESS H3 protocol-115 bidirectional battery control driver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import time
from pathlib import Path
import tempfile
from typing import Any

from pymodbus.client import ModbusSerialClient

from communication.goodwe_probe import _find_communicator
from communication.modbus_bus_lock import ziskej_zamek_modbus_sbernice


PROFILE_ID = "foxess_h3_10_0_e"
EXPECTED_MODEL = "H3-10.0-E"
EXPECTED_DEVICE_ID = 247
EXPECTED_PROTOCOL_VERSION = 115

PROTOCOL_VERSION_REGISTER = 30028
WORK_MODE_REGISTER = 41000
MAX_SOC_REGISTER = 41010
REMOTE_ENABLE_REGISTER = 44000
REMOTE_TIMEOUT_REGISTER = 44001
REMOTE_ACTIVE_POWER_REGISTER = 44002
BATTERY_POWER_REGISTER = 31036
BATTERY_SOC_REGISTER = 31038

WORK_MODE_SELF_USE = 0
WORK_MODE_FEED_IN_FIRST = 1
WORK_MODE_BACK_UP = 2

REMOTE_TIMEOUT_SECONDS = 180
MAXIMUM_CHARGE_POWER_W = 5000
MAXIMUM_DISCHARGE_POWER_W = 10000
MINIMUM_DISCHARGE_SOC_MARGIN_PERCENT = 2.0

MODBUS_READ_ATTEMPTS = 3
MODBUS_READ_RETRY_DELAY_SECONDS = 0.35
MODBUS_WRITE_ATTEMPTS = 3
MODBUS_WRITE_SETTLE_SECONDS = 0.45
MODBUS_POST_VERIFY_SETTLE_SECONDS = 0.20

SUPPORTED_ACTIONS = {
    "auto",
    "charge_grid",
    "discharge_grid",
}

OWNER_MARKER_PATH = Path(
    os.getenv(
        "IQF_FOXESS_REMOTE_OWNER_PATH",
        "/config/foxess_remote_control_owner.json",
    )
)


@dataclass(frozen=True)
class FoxessH3ControlResult:
    action: str
    requested_power_w: int
    applied_power_w: int
    verified: bool
    write_performed: bool


def _decode_s16(value: int) -> int:
    raw = int(value) & 0xFFFF
    return raw - 0x10000 if raw & 0x8000 else raw


def _decode_s32(
    high_word: int,
    low_word: int,
) -> int:
    value = (
        ((int(high_word) & 0xFFFF) << 16)
        | (int(low_word) & 0xFFFF)
    )

    if value & 0x80000000:
        return value - 0x100000000

    return value


def _encode_s32(value: int) -> tuple[int, int]:
    normalized = int(value)

    if (
        normalized < -0x80000000
        or normalized > 0x7FFFFFFF
    ):
        raise ValueError(
            "FoxESS signed32 setpoint je mimo rozsah."
        )

    raw = normalized & 0xFFFFFFFF

    return (
        (raw >> 16) & 0xFFFF,
        raw & 0xFFFF,
    )


def _read_registers(
    *,
    client: Any,
    device_id: int,
    address: int,
    count: int,
) -> list[int]:
    last_error: Exception | None = None

    for attempt in range(
        1,
        MODBUS_READ_ATTEMPTS + 1,
    ):
        if attempt > 1:
            time.sleep(
                MODBUS_READ_RETRY_DELAY_SECONDS
            )

        try:
            response = client.read_holding_registers(
                address=address,
                count=count,
                device_id=device_id,
            )

            if response is None:
                raise RuntimeError(
                    f"FoxESS read {address}/{count} vratil None."
                )

            is_error = getattr(
                response,
                "isError",
                None,
            )

            if callable(is_error) and is_error():
                raise RuntimeError(
                    f"FoxESS read {address}/{count} vratil chybu."
                )

            registers = getattr(
                response,
                "registers",
                None,
            )

            if (
                not isinstance(registers, list)
                or len(registers) != count
            ):
                raise RuntimeError(
                    f"FoxESS read {address}/{count} nema platny vysledek."
                )

            return [
                int(value) & 0xFFFF
                for value in registers
            ]

        except Exception as exc:
            last_error = exc

            if attempt >= MODBUS_READ_ATTEMPTS:
                raise

    raise RuntimeError(
        f"FoxESS read {address}/{count} selhal."
    ) from last_error


def _read_one(
    *,
    client: Any,
    device_id: int,
    address: int,
) -> int:
    return _read_registers(
        client=client,
        device_id=device_id,
        address=address,
        count=1,
    )[0]


def _read_s16(
    *,
    client: Any,
    device_id: int,
    address: int,
) -> int:
    return _decode_s16(
        _read_one(
            client=client,
            device_id=device_id,
            address=address,
        )
    )


def _read_s32(
    *,
    client: Any,
    device_id: int,
    address: int,
) -> int:
    values = _read_registers(
        client=client,
        device_id=device_id,
        address=address,
        count=2,
    )

    return _decode_s32(
        values[0],
        values[1],
    )


def _write_one(
    *,
    client: Any,
    device_id: int,
    address: int,
    value: int,
) -> None:
    expected = int(value) & 0xFFFF
    last_error: Exception | None = None

    for attempt in range(
        1,
        MODBUS_WRITE_ATTEMPTS + 1,
    ):
        if attempt > 1:
            time.sleep(
                MODBUS_READ_RETRY_DELAY_SECONDS
            )

        write_error: Exception | None = None

        try:
            response = client.write_register(
                address=address,
                value=expected,
                device_id=device_id,
            )

            if response is None:
                raise RuntimeError(
                    f"FoxESS write {address} vratil None."
                )

            is_error = getattr(
                response,
                "isError",
                None,
            )

            if callable(is_error) and is_error():
                raise RuntimeError(
                    f"FoxESS write {address} selhal."
                )

        except Exception as exc:
            #
            # FoxESS H3 protocol 115 muze zapis fyzicky prijmout,
            # ale neodpovedet na Modbus write response. Po settle
            # intervalu proto vzdy overime skutecny registr.
            #
            write_error = exc

        time.sleep(
            MODBUS_WRITE_SETTLE_SECONDS
        )

        try:
            actual = _read_one(
                client=client,
                device_id=device_id,
                address=address,
            )
        except Exception as exc:
            last_error = exc

            if attempt >= MODBUS_WRITE_ATTEMPTS:
                if write_error is not None:
                    raise RuntimeError(
                        f"FoxESS write/readback {address} selhal "
                        f"po {MODBUS_WRITE_ATTEMPTS} pokusech."
                    ) from write_error
                raise

            continue

        if actual == expected:
            time.sleep(
                MODBUS_POST_VERIFY_SETTLE_SECONDS
            )
            return

        last_error = RuntimeError(
            f"FoxESS registr {address}: "
            f"ocekavano {expected}, nacteno {actual}."
        )

        if attempt >= MODBUS_WRITE_ATTEMPTS:
            raise last_error

    raise RuntimeError(
        f"FoxESS write {address} selhal."
    ) from last_error


def _write_s16(
    *,
    client: Any,
    device_id: int,
    address: int,
    value: int,
) -> None:
    normalized = int(value)

    if (
        normalized < -0x8000
        or normalized > 0x7FFF
    ):
        raise ValueError(
            "FoxESS signed16 setpoint je mimo rozsah."
        )

    #
    # _write_one potvrzuje presny 16bit raw obraz hodnoty.
    # Dalsi okamzity readback zde zbytecne zvysoval pocet
    # RTU transakci a u H3 protocol 115 zhorsoval stabilitu.
    #
    _write_one(
        client=client,
        device_id=device_id,
        address=address,
        value=normalized,
    )


def _write_s32(
    *,
    client: Any,
    device_id: int,
    address: int,
    value: int,
) -> None:
    high, low = _encode_s32(
        value
    )

    response = client.write_registers(
        address=address,
        values=[
            high,
            low,
        ],
        device_id=device_id,
    )

    if response is None:
        raise RuntimeError(
            f"FoxESS write32 {address} vratil None."
        )

    is_error = getattr(
        response,
        "isError",
        None,
    )

    if callable(is_error) and is_error():
        raise RuntimeError(
            f"FoxESS write32 {address} selhal."
        )

    actual = _read_s32(
        client=client,
        device_id=device_id,
        address=address,
    )

    if actual != int(value):
        raise RuntimeError(
            f"FoxESS registr {address}/2 readback nesouhlasi."
        )


def _read_marker(
    marker_path: Path,
) -> dict[str, Any] | None:
    if not marker_path.exists():
        return None

    try:
        payload = json.loads(
            marker_path.read_text(
                encoding="utf-8-sig"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            "FoxESS owner marker nelze nacist."
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            "FoxESS owner marker nema platny format."
        )

    if (
        payload.get("schema_version") != 1
        or payload.get("owner") != "tng_iq_fanda"
        or payload.get("profile_id") != PROFILE_ID
        or int(
            payload.get("device_id")
            or 0
        )
        != EXPECTED_DEVICE_ID
    ):
        raise RuntimeError(
            "FoxESS owner marker neodpovida tomuto driveru."
        )

    return payload


def _write_marker(
    marker_path: Path,
    payload: dict[str, Any],
) -> None:
    marker_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    descriptor, temp_name = (
        tempfile.mkstemp(
            prefix="foxess-owner-",
            suffix=".tmp",
            dir=marker_path.parent,
        )
    )

    temp_path = Path(
        temp_name
    )

    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        os.replace(
            temp_path,
            marker_path,
        )

    except Exception:
        temp_path.unlink(
            missing_ok=True
        )
        raise


def _remove_marker(
    marker_path: Path,
) -> None:
    marker_path.unlink(
        missing_ok=True
    )


def _validate_protocol(
    *,
    client: Any,
    device_id: int,
) -> None:
    protocol_version = _read_one(
        client=client,
        device_id=device_id,
        address=PROTOCOL_VERSION_REGISTER,
    )

    if (
        protocol_version
        != EXPECTED_PROTOCOL_VERSION
    ):
        raise RuntimeError(
            "FoxESS battery control je povolen "
            "pouze pro Modbus protocol 115."
        )


def _validate_action(
    action: str,
) -> str:
    normalized = str(
        action
        or ""
    ).strip()

    if normalized not in SUPPORTED_ACTIONS:
        raise ValueError(
            "FoxESS H3 podporuje auto, charge_grid a discharge_grid."
        )

    return normalized


def _validate_charge_power(
    value: int,
) -> int:
    power = int(
        value
    )

    if power < 0:
        raise ValueError(
            "FoxESS nabijeci vykon nesmi byt zaporny."
        )

    if power > MAXIMUM_CHARGE_POWER_W:
        raise ValueError(
            "FoxESS nabijeci vykon prekrocil 5000 W."
        )

    return power


def _validate_discharge_power(
    value: int,
) -> int:
    power = int(
        value
    )

    if power < 0:
        raise ValueError(
            "FoxESS vybijeci vykon nesmi byt zaporny."
        )

    if power > MAXIMUM_DISCHARGE_POWER_W:
        raise ValueError(
            "FoxESS vybijeci vykon prekrocil maximalni vykon menice 10000 W."
        )

    return power


def _restore_auto(
    *,
    client: Any,
    device_id: int,
    marker_path: Path,
    marker: dict[str, Any],
) -> bool:
    writes = False

    active_power = _read_s16(
        client=client,
        device_id=device_id,
        address=REMOTE_ACTIVE_POWER_REGISTER,
    )

    if active_power != 0:
        _write_s16(
            client=client,
            device_id=device_id,
            address=REMOTE_ACTIVE_POWER_REGISTER,
            value=0,
        )
        writes = True

    remote_enable = _read_one(
        client=client,
        device_id=device_id,
        address=REMOTE_ENABLE_REGISTER,
    )

    if remote_enable != 0:
        _write_one(
            client=client,
            device_id=device_id,
            address=REMOTE_ENABLE_REGISTER,
            value=0,
        )
        writes = True

    original_timeout = int(
        marker.get(
            "original_timeout",
            REMOTE_TIMEOUT_SECONDS,
        )
    )

    current_timeout = _read_one(
        client=client,
        device_id=device_id,
        address=REMOTE_TIMEOUT_REGISTER,
    )

    if current_timeout != original_timeout:
        _write_one(
            client=client,
            device_id=device_id,
            address=REMOTE_TIMEOUT_REGISTER,
            value=original_timeout,
        )
        writes = True

    original_work_mode = int(
        marker.get(
            "original_work_mode",
            WORK_MODE_SELF_USE,
        )
    )

    if original_work_mode not in {
        WORK_MODE_SELF_USE,
        WORK_MODE_FEED_IN_FIRST,
        WORK_MODE_BACK_UP,
    }:
        raise RuntimeError(
            "FoxESS owner marker ma neplatny puvodni work mode."
        )

    current_work_mode = _read_one(
        client=client,
        device_id=device_id,
        address=WORK_MODE_REGISTER,
    )

    if current_work_mode != original_work_mode:
        _write_one(
            client=client,
            device_id=device_id,
            address=WORK_MODE_REGISTER,
            value=original_work_mode,
        )
        writes = True

    _remove_marker(
        marker_path
    )

    return writes


def apply_foxess_h3_action(
    *,
    client: Any,
    device_id: int,
    action: str,
    allowed_charge_power_w: int,
    target_soc_percent: float,
    allowed_discharge_power_w: int = 0,
    minimum_discharge_soc_percent: float = 20.0,
    marker_path: Path = OWNER_MARKER_PATH,
) -> FoxessH3ControlResult:
    if int(device_id) != EXPECTED_DEVICE_ID:
        raise ValueError(
            "FoxESS driver vyzaduje slave 247."
        )

    action = _validate_action(
        action
    )

    charge_power_w = _validate_charge_power(
        allowed_charge_power_w
    )
    discharge_power_w = _validate_discharge_power(
        allowed_discharge_power_w
    )

    target_soc = float(
        target_soc_percent
    )
    minimum_discharge_soc = float(
        minimum_discharge_soc_percent
    )

    if (
        target_soc < 0
        or target_soc > 100
    ):
        raise ValueError(
            "FoxESS target SOC je mimo 0 az 100 %."
        )

    if (
        minimum_discharge_soc < 10
        or minimum_discharge_soc > 100
    ):
        raise ValueError(
            "FoxESS minimum discharge SOC je mimo 10 az 100 %."
        )

    if action == "auto":
        if (
            charge_power_w != 0
            or discharge_power_w != 0
        ):
            raise ValueError(
                "FoxESS auto vyzaduje nulovy charge i discharge vykon."
            )
    elif action == "charge_grid":
        if discharge_power_w != 0:
            raise ValueError(
                "FoxESS charge_grid nesmi obsahovat discharge vykon."
            )
        if charge_power_w <= 0:
            raise ValueError(
                "FoxESS charge_grid vyzaduje kladny vykon."
            )
    elif action == "discharge_grid":
        if charge_power_w != 0:
            raise ValueError(
                "FoxESS discharge_grid nesmi obsahovat charge vykon."
            )
        if discharge_power_w <= 0:
            raise ValueError(
                "FoxESS discharge_grid vyzaduje kladny vykon."
            )

    _validate_protocol(
        client=client,
        device_id=device_id,
    )

    marker = _read_marker(
        marker_path
    )

    remote_enable = _read_one(
        client=client,
        device_id=device_id,
        address=REMOTE_ENABLE_REGISTER,
    )

    if action == "auto":
        if marker is None:
            return FoxessH3ControlResult(
                action="auto",
                requested_power_w=0,
                applied_power_w=0,
                verified=True,
                write_performed=False,
            )

        writes = _restore_auto(
            client=client,
            device_id=device_id,
            marker_path=marker_path,
            marker=marker,
        )

        return FoxessH3ControlResult(
            action="auto",
            requested_power_w=0,
            applied_power_w=0,
            verified=True,
            write_performed=writes,
        )

    if (
        remote_enable == 1
        and marker is None
    ):
        raise RuntimeError(
            "FoxESS remote control je aktivni mimo TNG IQ FANDA; "
            "driver jej neprevezme."
        )

    max_soc = _read_one(
        client=client,
        device_id=device_id,
        address=MAX_SOC_REGISTER,
    )

    local_soc = _read_one(
        client=client,
        device_id=device_id,
        address=BATTERY_SOC_REGISTER,
    )

    if (
        not 0 <= max_soc <= 100
        or not 0 <= local_soc <= 100
    ):
        raise RuntimeError(
            "FoxESS SOC readback je mimo rozsah."
        )

    if action == "charge_grid":
        effective_target_soc = min(
            target_soc,
            float(max_soc),
        )

        if local_soc >= effective_target_soc:
            if marker is not None:
                _restore_auto(
                    client=client,
                    device_id=device_id,
                    marker_path=marker_path,
                    marker=marker,
                )

            return FoxessH3ControlResult(
                action="auto",
                requested_power_w=charge_power_w,
                applied_power_w=0,
                verified=True,
                write_performed=(
                    marker is not None
                ),
            )

        selected_power_w = charge_power_w
        desired_active_power = -charge_power_w

    else:
        minimum_with_margin = (
            minimum_discharge_soc
            + MINIMUM_DISCHARGE_SOC_MARGIN_PERCENT
        )

        if local_soc <= minimum_with_margin:
            if marker is not None:
                _restore_auto(
                    client=client,
                    device_id=device_id,
                    marker_path=marker_path,
                    marker=marker,
                )

            raise RuntimeError(
                "FoxESS discharge safety guard zablokoval rizeni."
            )

        selected_power_w = discharge_power_w
        desired_active_power = discharge_power_w

    if marker is None:
        original_work_mode = _read_one(
            client=client,
            device_id=device_id,
            address=WORK_MODE_REGISTER,
        )

        original_timeout = _read_one(
            client=client,
            device_id=device_id,
            address=REMOTE_TIMEOUT_REGISTER,
        )

        if original_work_mode not in {
            WORK_MODE_SELF_USE,
            WORK_MODE_FEED_IN_FIRST,
            WORK_MODE_BACK_UP,
        }:
            raise RuntimeError(
                "FoxESS work mode nelze bezpecne prevzit."
            )

        marker = {
            "schema_version": 1,
            "owner": "tng_iq_fanda",
            "profile_id": PROFILE_ID,
            "device_id": EXPECTED_DEVICE_ID,
            "original_work_mode": original_work_mode,
            "original_timeout": original_timeout,
            "armed_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        _write_marker(
            marker_path,
            marker,
        )

    try:
        current_work_mode = _read_one(
            client=client,
            device_id=device_id,
            address=WORK_MODE_REGISTER,
        )

        if current_work_mode != WORK_MODE_BACK_UP:
            _write_one(
                client=client,
                device_id=device_id,
                address=WORK_MODE_REGISTER,
                value=WORK_MODE_BACK_UP,
            )

        current_timeout = _read_one(
            client=client,
            device_id=device_id,
            address=REMOTE_TIMEOUT_REGISTER,
        )

        if (
            current_timeout
            != REMOTE_TIMEOUT_SECONDS
        ):
            _write_one(
                client=client,
                device_id=device_id,
                address=REMOTE_TIMEOUT_REGISTER,
                value=REMOTE_TIMEOUT_SECONDS,
            )

        current_enable = _read_one(
            client=client,
            device_id=device_id,
            address=REMOTE_ENABLE_REGISTER,
        )

        if current_enable != 1:
            _write_one(
                client=client,
                device_id=device_id,
                address=REMOTE_ENABLE_REGISTER,
                value=1,
            )

        current_active_power = _read_s16(
            client=client,
            device_id=device_id,
            address=REMOTE_ACTIVE_POWER_REGISTER,
        )

        if not isinstance(
            current_active_power,
            int,
        ):
            raise RuntimeError(
                "FoxESS active power readback nema platny format."
            )

        #
        # Protocol-115 Remote Active Power is one signed 16-bit register:
        #   44002 < 0 = force charge/import,
        #   44002 > 0 = force discharge/export.
        #
        # 44003 is not part of this V1 active-power setpoint.
        # Read/write 44000, 44001 and 44002 individually because
        # multi-register reads can return invalid values on this family.
        #
        # Write on every intent because the FoxESS remote-control
        # watchdog is refreshed by the active-power command.
        #
        _write_s16(
            client=client,
            device_id=device_id,
            address=REMOTE_ACTIVE_POWER_REGISTER,
            value=desired_active_power,
        )

        if (
            _read_one(
                client=client,
                device_id=device_id,
                address=REMOTE_ENABLE_REGISTER,
            )
            != 1
        ):
            raise RuntimeError(
                "FoxESS remote_enable final readback selhal."
            )

        if (
            _read_s16(
                client=client,
                device_id=device_id,
                address=REMOTE_ACTIVE_POWER_REGISTER,
            )
            != desired_active_power
        ):
            raise RuntimeError(
                "FoxESS active power final readback selhal."
            )

    except Exception:
        try:
            _restore_auto(
                client=client,
                device_id=device_id,
                marker_path=marker_path,
                marker=marker,
            )
        except Exception:
            pass
        raise

    return FoxessH3ControlResult(
        action=action,
        requested_power_w=selected_power_w,
        applied_power_w=selected_power_w,
        verified=True,
        write_performed=True,
    )

def _select_runtime(
    cloud_config: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(
        cloud_config,
        dict,
    ):
        raise RuntimeError(
            "Cloudova konfigurace neni dostupna."
        )

    runtimes = cloud_config.get(
        "module_runtime_configurations"
    )

    if not isinstance(
        runtimes,
        list,
    ):
        raise RuntimeError(
            "Cloudova konfigurace nema runtime modulu."
        )

    matches = []

    for runtime in runtimes:
        if not isinstance(
            runtime,
            dict,
        ):
            continue

        if (
            str(
                runtime.get(
                    "module_key"
                )
                or ""
            ).strip().lower()
            != "photovoltaic"
        ):
            continue

        profile_id = str(
            runtime.get(
                "profile_id"
            )
            or runtime.get(
                "inverter_profile_id"
            )
            or ""
        ).strip()

        manufacturer = str(
            runtime.get(
                "manufacturer"
            )
            or ""
        ).strip().lower()

        model = str(
            runtime.get(
                "model"
            )
            or runtime.get(
                "identified_model"
            )
            or ""
        ).strip()

        communication_type = str(
            runtime.get(
                "communication_type"
            )
            or ""
        ).strip().lower()

        if (
            profile_id == PROFILE_ID
            or (
                manufacturer == "foxess"
                and model == EXPECTED_MODEL
            )
        ):
            if communication_type != "rs485":
                raise RuntimeError(
                    "FoxESS runtime nema RS485 transport."
                )

            matches.append(
                runtime
            )

    if len(matches) != 1:
        raise RuntimeError(
            "FoxESS H3 runtime nebyl nalezen jednoznacne."
        )

    runtime = matches[0]

    communicator_id = str(
        runtime.get(
            "communicator_id"
        )
        or ""
    ).strip()

    if not communicator_id:
        raise RuntimeError(
            "FoxESS runtime nema communicator_id."
        )

    device_id = (
        runtime.get(
            "modbus_device_id"
        )
    )

    if device_id is None:
        device_id = runtime.get(
            "matched_device_id"
        )

    if int(
        device_id
        or 0
    ) != EXPECTED_DEVICE_ID:
        raise RuntimeError(
            "FoxESS runtime nema slave 247."
        )

    return {
        **runtime,
        "modbus_device_id":
            EXPECTED_DEVICE_ID,
    }


def execute_foxess_h3_from_cloud_config(
    *,
    cloud_config: dict[str, Any],
    action: str,
    allowed_charge_power_w: int,
    target_soc_percent: float,
    allowed_discharge_power_w: int = 0,
    minimum_discharge_soc_percent: float = 20.0,
) -> FoxessH3ControlResult:
    runtime = _select_runtime(
        cloud_config
    )

    communicator_id = str(
        runtime[
            "communicator_id"
        ]
    ).strip()

    device_id = int(
        runtime[
            "modbus_device_id"
        ]
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
            timeout=1.5,
            retries=0,
        )

        try:
            if not client.connect():
                raise RuntimeError(
                    "FoxESS H3 control nelze otevrit seriovy port."
                )

            return apply_foxess_h3_action(
                client=client,
                device_id=device_id,
                action=action,
                allowed_charge_power_w=(
                    allowed_charge_power_w
                ),
                target_soc_percent=(
                    target_soc_percent
                ),
                allowed_discharge_power_w=(
                    allowed_discharge_power_w
                ),
                minimum_discharge_soc_percent=(
                    minimum_discharge_soc_percent
                ),
                marker_path=OWNER_MARKER_PATH,
            )

        finally:
            client.close()

