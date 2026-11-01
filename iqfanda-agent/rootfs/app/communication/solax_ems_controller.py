"""Local SolaX X3-Hybrid G4 battery power control through the VPP interface.

Protocol sources (manufacturer authored):
* https://kb.solaxpower.com/it/solution/detail/40289fdf9279482a01927a1743c40230
* Hybrid-X1X3-G4 Modbus TCP/RTU V3.21, FC03/FC04 tables.

FC16 0x007c..0x008a is a WRITE command, not a holding-register mirror.
Mode 4 battery watts are positive for discharge, negative for charge. FC04
0x0100 confirms the selected remote mode; measured battery watts remain a
separate observation. AUTO disables our overlay without changing work mode,
schedules, reserve SOC or battery current settings in EEPROM.

RemoteCtrlTimeOut is NOT an unconditional mode-4 hardware watchdog: the
manufacturer starts it after a target completes. The local session supervisor
must enforce the deadline/SOC, poll this driver, and recover AUTO after restart.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable

from communication.battery_control_safety import BatteryControlSafetyStop


PROFILE_ID = "solax_x3_hybrid_g4"
EXPECTED_MODEL = "X3-Hybrid G4"
EXPECTED_DEVICE_ID = 1
MAXIMUM_CHARGE_POWER_W = 15000
MAXIMUM_DISCHARGE_POWER_W = 15000
MINIMUM_DISCHARGE_SOC_MARGIN_PERCENT = 2.0
REMOTE_TIMEOUT_SECONDS = 60
OWNER_MARKER_PATH = Path(os.getenv(
    "IQF_SOLAX_REMOTE_OWNER_PATH", "/config/solax_remote_control_owner.json"
))
SUPPORTED_ACTIONS = frozenset({"auto", "charge_grid", "discharge_grid"})


@dataclass(frozen=True)
class SolaxControlResult:
    action: str
    requested_power_w: int
    applied_power_w: int
    verified: bool
    write_performed: bool
    observed_soc_percent: float | None = None
    observed_battery_power_w: int | None = None
    observation_phase: str = "before_command"
    verification_scope: str = "accepted_write_and_remote_mode_readback"
    hardware_watchdog_verified: bool = False


def _s16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def _s32_words(value: int) -> list[int]:
    """Manufacturer uses low word first, signed two's complement."""
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        raise ValueError("SolaX signed power is out of range.")
    raw = value & 0xFFFFFFFF
    return [raw & 0xFFFF, raw >> 16]


def _ascii(words: list[int]) -> str:
    raw = b"".join(word.to_bytes(2, "big") for word in words)
    try:
        return raw.decode("ascii").strip(" \x00")
    except UnicodeDecodeError as exc:
        raise RuntimeError("SolaX returned invalid identity data.") from exc


def _remote_block(power_w: int | None) -> list[int]:
    # All 15 words are required; unused fields are explicitly zero. The timeout
    # is supplied as documented, but is not represented as a mode-4 watchdog.
    block = [0] * 15
    if power_w is not None:
        block[0] = 4
        block[1] = 1
        block[12] = REMOTE_TIMEOUT_SECONDS
        block[13:15] = _s32_words(power_w)
    return block


class _Transport:
    """Keep the manufacturer's minimum one-second command interval."""

    def __init__(self, client: Any, device_id: int, *,
                 sleeper: Callable[[float], None],
                 clock: Callable[[], float]) -> None:
        self.client = client
        self.device_id = device_id
        self.sleeper = sleeper
        self.clock = clock
        # The caller holds the bus lock, but the preceding telemetry reader
        # may have completed immediately before we acquired it.
        self.next_request = self.clock() + 1.0
        self.write_attempted = False

    def _call(self, method: Callable[..., Any], **kwargs: Any) -> Any:
        remaining = self.next_request - self.clock()
        if remaining > 0:
            self.sleeper(remaining)
        try:
            response = method(device_id=self.device_id, **kwargs)
        finally:
            self.next_request = self.clock() + 1.0
        if response is None or response.isError():
            raise RuntimeError("SolaX Modbus transaction failed.")
        return response

    def read(self, function_code: int, address: int, count: int = 1) -> list[int]:
        method = (self.client.read_input_registers if function_code == 4
                  else self.client.read_holding_registers)
        response = self._call(method, address=address, count=count)
        words = getattr(response, "registers", None)
        if (not isinstance(words, (list, tuple)) or len(words) != count
                or any(type(word) is not int or not 0 <= word <= 0xFFFF
                       for word in words)):
            raise RuntimeError("SolaX returned incomplete or invalid register data.")
        return list(words)

    def unlock(self) -> None:
        if self.read(4, 0x0054)[0] == 1:
            return
        self.write_attempted = True
        response = self._call(self.client.write_register, address=0, value=2014)
        if getattr(response, "address", None) != 0 or getattr(response, "registers", None) != [2014]:
            raise RuntimeError("SolaX did not acknowledge the unlock command.")
        if self.read(4, 0x0054)[0] != 1:
            raise RuntimeError("SolaX controls did not unlock.")

    def remote(self, power_w: int | None) -> None:
        self.write_attempted = True
        response = self._call(self.client.write_registers, address=0x007C,
                              values=_remote_block(power_w))
        if (getattr(response, "address", None) != 0x007C
                or getattr(response, "count", None) != 15):
            raise RuntimeError("SolaX did not acknowledge the complete VPP command.")
        expected_mode = 0 if power_w is None else 4
        if self.read(4, 0x0100)[0] != expected_mode:
            raise RuntimeError("SolaX remote mode readback does not match the command.")

    def relock(self) -> None:
        if self.read(4, 0x0054)[0] == 0:
            return
        self.write_attempted = True
        # The vendor documents that a value other than the unlock password
        # locks parameter writes again. No EEPROM settings are changed.
        response = self._call(self.client.write_register, address=0, value=0)
        if getattr(response, "address", None) != 0 or getattr(response, "registers", None) != [0]:
            raise RuntimeError("SolaX did not acknowledge restoring the parameter lock.")
        if self.read(4, 0x0054)[0] != 0:
            raise RuntimeError("SolaX parameter lock was not restored.")


