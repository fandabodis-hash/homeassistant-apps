"""Pylontech US5000 read-only telemetry over the Console RS232 port."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterator

import serial


PROFILE_ID = "pylontech_us5000_console"
COMMUNICATION_STATE_PATH = Path(
    os.getenv(
        "IQF_COMMUNICATION_STATE_PATH",
        "/config/communication.json",
    )
)

BAUDRATE = 115200
BYTESIZE = 8
PARITY = "N"
STOPBITS = 1

PYLONTECH_QUERY = b"pwr\r"
MAX_RESPONSE_BYTES = 65536


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(
            f"JSON {path} nema platny objekt."
        )

    return data


def _runtime_communication(
    runtime_configuration: dict[str, Any],
) -> dict[str, Any]:
    configuration = runtime_configuration.get(
        "configuration"
    )

    if not isinstance(configuration, dict):
        configuration = {}

    communication = configuration.get(
        "communication"
    )

    if not isinstance(communication, dict):
        communication = {}

    merged = dict(communication)

    top_level_id = str(
        runtime_configuration.get(
            "communicator_id"
        )
        or ""
    ).strip()

    if top_level_id:
        merged["communicator_id"] = top_level_id

    return merged


def _resolve_communicator(
    runtime_configuration: dict[str, Any],
) -> dict[str, Any]:
    communication = _runtime_communication(
        runtime_configuration
    )

    requested_id = str(
        communication.get("communicator_id")
        or ""
    ).strip()
    requested_serial = str(
        communication.get("serial_number")
        or ""
    ).strip()
    requested_path = str(
        communication.get("preferred_path")
        or ""
    ).strip()

    state = _read_json(
        COMMUNICATION_STATE_PATH
    )
    raw_communicators = state.get(
        "communicators"
    )

    if not isinstance(
        raw_communicators,
        list,
    ):
        raise RuntimeError(
            "Komunikacni stav nema komunikatory."
        )

    communicators = [
        item
        for item in raw_communicators
        if isinstance(item, dict)
    ]

    match_sets: list[list[dict[str, Any]]] = []

    if requested_id:
        match_sets.append(
            [
                item
                for item in communicators
                if str(
                    item.get("communicator_id")
                    or ""
                ).strip()
                == requested_id
            ]
        )

    if requested_serial:
        match_sets.append(
            [
                item
                for item in communicators
                if str(
                    item.get("serial_number")
                    or ""
                ).strip()
                == requested_serial
            ]
        )

    if requested_path:
        match_sets.append(
            [
                item
                for item in communicators
                if str(
                    item.get("preferred_path")
                    or ""
                ).strip()
                == requested_path
            ]
        )

    communicator = None

    for matches in match_sets:
        if len(matches) == 1:
            communicator = matches[0]
            break

    if communicator is None:
        raise RuntimeError(
            "RS232 komunikator baterie nebyl "
            "jednoznacne nalezen."
        )

    capabilities = communicator.get(
        "capabilities"
    )

    if not isinstance(capabilities, list):
        capabilities = []

    normalized_capabilities = {
        str(value).strip().lower()
        for value in capabilities
    }

    if "serial" not in normalized_capabilities:
        raise RuntimeError(
            "Komunikator baterie neni seriove zarizeni."
        )

    if communicator.get("connected") is not True:
        raise RuntimeError(
            "Komunikator baterie neni pripojen."
        )

    if communicator.get("status") != "ready":
        raise RuntimeError(
            "Komunikator baterie neni pripraven."
        )

    preferred_path = str(
        communicator.get("preferred_path")
        or ""
    ).strip()
    stable_paths = communicator.get(
        "stable_paths"
    )

    if not isinstance(stable_paths, list):
        stable_paths = []

    if (
        not preferred_path.startswith(
            "/dev/serial/by-id/"
        )
        or preferred_path not in stable_paths
    ):
        raise RuntimeError(
            "Komunikator baterie nema stabilni "
            "/dev/serial/by-id cestu."
        )

    if not Path(preferred_path).exists():
        raise RuntimeError(
            "Stabilni RS232 cesta baterie neexistuje."
        )

    return communicator


@contextmanager
def _exclusive_serial_port(
    serial_path: str,
) -> Iterator[None]:
    digest = hashlib.sha256(
        serial_path.encode("utf-8")
    ).hexdigest()[:24]

    lock_path = Path(
        "/tmp"
    ) / (
        "iqfanda-serial-"
        + digest
        + ".lock"
    )

    with lock_path.open(
        "a+",
        encoding="utf-8",
    ) as lock_file:
        fcntl.flock(
            lock_file.fileno(),
            fcntl.LOCK_EX,
        )

        try:
            yield
        finally:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_UN,
            )


def _read_console_response(
    *,
    serial_path: str,
) -> str:
    port = serial.Serial()

    port.port = serial_path
    port.baudrate = BAUDRATE
    port.bytesize = serial.EIGHTBITS
    port.parity = serial.PARITY_NONE
    port.stopbits = serial.STOPBITS_ONE
    port.timeout = 0.10
    port.write_timeout = 1.0
    port.xonxoff = False
    port.rtscts = False
    port.dsrdtr = False
    port.dtr = False
    port.rts = False

    data = bytearray()

    with _exclusive_serial_port(
        serial_path
    ):
        try:
            port.open()
            port.dtr = False
            port.rts = False
            port.reset_input_buffer()

            written = port.write(
                PYLONTECH_QUERY
            )
            port.flush()

            if written != len(
                PYLONTECH_QUERY
            ):
                raise RuntimeError(
                    "Pylontech diagnosticky dotaz "
                    "nebyl odeslan cely."
                )

            hard_deadline = (
                time.monotonic()
                + 4.0
            )
            idle_deadline = None

            while (
                time.monotonic()
                < hard_deadline
            ):
                waiting = port.in_waiting

                if waiting:
                    chunk = port.read(
                        min(
                            waiting,
                            MAX_RESPONSE_BYTES
                            - len(data),
                        )
                    )

                    data.extend(chunk)

                    if len(data) >= MAX_RESPONSE_BYTES:
                        raise RuntimeError(
                            "Pylontech odpoved prekrocila "
                            "bezpecny limit."
                        )

                    idle_deadline = (
                        time.monotonic()
                        + 0.45
                    )

                    if b"pylon>" in data:
                        break

                elif (
                    idle_deadline is not None
                    and time.monotonic()
                    >= idle_deadline
                ):
                    break

                time.sleep(0.03)

        finally:
            try:
                port.close()
            except Exception:
                pass

    if not data:
        raise RuntimeError(
            "Pylontech Console nevratila zadna data."
        )

    return data.decode(
        "utf-8",
        errors="replace",
    ).replace(
        "\x00",
        "",
    )


def _parse_int(
    value: str,
) -> int:
    return int(
        str(value).strip()
    )


def _parse_pwr_response(
    text: str,
) -> tuple[
    list[dict[str, Any]],
    list[int],
]:
    modules: list[dict[str, Any]] = []
    absent_ids: list[int] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        match = re.match(
            r"^(\d{1,2})\s+",
            line,
        )

        if match is None:
            continue

        module_id = int(
            match.group(1)
        )

        if not 1 <= module_id <= 16:
            continue

        if re.search(
            r"\bAbsent\b",
            line,
            flags=re.IGNORECASE,
        ):
            absent_ids.append(
                module_id
            )
            continue

        tokens = line.split()

        if len(tokens) < 24:
            continue

        try:
            voltage_v = (
                _parse_int(
                    tokens[1]
                )
                / 1000.0
            )
            current_a = (
                _parse_int(
                    tokens[2]
                )
                / 1000.0
            )
            temperature_c = (
                _parse_int(
                    tokens[3]
                )
                / 1000.0
            )
            temperature_low_c = (
                _parse_int(
                    tokens[4]
                )
                / 1000.0
            )
            temperature_high_c = (
                _parse_int(
                    tokens[6]
                )
                / 1000.0
            )
            cell_voltage_low_v = (
                _parse_int(
                    tokens[8]
                )
                / 1000.0
            )
            cell_voltage_high_v = (
                _parse_int(
                    tokens[10]
                )
                / 1000.0
            )
            soc_percent = float(
                tokens[16].rstrip("%")
            )
            mos_temperature_c = (
                _parse_int(
                    tokens[21]
                )
                / 1000.0
            )
        except (
            TypeError,
            ValueError,
            IndexError,
        ):
            continue

        statuses = {
            "base": tokens[12],
            "voltage": tokens[13],
            "current": tokens[14],
            "temperature": tokens[15],
            "battery_voltage": tokens[19],
            "battery_temperature": tokens[20],
            "mos_temperature": tokens[22],
            "system_alarm": tokens[23],
        }

        alarm_normal = (
            str(
                statuses[
                    "system_alarm"
                ]
            ).strip().lower()
            == "normal"
        )

        modules.append(
            {
                "module_id": module_id,
                "voltage_v": voltage_v,
                "current_a": current_a,
                "temperature_c": (
                    temperature_c
                ),
                "temperature_low_c": (
                    temperature_low_c
                ),
                "temperature_high_c": (
                    temperature_high_c
                ),
                "cell_voltage_low_v": (
                    cell_voltage_low_v
                ),
                "cell_voltage_high_v": (
                    cell_voltage_high_v
                ),
                "soc_percent": soc_percent,
                "mos_temperature_c": (
                    mos_temperature_c
                ),
                "statuses": statuses,
                "alarm_normal": (
                    alarm_normal
                ),
            }
        )

    modules.sort(
        key=lambda item: int(
            item["module_id"]
        )
    )
    absent_ids.sort()

    return modules, absent_ids


def _entity(
    key: str,
    category: str,
    name: str,
    value: Any,
    unit: str | None,
    value_type: str,
    *,
    quality: str = "good",
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "entity_key": key,
        "category": category,
        "name": name,
        "value": value,
        "unit": unit,
        "value_type": value_type,
        "quality": quality,
        "source_address": None,
        "attributes": (
            dict(attributes)
            if isinstance(
                attributes,
                dict,
            )
            else {}
        ),
    }


def _build_entities(
    *,
    modules: list[dict[str, Any]],
    expected_module_count: int,
) -> list[dict[str, Any]]:
    if not modules:
        return []

    quality = (
        "good"
        if len(modules)
        == expected_module_count
        else "partial"
    )

    voltages = [
        float(item["voltage_v"])
        for item in modules
    ]
    currents = [
        float(item["current_a"])
        for item in modules
    ]
    temperatures = [
        float(item["temperature_c"])
        for item in modules
    ]
    soc_values = [
        float(item["soc_percent"])
        for item in modules
    ]

    voltage_v = sum(
        voltages
    ) / len(voltages)
    current_a = sum(currents)
    power_w = (
        voltage_v
        * current_a
    )
    soc_min = min(soc_values)
    soc_average = (
        sum(soc_values)
        / len(soc_values)
    )
    temperature_average = (
        sum(temperatures)
        / len(temperatures)
    )
    temperature_max = max(
        max(
            float(item["temperature_c"]),
            float(item["temperature_high_c"]),
            float(item["mos_temperature_c"]),
        )
        for item in modules
    )
    alarm_count = sum(
        1
        for item in modules
        if item.get(
            "alarm_normal"
        )
        is not True
    )

    entities = [
        _entity(
            "baterie.soc",
            "baterie",
            "SOC baterie",
            round(soc_min, 2),
            "%",
            "number",
            quality=quality,
            attributes={
                "aggregation": "minimum",
            },
        ),
        _entity(
            "baterie.soc_prumer",
            "baterie",
            "Průměrný SOC baterie",
            round(
                soc_average,
                2,
            ),
            "%",
            "number",
            quality=quality,
            attributes={
                "aggregation": "average",
            },
        ),
        _entity(
            "baterie.napeti",
            "baterie",
            "Napětí baterie",
            round(voltage_v, 3),
            "V",
            "number",
            quality=quality,
            attributes={
                "aggregation": (
                    "module_average"
                ),
            },
        ),
        _entity(
            "baterie.proud",
            "baterie",
            "Proud baterie",
            round(current_a, 3),
            "A",
            "number",
            quality=quality,
            attributes={
                "aggregation": (
                    "module_sum"
                ),
            },
        ),
        _entity(
            "baterie.vykon",
            "baterie",
            "Výkon baterie",
            round(power_w, 1),
            "W",
            "number",
            quality=quality,
            attributes={
                "positive_direction": (
                    "discharge"
                ),
            },
        ),
        _entity(
            "baterie.teplota",
            "baterie",
            "Teplota baterie",
            round(
                temperature_average,
                1,
            ),
            "°C",
            "number",
            quality=quality,
        ),
        _entity(
            "baterie.teplota_max",
            "baterie",
            "Maximální teplota baterie",
            round(
                temperature_max,
                1,
            ),
            "°C",
            "number",
            quality=quality,
        ),
        _entity(
            "baterie.moduly.pritomno",
            "baterie",
            "Přítomné bateriové moduly",
            len(modules),
            None,
            "integer",
            quality=quality,
        ),
        _entity(
            "baterie.moduly.ocekavano",
            "baterie",
            "Očekávané bateriové moduly",
            expected_module_count,
            None,
            "integer",
            quality=quality,
        ),
        _entity(
            "baterie.alarmy",
            "baterie",
            "Počet aktivních alarmů baterie",
            alarm_count,
            None,
            "integer",
            quality=(
                "good"
                if alarm_count == 0
                else "warning"
            ),
        ),
    ]

    for item in modules:
        module_id = int(
            item["module_id"]
        )
        prefix = (
            "baterie.modul"
            + str(module_id)
        )
        module_quality = (
            "good"
            if item.get(
                "alarm_normal"
            )
            is True
            else "warning"
        )

        entities.extend(
            [
                _entity(
                    prefix + ".soc",
                    "baterie_modul",
                    (
                        "SOC baterie "
                        + str(module_id)
                    ),
                    round(
                        float(
                            item[
                                "soc_percent"
                            ]
                        ),
                        2,
                    ),
                    "%",
                    "number",
                    quality=module_quality,
                ),
                _entity(
                    prefix + ".napeti",
                    "baterie_modul",
                    (
                        "Napětí baterie "
                        + str(module_id)
                    ),
                    round(
                        float(
                            item[
                                "voltage_v"
                            ]
                        ),
                        3,
                    ),
                    "V",
                    "number",
                    quality=module_quality,
                ),
                _entity(
                    prefix + ".proud",
                    "baterie_modul",
                    (
                        "Proud baterie "
                        + str(module_id)
                    ),
                    round(
                        float(
                            item[
                                "current_a"
                            ]
                        ),
                        3,
                    ),
                    "A",
                    "number",
                    quality=module_quality,
                ),
                _entity(
                    prefix + ".teplota",
                    "baterie_modul",
                    (
                        "Teplota baterie "
                        + str(module_id)
                    ),
                    round(
                        float(
                            item[
                                "temperature_c"
                            ]
                        ),
                        1,
                    ),
                    "°C",
                    "number",
                    quality=module_quality,
                ),
                _entity(
                    prefix + ".alarm",
                    "baterie_modul",
                    (
                        "Alarm baterie "
                        + str(module_id)
                    ),
                    (
                        item.get(
                            "alarm_normal"
                        )
                        is not True
                    ),
                    None,
                    "boolean",
                    quality=module_quality,
                    attributes={
                        "statuses": dict(
                            item.get(
                                "statuses"
                            )
                            or {}
                        ),
                    },
                ),
            ]
        )

    return entities


def read_pylontech_snapshot(
    runtime_configuration: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(
        runtime_configuration,
        dict,
    ):
        raise TypeError(
            "Runtime baterie nema platny format."
        )

    if runtime_configuration.get(
        "read_only"
    ) is not True:
        raise ValueError(
            "Pylontech runtime neni read-only."
        )

    manufacturer = str(
        runtime_configuration.get(
            "manufacturer"
        )
        or ""
    ).strip().lower()
    model = str(
        runtime_configuration.get(
            "model"
        )
        or ""
    ).strip()

    if (
        manufacturer != "pylontech"
        or model != "US5000"
    ):
        raise ValueError(
            "Pylontech adapter podporuje pouze US5000."
        )

    communication_type = str(
        runtime_configuration.get(
            "communication_type"
        )
        or _runtime_communication(
            runtime_configuration
        ).get("type")
        or ""
    ).strip().lower()

    if communication_type != "rs232":
        raise ValueError(
            "Pylontech US5000 vyzaduje RS232."
        )

    configuration = runtime_configuration.get(
        "configuration"
    )

    if not isinstance(configuration, dict):
        configuration = {}

    expected_module_count = int(
        configuration.get(
            "module_count"
        )
        or runtime_configuration.get(
            "expected_module_count"
        )
        or 0
    )

    if not 1 <= expected_module_count <= 16:
        raise ValueError(
            "Pocet Pylontech modulu musi byt 1 az 16."
        )

    communicator = _resolve_communicator(
        runtime_configuration
    )
    communicator_id = str(
        communicator.get("communicator_id")
        or ""
    ).strip()
    communicator_serial = str(
        communicator.get("serial_number")
        or ""
    ).strip()
    serial_path = str(
        communicator.get("preferred_path")
        or ""
    ).strip()

    response_text = (
        _read_console_response(
            serial_path=serial_path,
        )
    )

    command_completed = (
        "Command completed successfully"
        in response_text
    )
    console_prompt = (
        "pylon>" in response_text
    )

    modules, absent_ids = (
        _parse_pwr_response(
            response_text
        )
    )

    detected_module_ids = [
        int(item["module_id"])
        for item in modules
    ]
    expected_ids = list(
        range(
            1,
            expected_module_count + 1,
        )
    )

    module_count_verified = (
        detected_module_ids
        == expected_ids
    )

    profile_verified = (
        command_completed
        and console_prompt
        and module_count_verified
    )

    entities = _build_entities(
        modules=modules,
        expected_module_count=(
            expected_module_count
        ),
    )

    return {
        "complete": profile_verified,
        "read_only": True,
        "diagnostic_query_only": True,
        "battery_configuration_write": False,
        "serial_tx": "diagnostic_query_only",
        "profile_id": PROFILE_ID,
        "manufacturer": "pylontech",
        "battery_model": "US5000",
        "communication_type": "rs232",
        "communicator_id": communicator_id,
        "communicator_serial_number": (
            communicator_serial
        ),
        "serial_path": serial_path,
        "baudrate": BAUDRATE,
        "query": "pwr",
        "command_completed": (
            command_completed
        ),
        "console_prompt": console_prompt,
        "detected_module_ids": (
            detected_module_ids
        ),
        "detected_module_count": (
            len(detected_module_ids)
        ),
        "absent_module_ids": absent_ids,
        "expected_module_count": (
            expected_module_count
        ),
        "module_count_verified": (
            module_count_verified
        ),
        "profile_verified": (
            profile_verified
        ),
        "telemetry_source": (
            "pylontech_us5000_rs232"
        ),
        "modules": modules,
        "entities": entities,
    }


def probe_pylontech_us5000(
    command_payload: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(
        command_payload,
        dict,
    ):
        raise TypeError(
            "Pylontech probe nema platny payload."
        )

    if (
        command_payload.get("read_only")
        is not True
        or command_payload.get(
            "diagnostic_query_only"
        )
        is not True
        or command_payload.get(
            "battery_configuration_write"
        )
        is not False
    ):
        raise ValueError(
            "Pylontech probe porusuje diagnosticky kontrakt."
        )

    expected_module_count = command_payload.get(
        "expected_module_count"
    )

    if type(expected_module_count) is not int:
        raise ValueError(
            "Pylontech probe nema pocet modulu."
        )

    runtime_configuration = {
        "module_key": "battery",
        "module_type": "battery",
        "manufacturer": str(
            command_payload.get(
                "manufacturer"
            )
            or ""
        ).strip().lower(),
        "model": str(
            command_payload.get(
                "battery_model"
            )
            or ""
        ).strip(),
        "communication_type": "rs232",
        "communicator_id": str(
            command_payload.get(
                "communicator_id"
            )
            or ""
        ).strip(),
        "expected_module_count": (
            expected_module_count
        ),
        "read_only": True,
        "configuration": {
            "module_count": (
                expected_module_count
            ),
            "communication": {
                "type": "rs232",
                "communicator_id": str(
                    command_payload.get(
                        "communicator_id"
                    )
                    or ""
                ).strip(),
                "serial_number": str(
                    command_payload.get(
                        "communicator_serial_number"
                    )
                    or ""
                ).strip()
                or None,
                "preferred_path": str(
                    command_payload.get(
                        "preferred_path"
                    )
                    or ""
                ).strip(),
            },
        },
    }

    return read_pylontech_snapshot(
        runtime_configuration
    )
