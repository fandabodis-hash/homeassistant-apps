from __future__ import annotations

import argparse
import json
import os
import platform
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from identity import DEFAULT_IDENTITY_PATH, DeviceIdentityService


SERIAL_PATTERN = re.compile(r"^F800711-TNG-\d{6}$")


def _read_raspberry_pi_serial() -> str | None:
    cpuinfo_path = Path("/proc/cpuinfo")

    if not cpuinfo_path.is_file():
        return None

    try:
        for line in cpuinfo_path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines():
            if line.lower().startswith("serial"):
                _, value = line.split(":", 1)
                serial = value.strip()
                return serial or None
    except OSError:
        return None

    return None


def _atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _build_identity(
    serial_number: str,
    model: str,
    hardware_revision: str,
) -> dict[str, Any]:
    serial_suffix = serial_number.rsplit("-", 1)[-1]
    hostname = f"iqf-{serial_suffix}"

    return {
        "manufacturer": "TNG-Air",
        "product": "TNG IQ FANDA",
        "serial_number": serial_number,
        "model": model,
        "hardware_revision": hardware_revision,
        "production_date": date.today().isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "factory_version": "1.0",
        "hostname": hostname,
        "state": "UNPROVISIONED",
        "hardware_id": _read_raspberry_pi_serial(),
        "platform": platform.platform(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assign permanent manufacturing identity to IQ FANDA.",
    )
    parser.add_argument(
        "--serial-number",
        required=True,
        help=(
            "Manufacturing serial number, "
            "for example F800711-TNG-000001."
        ),
    )
    parser.add_argument(
        "--model",
        default="IQF-PI5",
    )
    parser.add_argument(
        "--hardware-revision",
        default="A1",
    )
    parser.add_argument(
        "--identity-path",
        type=Path,
        default=DEFAULT_IDENTITY_PATH,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing identity.",
    )

    args = parser.parse_args()

    serial_number = args.serial_number.strip().upper()

    if not SERIAL_PATTERN.fullmatch(serial_number):
        parser.error(
            "Serial number must have format "
            "F800711-TNG-NNNNNN, "
            "for example F800711-TNG-000001."
        )

    identity_service = DeviceIdentityService(args.identity_path)

    if identity_service.exists() and not args.force:
        existing_identity = identity_service.load()

        print("ERROR: Device identity already exists.")
        print(
            "Existing serial number: "
            f"{existing_identity.get('serial_number')}"
        )
        print(f"Identity path: {args.identity_path}")
        print("Use --force only if replacement is intentional.")

        return 2

    identity = _build_identity(
        serial_number=serial_number,
        model=args.model.strip(),
        hardware_revision=args.hardware_revision.strip(),
    )

    _atomic_write_json(args.identity_path, identity)

    print("IQ FANDA manufacturing identity created.")
    print(f"Serial number: {identity['serial_number']}")
    print(f"Hostname: {identity['hostname']}")
    print(f"Model: {identity['model']}")
    print(f"Hardware revision: {identity['hardware_revision']}")
    print(f"State: {identity['state']}")
    print(f"Identity path: {args.identity_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