def _read_marker(path: Path, *, serial: str, transport_id: str,
                 device_id: int) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("SolaX control owner marker cannot be read.") from exc
    if (not isinstance(marker, dict) or marker.get("schema_version") != 1
            or marker.get("profile_id") != PROFILE_ID
            or marker.get("serial") != serial
            or marker.get("transport_id") != transport_id
            or marker.get("device_id") != device_id
            or type(marker.get("original_work_mode")) is not int
            or marker["original_work_mode"] not in (0, 1, 2)
            or type(marker.get("original_lock")) is not int
            or marker["original_lock"] not in (0, 1)
            or type(marker.get("remote_command_attempted")) is not bool):
        raise RuntimeError("SolaX control owner marker does not match this inverter.")
    return marker


def _save_marker(path: Path, marker: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", delete=False) as stream:
            temporary = stream.name
            os.chmod(temporary, 0o600)
            json.dump(marker, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _restore_auto(io: _Transport, marker: dict[str, Any], path: Path) -> None:
    mode = io.read(4, 0x0100)[0]
    if mode not in (0, 4):
        raise RuntimeError("SolaX remote mode belongs to a different controller; restoration stopped.")
    if mode == 4 and not marker["remote_command_attempted"]:
        raise RuntimeError("SolaX remote mode appeared before our first command; restoration stopped.")
    # Also cancel when the current read says zero: an earlier timed-out or
    # partially acknowledged command may still be completing in the inverter.
    # Only our durable, identity-checked marker authorizes this cancellation.
    if marker["remote_command_attempted"]:
        io.unlock()
        io.remote(None)
    # Mode 0 returns to the inverter's prior mode. Do not guess a work-mode
    # write register or overwrite an operator's intervening configuration.
    if io.read(3, 0x008B)[0] != marker["original_work_mode"]:
        raise RuntimeError("SolaX original work mode could not be verified after AUTO.")
    if marker["original_lock"] == 0:
        io.relock()
    elif io.read(4, 0x0054)[0] != 1:
        io.unlock()
    path.unlink(missing_ok=True)


def _identity(io: _Transport) -> str:
    identity = io.read(3, 0, 21)
    serial = _ascii(identity[:7])
    manufacturer = _ascii(identity[7:14])
    model = _ascii(identity[14:21])
    if (not serial or not serial.isalnum() or manufacturer.casefold() != "solax"
            or model != EXPECTED_MODEL):
        raise RuntimeError("SolaX control requires an identified X3-Hybrid G4 inverter.")
    firmware = io.read(3, 0x007D, 7)
    # Vendor VPP compatibility table: X3 G4 ARM 1.40 / DSP 1.41 or newer
    # within the documented major version. A different major needs review.
    if (firmware[2] != 1 or firmware[3] != 1
            or not 41 <= firmware[0] <= 99 or not 40 <= firmware[6] <= 99):
        raise RuntimeError("SolaX firmware does not meet the documented G4 VPP requirements.")
    return serial


def _finite_percent(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"SolaX {name} must be a finite SOC percentage.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"SolaX {name} is invalid.") from exc
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError(f"SolaX {name} must be between 0 and 100 percent.")
    return number


def _preflight(io: _Transport, *, action: str, requested_power_w: int,
               target_soc_percent: float, minimum_discharge_soc_percent: float,
               settings: list[int]) -> tuple[int, float, int]:
    live = io.read(4, 0x0014, 18)
    soc = float(live[8])
    if not 0 <= soc <= 100:
        raise BatteryControlSafetyStop("invalid_battery_soc")
    if settings[2] != 1 or live[3] != 1:
        raise BatteryControlSafetyStop("battery_bms_not_connected", observed_soc_percent=soc)
    if live[6] != 0:
        raise BatteryControlSafetyStop("inverter_not_on_grid", observed_soc_percent=soc)
    faults = io.read(4, 0x003E, 8)
    if any(faults[index] for index in (0, 1, 2, 3, 5, 6, 7)):
        raise BatteryControlSafetyStop("inverter_or_bms_fault", observed_soc_percent=soc)
    # FC04 VPP bounds are read-only limits computed by the inverter.
    upper, lower = io.read(4, 0x011C, 2)
    if not 0 <= lower < upper <= 100:
        raise BatteryControlSafetyStop("invalid_inverter_soc_limits", observed_soc_percent=soc)
    original_mode = settings[0]
    # Respect the configured reserve of the original work mode as well as the
    # live VPP limit. These FC03 packed fields differ by work mode.
    reserve = (settings[8] >> 8 if original_mode == 0
               else settings[10] & 0xFF if original_mode == 1
               else settings[11] & 0xFF)
    if not 0 <= reserve <= 100:
        raise BatteryControlSafetyStop("invalid_configured_soc_reserve", observed_soc_percent=soc)
    if action == "charge_grid":
        if soc >= min(target_soc_percent, float(upper)):
            raise BatteryControlSafetyStop("target_soc_reached", observed_soc_percent=soc)
        current_limit_raw = live[16]
        maximum_power_w = MAXIMUM_CHARGE_POWER_W
    else:
        floor = max(minimum_discharge_soc_percent, float(lower), float(reserve))
        if soc <= floor + MINIMUM_DISCHARGE_SOC_MARGIN_PERCENT:
            raise BatteryControlSafetyStop("minimum_soc_reached", observed_soc_percent=soc)
        current_limit_raw = live[17]
        maximum_power_w = MAXIMUM_DISCHARGE_POWER_W
    voltage = _s16(live[0]) / 10.0
    if voltage <= 0 or current_limit_raw in (0, 0xFFFF):
        raise BatteryControlSafetyStop("bms_power_limit_unavailable", observed_soc_percent=soc)
    # Both voltage and permitted current come from this fresh battery read.
    bms_limit_w = int(voltage * (current_limit_raw / 10.0))
    applied_w = min(requested_power_w, bms_limit_w, maximum_power_w)
    if applied_w <= 0:
        raise BatteryControlSafetyStop("battery_power_not_permitted", observed_soc_percent=soc)
    # Telemetry positive means charge; the application's convention is the
    # opposite. Do not confuse this with the VPP write sign.
    observed_w = -_s16(live[2])
    return applied_w, soc, observed_w


def apply_solax_x3_g4_action(
    *, client: Any, device_id: int, action: str, allowed_charge_power_w: int,
    target_soc_percent: float, allowed_discharge_power_w: int = 0,
    minimum_discharge_soc_percent: float = 20.0,
    marker_path: Path = OWNER_MARKER_PATH, transport_id: str = "test-client",
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> SolaxControlResult:
    """Execute one local session tick; caller must own the serial bus lock.

    ``verified`` confirms the acknowledged VPP block and mode readback only.
    It does not assert that physical battery power has reached the setpoint.
    """
    if type(device_id) is not int or device_id != EXPECTED_DEVICE_ID:
        raise ValueError("SolaX G4 control is restricted to configured slave 1.")
    if action not in SUPPORTED_ACTIONS:
        raise ValueError("Unsupported SolaX battery action.")
    requested = (allowed_charge_power_w if action == "charge_grid"
                 else allowed_discharge_power_w if action == "discharge_grid" else 0)
    if action != "auto":
        if type(requested) is not int or requested <= 0:
            raise ValueError("SolaX requested battery power must be a positive integer.")
        target_soc_percent = _finite_percent(target_soc_percent, "target_soc_percent")
        minimum_discharge_soc_percent = _finite_percent(
            minimum_discharge_soc_percent, "minimum_discharge_soc_percent")
    io = _Transport(client, device_id, sleeper=sleeper, clock=clock)
    serial = _identity(io)
    marker_path = Path(marker_path)
    marker = _read_marker(marker_path, serial=serial, transport_id=transport_id,
                          device_id=device_id)
    remote_mode = io.read(4, 0x0100)[0]
    if remote_mode not in (0, 4) or (remote_mode == 4 and marker is None):
        raise RuntimeError("SolaX remote control is already owned by another controller.")
    if action == "auto":
        if marker is not None:
            _restore_auto(io, marker, marker_path)
        return SolaxControlResult(action, 0, 0, True, io.write_attempted,
                                   verification_scope="remote_disabled_and_original_mode")
    settings = io.read(3, 0x008B, 12)
    if settings[0] not in (0, 1, 2):
        raise RuntimeError("SolaX inverter is already in a separate manual work mode.")
    if marker is not None and settings[0] != marker["original_work_mode"]:
        raise RuntimeError("SolaX work mode changed during the manual session.")
    applied, soc, observed = _preflight(io, action=action, requested_power_w=requested,
        target_soc_percent=target_soc_percent,
        minimum_discharge_soc_percent=minimum_discharge_soc_percent, settings=settings)
    if marker is None:
        lock_state = io.read(4, 0x0054)[0]
        if lock_state not in (0, 1):
            raise RuntimeError("SolaX returned an invalid parameter lock state.")
        marker = {"schema_version": 1, "profile_id": PROFILE_ID, "serial": serial,
                  "device_id": device_id, "transport_id": transport_id,
                  "original_work_mode": settings[0], "original_lock": lock_state,
                  "remote_command_attempted": False}
        # Durable ownership must exist before the first possible physical write.
        _save_marker(marker_path, marker)
    try:
        io.unlock()
        if not marker["remote_command_attempted"]:
            # Persist the uncertain-command phase BEFORE writing. Recovery
            # can distinguish a failed unlock from a possibly applied VPP
            # command even after a power loss or a lost acknowledgement.
            marker = {**marker, "remote_command_attempted": True}
            _save_marker(marker_path, marker)
        io.remote(-applied if action == "charge_grid" else applied)
    except Exception as exc:
        try:
            _restore_auto(io, marker, marker_path)
        except Exception as restore_exc:
            # Retain the marker for the local supervisor's STOP/restart recovery.
            raise RuntimeError(
                f"SolaX command failed; AUTO recovery is pending: {restore_exc}"
            ) from exc
        raise
    return SolaxControlResult(action, requested, applied, True, io.write_attempted,
                               observed_soc_percent=soc, observed_battery_power_w=observed)


def _select_runtime(cloud_config: dict[str, Any]) -> dict[str, Any]:
    runtimes = cloud_config.get("module_runtime_configurations") if isinstance(cloud_config, dict) else None
    if not isinstance(runtimes, list):
        raise RuntimeError("SolaX module runtime configuration is missing.")
    matches = [item for item in runtimes if isinstance(item, dict)
               and str(item.get("module_key", "")).lower() == "photovoltaic"
               and str(item.get("manufacturer", "")).lower() == "solax"
               and (item.get("model") or item.get("identified_model")) == EXPECTED_MODEL]
    if len(matches) != 1:
        raise RuntimeError("SolaX X3-Hybrid G4 runtime is ambiguous or missing.")
    runtime = matches[0]
    if (runtime.get("communication_type") != "rs485"
            or not str(runtime.get("communicator_id") or "").strip()
            or runtime.get("modbus_device_id", runtime.get("matched_device_id")) not in (1, "1")):
        raise RuntimeError("SolaX control requires its RS485 communicator and slave 1.")
    profile = runtime.get("profile_id") or runtime.get("inverter_profile_id")
    if profile and profile != PROFILE_ID:
        raise RuntimeError("SolaX control profile does not match the selected model.")
    return runtime


def execute_solax_x3_g4_from_cloud_config(
    *, cloud_config: dict[str, Any], action: str, allowed_charge_power_w: int,
    target_soc_percent: float, allowed_discharge_power_w: int = 0,
    minimum_discharge_soc_percent: float = 20.0,
) -> SolaxControlResult:
    from pymodbus.client import ModbusSerialClient
    from communication.goodwe_probe import _find_communicator
    from communication.modbus_bus_lock import ziskej_zamek_modbus_sbernice

    runtime = _select_runtime(cloud_config)
    _communicator, serial_path = _find_communicator(str(runtime["communicator_id"]).strip())
    with ziskej_zamek_modbus_sbernice(serial_path):
        client = ModbusSerialClient(port=serial_path, baudrate=19200, bytesize=8,
                                   parity="N", stopbits=1, timeout=1.0, retries=0)
        try:
            if not client.connect():
                raise RuntimeError("SolaX control cannot open the serial port.")
            return apply_solax_x3_g4_action(client=client, device_id=1, action=action,
                allowed_charge_power_w=allowed_charge_power_w,
                target_soc_percent=target_soc_percent,
                allowed_discharge_power_w=allowed_discharge_power_w,
                minimum_discharge_soc_percent=minimum_discharge_soc_percent,
                marker_path=OWNER_MARKER_PATH, transport_id=str(serial_path))
        finally:
            # Keep the lock through the last required quiet interval so the
            # next telemetry reader cannot immediately collide with control.
            time.sleep(1.0)
            client.close()
