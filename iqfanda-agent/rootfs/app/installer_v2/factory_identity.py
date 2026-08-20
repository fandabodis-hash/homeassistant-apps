"""Finalizace vyrobni identity TNG IQ FANDA Installer V2.

Spojuje:
- trvale provisioning_id z bootstrap identity,
- seriove cislo pridelenene cloudem,
- standardni format vyrobni identity TNG IQ FANDA.

Tento modul:
- neprideluje seriove cislo,
- nevola cloud,
- neovlada sit ani AP.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from host.identity import (
    DEFAULT_IDENTITY_PATH,
    DeviceIdentityService,
)
from installer_v2.bootstrap_identity import (
    atomic_create_json_once,
    normalize_hardware_id,
    validate_bootstrap_identity,
)
from manufacturing import (
    build_manufacturing_identity,
    normalize_serial_number,
)


def _normalize_optional_hardware_id(
    value: Any,
) -> str | None:
    normalized = str(
        value or ""
    ).strip()

    if not normalized:
        return None

    return normalize_hardware_id(
        normalized
    )


def _verify_existing_identity(
    *,
    existing: dict[str, Any],
    provisioning_id: str,
    serial_number: str,
    hardware_id: str,
) -> dict[str, Any]:
    """Overi, zda existujici identita odpovida stejnemu kusu."""

    existing_provisioning_id = str(
        existing.get(
            "provisioning_id"
        )
        or ""
    ).strip()

    existing_serial = (
        str(
            existing.get(
                "serial_number"
            )
            or ""
        )
        .strip()
        .upper()
    )

    existing_hardware_id = (
        _normalize_optional_hardware_id(
            existing.get(
                "hardware_id"
            )
        )
    )

    if (
        existing_provisioning_id
        != provisioning_id
    ):
        raise FileExistsError(
            "Existujici vyrobni identita ma jine provisioning_id."
        )

    if (
        existing_serial
        != serial_number
    ):
        raise FileExistsError(
            "Existujici vyrobni identita ma jine seriove cislo."
        )

    if (
        existing_hardware_id is not None
        and existing_hardware_id
        != hardware_id
    ):
        raise FileExistsError(
            "Existujici vyrobni identita patri jinemu hardware."
        )

    return existing


def finalize_manufacturing_identity(
    *,
    bootstrap_identity: dict[str, Any],
    serial_number: str,
    identity_path: Path = DEFAULT_IDENTITY_PATH,
    model: str = "IQ FANDA PI5",
    hardware_revision: str = "Raspberry Pi 5",
    software_version: str = "0.1.74",
) -> dict[str, Any]:
    """
    Dokonci trvalou vyrobni identitu.

    provisioning_id se nikdy nevytvari znovu.
    Pouzije se presne hodnota z bootstrap identity.

    Publikace device_identity.json je create-once:
    prvni proces vyhraje, dalsi pouze overi existujici identitu.

    Opakovane volani se stejnymi daty je idempotentni.
    Konflikt s existujici identitou je odmitnut.
    """

    bootstrap = validate_bootstrap_identity(
        bootstrap_identity
    )

    provisioning_id = str(
        bootstrap[
            "provisioning_id"
        ]
    )

    hardware_id = normalize_hardware_id(
        bootstrap[
            "hardware_id"
        ]
    )

    normalized_serial = (
        normalize_serial_number(
            serial_number
        )
    )

    target = Path(
        identity_path
    )

    identity_service = (
        DeviceIdentityService(
            identity_path=target
        )
    )

    if identity_service.exists():
        existing = (
            identity_service.load()
        )

        verified = (
            _verify_existing_identity(
                existing=existing,
                provisioning_id=(
                    provisioning_id
                ),
                serial_number=(
                    normalized_serial
                ),
                hardware_id=(
                    hardware_id
                ),
            )
        )

        return {
            "created": False,
            "identity": verified,
        }

    candidate = (
        build_manufacturing_identity(
            serial_number=(
                normalized_serial
            ),
            model=model,
            hardware_revision=(
                hardware_revision
            ),
            software_version=(
                software_version
            ),
        )
    )

    detected_hardware_id = (
        _normalize_optional_hardware_id(
            candidate.get(
                "hardware_id"
            )
        )
    )

    if (
        detected_hardware_id is not None
        and detected_hardware_id
        != hardware_id
    ):
        raise ValueError(
            "Bootstrap hardware_id neodpovida fyzickemu zarizeni."
        )

    candidate[
        "provisioning_id"
    ] = provisioning_id

    candidate[
        "hardware_id"
    ] = hardware_id

    candidate[
        "serial_number"
    ] = normalized_serial

    candidate[
        "state"
    ] = "READY_FOR_INSTALL"

    created = atomic_create_json_once(
        path=target,
        payload=candidate,
    )

    if not identity_service.exists():
        raise RuntimeError(
            "Vyrobni identita nebyla po vytvoreni nalezena."
        )

    verified = (
        identity_service.load()
    )

    _verify_existing_identity(
        existing=verified,
        provisioning_id=(
            provisioning_id
        ),
        serial_number=(
            normalized_serial
        ),
        hardware_id=(
            hardware_id
        ),
    )

    if (
        str(
            verified.get(
                "state"
            )
            or ""
        )
        != "READY_FOR_INSTALL"
    ):
        raise RuntimeError(
            "Vyrobni identita nema stav READY_FOR_INSTALL."
        )

    return {
        "created": created,
        "identity": verified,
    }