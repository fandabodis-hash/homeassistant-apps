"""Cloud registrace prvni instalace TNG IQ FANDA Installer V2."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request

from installer_v2.bootstrap_identity import (
    atomic_create_json_once,
)


DEFAULT_API_BASE_URL = "https://api.tngiqfanda.cz"

REGISTER_ENDPOINT = (
    "/api/v1/installer-v2/register"
)

MANUFACTURING_IDENTITY_PATH = Path(
    "/data/device_identity.json"
)

BOOTSTRAP_CREDENTIAL_PATH = Path(
    "/data/installer_v2_bootstrap_credential.json"
)

DEVICE_CONFIG_PATH = Path(
    os.getenv(
        "IQF_DEVICE_CONFIG_PATH",
        "/config/device.json",
    )
)


def _load_json(
    path: Path,
) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Soubor neexistuje: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(
            f"Neplatny JSON objekt: {path}"
        )

    return payload


def cloud_identity_exists() -> bool:
    return DEVICE_CONFIG_PATH.exists()


def load_cloud_identity() -> dict[str, Any]:
    identity = _load_json(
        DEVICE_CONFIG_PATH
    )

    for field in (
        "device_uuid",
        "device_token",
    ):
        if not str(
            identity.get(field)
            or ""
        ).strip():
            raise ValueError(
                "Cloud identita nema pole: "
                f"{field}"
            )

    return identity


def _load_factory_material() -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    identity = _load_json(
        MANUFACTURING_IDENTITY_PATH
    )

    credential = _load_json(
        BOOTSTRAP_CREDENTIAL_PATH
    )

    for field in (
        "provisioning_id",
        "hardware_id",
        "serial_number",
    ):
        if not str(
            identity.get(field)
            or ""
        ).strip():
            raise ValueError(
                "Vyrobni identita nema pole: "
                f"{field}"
            )

    raw_token = str(
        credential.get(
            "bootstrap_token"
        )
        or ""
    ).strip()

    if not raw_token:
        raise ValueError(
            "Bootstrap credential nema token."
        )

    if (
        str(
            credential.get(
                "provisioning_id"
            )
            or ""
        ).strip()
        != str(
            identity[
                "provisioning_id"
            ]
        ).strip()
    ):
        raise ValueError(
            "Bootstrap credential ma jine provisioning_id."
        )

    if (
        str(
            credential.get(
                "hardware_id"
            )
            or ""
        ).strip().lower()
        != str(
            identity[
                "hardware_id"
            ]
        ).strip().lower()
    ):
        raise ValueError(
            "Bootstrap credential patri jinemu hardware."
        )

    return (
        identity,
        credential,
    )


def _register_url() -> str:
    base_url = (
        os.getenv(
            "IQF_API_BASE_URL",
            DEFAULT_API_BASE_URL,
        )
        .strip()
        .rstrip("/")
    )

    if not base_url:
        base_url = DEFAULT_API_BASE_URL

    return (
        base_url
        + REGISTER_ENDPOINT
    )


def _request_registration(
    *,
    customer_name: str,
    email: str,
    identity: dict[str, Any],
    raw_bootstrap_token: str,
) -> dict[str, Any]:
    payload = {
        "provisioning_id":
            identity[
                "provisioning_id"
            ],

        "hardware_id":
            identity[
                "hardware_id"
            ],

        "customer_name":
            customer_name,

        "email":
            email,

        "device_model":
            identity.get("model"),

        "hardware_revision":
            identity.get(
                "hardware_revision"
            ),

        "device_hostname":
            identity.get(
                "hostname"
            ),

        "software_version":
            identity.get(
                "software_version"
            ),
    }

    body = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")

    http_request = request.Request(
        _register_url(),
        data=body,
        method="POST",
        headers={
            "Content-Type":
                "application/json",
            "Accept":
                "application/json",
            "Authorization":
                (
                    "Bootstrap "
                    + raw_bootstrap_token
                ),
            "User-Agent":
                "TNG-IQ-FANDA-Installer-V2",
        },
    )

    try:
        with request.urlopen(
            http_request,
            timeout=30,
        ) as response:
            response_body = (
                response
                .read()
                .decode(
                    "utf-8",
                    errors="strict",
                )
            )

    except error.HTTPError as exc:
        response_body = (
            exc.read().decode(
                "utf-8",
                errors="replace",
            )
        )

        raise RuntimeError(
            "Cloud registrace odmitnuta, "
            f"HTTP {exc.code}: "
            f"{response_body}"
        ) from exc

    except error.URLError as exc:
        raise RuntimeError(
            "Cloud neni dostupny: "
            f"{exc.reason}"
        ) from exc

    registration = json.loads(
        response_body
    )

    if not isinstance(
        registration,
        dict,
    ):
        raise ValueError(
            "Cloud vratil neplatnou odpoved."
        )

    if registration.get(
        "success"
    ) is not True:
        raise ValueError(
            "Cloud registrace nebyla uspesna."
        )

    for field in (
        "device_uuid",
        "device_token",
        "site_id",
        "device_id",
        "serial_number",
    ):
        if not str(
            registration.get(field)
            or ""
        ).strip():
            raise ValueError(
                "Cloud odpoved nema pole: "
                f"{field}"
            )

    expected_serial = str(
        identity[
            "serial_number"
        ]
    ).strip().upper()

    received_serial = str(
        registration[
            "serial_number"
        ]
    ).strip().upper()

    if (
        received_serial
        != expected_serial
    ):
        raise ValueError(
            "Cloud vratil jine seriove cislo."
        )

    return registration


def _device_config_from_registration(
    registration: dict[str, Any],
) -> dict[str, Any]:
    return {
        "device_uuid":
            registration[
                "device_uuid"
            ],

        "device_token":
            registration[
                "device_token"
            ],

        "api_base_url":
            (
                os.getenv(
                    "IQF_API_BASE_URL",
                    DEFAULT_API_BASE_URL,
                )
                .strip()
                .rstrip("/")
                or DEFAULT_API_BASE_URL
            ),

        "device_status":
            registration.get(
                "device_status"
            ),

        "site_id":
            registration[
                "site_id"
            ],

        "device_id":
            registration[
                "device_id"
            ],
    }


def _publish_cloud_identity(
    registration: dict[str, Any],
) -> dict[str, Any]:
    candidate = (
        _device_config_from_registration(
            registration
        )
    )

    if DEVICE_CONFIG_PATH.exists():
        existing = load_cloud_identity()

        if (
            str(
                existing[
                    "device_uuid"
                ]
            )
            != str(
                candidate[
                    "device_uuid"
                ]
            )
        ):
            raise FileExistsError(
                "Existujici cloud identita "
                "patri jinemu zarizeni."
            )

        return {
            "created": False,
            "identity": existing,
        }

    created = atomic_create_json_once(
        path=DEVICE_CONFIG_PATH,
        payload=candidate,
    )

    verified = load_cloud_identity()

    if (
        str(
            verified[
                "device_uuid"
            ]
        )
        != str(
            candidate[
                "device_uuid"
            ]
        )
    ):
        raise RuntimeError(
            "Publikovana cloud identita "
            "neodpovida registraci."
        )

    return {
        "created": created,
        "identity": verified,
    }


def ensure_cloud_registration(
    *,
    customer_name: str,
    email: str,
) -> dict[str, Any]:
    """
    Idempotentne dokonci cloudovou registraci.

    Pokud /config/device.json jiz existuje,
    cloud se znovu nevola.
    """

    normalized_name = " ".join(
        str(customer_name).split()
    )

    normalized_email = (
        str(email)
        .strip()
        .lower()
    )

    if not normalized_name:
        raise ValueError(
            "Jmeno zakaznika je prazdne."
        )

    if (
        not normalized_email
        or "@"
        not in normalized_email
    ):
        raise ValueError(
            "E-mail zakaznika je neplatny."
        )

    if cloud_identity_exists():
        existing = load_cloud_identity()

        return {
            "ok": True,
            "cloud_called": False,
            "registration_created": False,
            "identity_created": False,
            "identity": existing,
        }

    identity, credential = (
        _load_factory_material()
    )

    registration = (
        _request_registration(
            customer_name=(
                normalized_name
            ),
            email=(
                normalized_email
            ),
            identity=identity,
            raw_bootstrap_token=str(
                credential[
                    "bootstrap_token"
                ]
            ),
        )
    )

    published = (
        _publish_cloud_identity(
            registration
        )
    )

    return {
        "ok": True,
        "cloud_called": True,
        "registration_created": bool(
            registration.get(
                "created"
            )
        ),
        "identity_created": bool(
            published["created"]
        ),
        "serial_number":
            registration[
                "serial_number"
            ],
        "device_uuid":
            registration[
                "device_uuid"
            ],
        "identity":
            published[
                "identity"
            ],
    }