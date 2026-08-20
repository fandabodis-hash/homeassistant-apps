"""Trvala bootstrap identita TNG IQ FANDA Installer V2.

Bootstrap identita existuje jeste pred pridelenim serioveho cisla.

Jejim hlavnim ucelem je zajistit idempotenci:
stejny fyzicky IQ FANDA musi pri opakovanem provisioning
pozadavku pouzit stale stejne provisioning_id.

Tento modul:
- neprideluje seriove cislo,
- nevola cloud,
- nemeni vyrobni identitu,
- neovlada sit ani AP.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BOOTSTRAP_IDENTITY_PATH = Path(
    "/data/installer_v2_bootstrap_identity.json"
)

_BOOTSTRAP_LOCK = threading.Lock()


def normalize_hardware_id(
    hardware_id: str,
) -> str:
    """Normalizuje hardwarovy identifikator Raspberry Pi."""

    normalized = str(
        hardware_id or ""
    ).strip().lower()

    if not normalized:
        raise ValueError(
            "hardware_id nesmi byt prazdny."
        )

    return normalized


def build_bootstrap_identity(
    hardware_id: str,
    provisioning_id: str | None = None,
) -> dict[str, Any]:
    """Vytvori kandidata nove bootstrap identity."""

    normalized_hardware_id = normalize_hardware_id(
        hardware_id
    )

    if provisioning_id is None:
        normalized_provisioning_id = str(
            uuid.uuid4()
        )
    else:
        normalized_provisioning_id = str(
            uuid.UUID(
                str(provisioning_id).strip()
            )
        )

    return {
        "bootstrap_version": 1,
        "provisioning_id": normalized_provisioning_id,
        "hardware_id": normalized_hardware_id,
        "state": "BOOTSTRAP_PENDING",
        "serial_number": None,
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


def validate_bootstrap_identity(
    payload: Any,
    expected_hardware_id: str | None = None,
) -> dict[str, Any]:
    """Overi existujici bootstrap identitu."""

    if not isinstance(payload, dict):
        raise ValueError(
            "Bootstrap identita musi byt JSON objekt."
        )

    try:
        provisioning_id = str(
            uuid.UUID(
                str(
                    payload.get(
                        "provisioning_id"
                    )
                    or ""
                ).strip()
            )
        )
    except (
        ValueError,
        AttributeError,
    ) as exc:
        raise ValueError(
            "Bootstrap identita nema platne provisioning_id."
        ) from exc

    hardware_id = normalize_hardware_id(
        payload.get("hardware_id")
        or ""
    )

    if expected_hardware_id is not None:
        expected = normalize_hardware_id(
            expected_hardware_id
        )

        if hardware_id != expected:
            raise ValueError(
                "Bootstrap identita patri jinemu hardware."
            )

    serial_number = payload.get(
        "serial_number"
    )

    if serial_number is not None:
        serial_number = (
            str(serial_number)
            .strip()
            .upper()
            or None
        )

    return {
        "bootstrap_version": int(
            payload.get(
                "bootstrap_version",
                1,
            )
        ),
        "provisioning_id": provisioning_id,
        "hardware_id": hardware_id,
        "state": str(
            payload.get("state")
            or "BOOTSTRAP_PENDING"
        ).strip(),
        "serial_number": serial_number,
        "created_at": str(
            payload.get("created_at")
            or ""
        ).strip(),
    }


def _fsync_directory(
    directory: Path,
) -> None:
    """Best-effort flush adresarove polozky na disk."""

    try:
        descriptor = os.open(
            str(directory),
            os.O_RDONLY,
        )
    except OSError:
        return

    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_candidate_file(
    target: Path,
    payload: dict[str, Any],
) -> Path:
    """
    Zapise kompletni kandidatsky soubor.

    Soubor jeste neni publikovan jako finalni identita.
    """

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    descriptor, temporary_name = (
        tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".candidate",
            dir=str(target.parent),
        )
    )

    temporary = Path(
        temporary_name
    )

    try:
        os.fchmod(
            descriptor,
            0o600,
        )

        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )

            handle.write("\n")
            handle.flush()

            os.fsync(
                handle.fileno()
            )

        return temporary

    except Exception:
        temporary.unlink(
            missing_ok=True
        )
        raise


def atomic_create_json_once(
    path: Path,
    payload: dict[str, Any],
) -> bool:
    """
    Atomicky publikuje identitu pouze pokud jeste neexistuje.

    Pouziti hard-link operace znamena:
    - prvni proces vyhraje,
    - dalsi proces nesmi prepsat existujici provisioning_id.

    Vraci True pouze procesu, ktery identitu skutecne vytvoril.
    """

    target = Path(path)

    temporary = _write_candidate_file(
        target=target,
        payload=payload,
    )

    try:
        try:
            os.link(
                temporary,
                target,
            )

        except FileExistsError:
            return False

        os.chmod(
            target,
            0o600,
        )

        _fsync_directory(
            target.parent
        )

        return True

    finally:
        temporary.unlink(
            missing_ok=True
        )


def load_bootstrap_identity(
    path: Path = DEFAULT_BOOTSTRAP_IDENTITY_PATH,
    expected_hardware_id: str | None = None,
) -> dict[str, Any] | None:
    """Pouze nacte existujici bootstrap identitu."""

    target = Path(path)

    if not target.is_file():
        return None

    try:
        payload = json.loads(
            target.read_text(
                encoding="utf-8-sig"
            )
        )

    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise RuntimeError(
            "Bootstrap identitu nelze nacist."
        ) from exc

    return validate_bootstrap_identity(
        payload,
        expected_hardware_id=(
            expected_hardware_id
        ),
    )


def ensure_bootstrap_identity(
    *,
    hardware_id: str,
    path: Path = DEFAULT_BOOTSTRAP_IDENTITY_PATH,
) -> dict[str, Any]:
    """
    Vrati existujici identitu nebo jednou vytvori novou.

    Opakovane i soubezne volani pro stejny hardware
    musi vzdy vratit stejne provisioning_id.
    """

    normalized_hardware_id = (
        normalize_hardware_id(
            hardware_id
        )
    )

    with _BOOTSTRAP_LOCK:
        existing = load_bootstrap_identity(
            path=path,
            expected_hardware_id=(
                normalized_hardware_id
            ),
        )

        if existing is not None:
            return {
                "created": False,
                "identity": existing,
            }

        candidate = build_bootstrap_identity(
            hardware_id=(
                normalized_hardware_id
            )
        )

        created = atomic_create_json_once(
            path=path,
            payload=candidate,
        )

        verified = load_bootstrap_identity(
            path=path,
            expected_hardware_id=(
                normalized_hardware_id
            ),
        )

        if verified is None:
            raise RuntimeError(
                "Bootstrap identita nebyla po vytvoreni nalezena."
            )

        return {
            "created": created,
            "identity": verified,
        }