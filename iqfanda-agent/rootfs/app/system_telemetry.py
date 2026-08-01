from __future__ import annotations

import glob
import logging
import shutil
import socket
import time
from pathlib import Path
from typing import Any


DEFAULT_CPU_SAMPLE_SECONDS = 0.25
DEFAULT_INTERNET_TIMEOUT_SECONDS = 3.0

UPTIME_FILE = Path("/proc/uptime")
CPU_STAT_FILE = Path("/proc/stat")
MEMINFO_FILE = Path("/proc/meminfo")

TEMPERATURE_PATTERNS = (
    "/sys/class/thermal/thermal_zone*/temp",
    "/sys/devices/virtual/thermal/thermal_zone*/temp",
)

DISK_PATH_CANDIDATES = (
    Path("/data"),
    Path("/config"),
    Path("/"),
)

INTERNET_TARGETS = (
    ("api.tngiqfanda.cz", 443),
    ("1.1.1.1", 443),
)


def _round_number(
    value: float,
    digits: int = 1,
) -> float:
    return round(float(value), digits)


def _read_text(
    path: Path,
) -> str:
    return path.read_text(
        encoding="utf-8",
    ).strip()


def get_uptime_seconds() -> int | None:
    try:
        raw_value = _read_text(
            UPTIME_FILE
        ).split()[0]

        return max(
            0,
            int(float(raw_value)),
        )

    except (
        FileNotFoundError,
        IndexError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        logging.debug(
            "Uptime se nepodařilo načíst: %s",
            exc,
        )

        return None


def _read_cpu_times() -> tuple[int, int] | None:
    try:
        first_line = _read_text(
            CPU_STAT_FILE
        ).splitlines()[0]

        parts = first_line.split()

        if not parts or parts[0] != "cpu":
            return None

        values = [
            int(value)
            for value in parts[1:]
        ]

        if len(values) < 4:
            return None

        idle = values[3]

        if len(values) > 4:
            idle += values[4]

        total = sum(values)

        return total, idle

    except (
        FileNotFoundError,
        IndexError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        logging.debug(
            "CPU statistiku se nepodařilo načíst: %s",
            exc,
        )

        return None


def get_cpu_usage_percent(
    sample_seconds: float = (
        DEFAULT_CPU_SAMPLE_SECONDS
    ),
) -> float | None:
    first_sample = _read_cpu_times()

    if first_sample is None:
        return None

    time.sleep(
        max(
            0.05,
            float(sample_seconds),
        )
    )

    second_sample = _read_cpu_times()

    if second_sample is None:
        return None

    first_total, first_idle = first_sample
    second_total, second_idle = second_sample

    total_delta = second_total - first_total
    idle_delta = second_idle - first_idle

    if total_delta <= 0:
        return None

    usage = (
        1.0
        - (
            idle_delta
            / total_delta
        )
    ) * 100.0

    usage = max(
        0.0,
        min(
            100.0,
            usage,
        ),
    )

    return _round_number(
        usage,
        1,
    )


def get_cpu_temperature_celsius() -> float | None:
    temperature_files: list[str] = []

    for pattern in TEMPERATURE_PATTERNS:
        temperature_files.extend(
            glob.glob(pattern)
        )

    for filename in sorted(
        set(temperature_files)
    ):
        try:
            raw_temperature = float(
                Path(filename).read_text(
                    encoding="utf-8",
                ).strip()
            )

            if abs(raw_temperature) > 1000:
                raw_temperature /= 1000.0

            if -50 <= raw_temperature <= 150:
                return _round_number(
                    raw_temperature,
                    1,
                )

        except (
            FileNotFoundError,
            OSError,
            TypeError,
            ValueError,
        ):
            continue

    return None


def _read_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}

    try:
        lines = _read_text(
            MEMINFO_FILE
        ).splitlines()

    except (
        FileNotFoundError,
        OSError,
    ) as exc:
        logging.debug(
            "Paměťové informace se nepodařilo "
            "načíst: %s",
            exc,
        )

        return values

    for line in lines:
        key, separator, remainder = (
            line.partition(":")
        )

        if not separator:
            continue

        raw_parts = remainder.strip().split()

        if not raw_parts:
            continue

        try:
            value_kib = int(
                raw_parts[0]
            )

        except ValueError:
            continue

        values[key.strip()] = (
            value_kib * 1024
        )

    return values


def get_memory_telemetry() -> dict[str, Any]:
    meminfo = _read_meminfo()

    total_bytes = meminfo.get(
        "MemTotal"
    )

    available_bytes = meminfo.get(
        "MemAvailable"
    )

    if total_bytes is None:
        return {}

    if available_bytes is None:
        free_bytes = meminfo.get(
            "MemFree",
            0,
        )

        buffers_bytes = meminfo.get(
            "Buffers",
            0,
        )

        cached_bytes = meminfo.get(
            "Cached",
            0,
        )

        available_bytes = (
            free_bytes
            + buffers_bytes
            + cached_bytes
        )

    used_bytes = max(
        0,
        total_bytes - available_bytes,
    )

    usage_percent = (
        used_bytes
        / total_bytes
        * 100.0
        if total_bytes > 0
        else 0.0
    )

    return {
        "memory_usage_percent": (
            _round_number(
                usage_percent,
                1,
            )
        ),
        "memory_total_bytes": int(
            total_bytes
        ),
        "memory_used_bytes": int(
            used_bytes
        ),
    }


def _select_disk_path() -> Path:
    for candidate in (
        DISK_PATH_CANDIDATES
    ):
        if candidate.exists():
            return candidate

    return Path("/")


def get_disk_telemetry() -> dict[str, Any]:
    disk_path = _select_disk_path()

    try:
        usage = shutil.disk_usage(
            disk_path
        )

    except OSError as exc:
        logging.debug(
            "Úložiště se nepodařilo načíst: %s",
            exc,
        )

        return {}

    used_bytes = int(usage.used)
    total_bytes = int(usage.total)

    usage_percent = (
        used_bytes
        / total_bytes
        * 100.0
        if total_bytes > 0
        else 0.0
    )

    return {
        "disk_usage_percent": (
            _round_number(
                usage_percent,
                1,
            )
        ),
        "disk_total_bytes": total_bytes,
        "disk_used_bytes": used_bytes,
    }


def get_internet_connected(
    timeout_seconds: float = (
        DEFAULT_INTERNET_TIMEOUT_SECONDS
    ),
) -> bool:
    for host, port in INTERNET_TARGETS:
        try:
            with socket.create_connection(
                (host, port),
                timeout=timeout_seconds,
            ):
                return True

        except OSError:
            continue

    return False


def collect_system_telemetry() -> dict[str, Any]:
    telemetry: dict[str, Any] = {}

    uptime_seconds = (
        get_uptime_seconds()
    )

    if uptime_seconds is not None:
        telemetry[
            "uptime_seconds"
        ] = uptime_seconds

    cpu_usage_percent = (
        get_cpu_usage_percent()
    )

    if cpu_usage_percent is not None:
        telemetry[
            "cpu_usage_percent"
        ] = cpu_usage_percent

    cpu_temperature = (
        get_cpu_temperature_celsius()
    )

    if cpu_temperature is not None:
        telemetry[
            "cpu_temperature_celsius"
        ] = cpu_temperature

    telemetry.update(
        get_memory_telemetry()
    )

    telemetry.update(
        get_disk_telemetry()
    )

    telemetry[
        "internet_connected"
    ] = get_internet_connected()

    return telemetry
