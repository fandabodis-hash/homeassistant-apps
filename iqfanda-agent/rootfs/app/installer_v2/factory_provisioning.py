"""Vyrobni provisioning TNG IQ FANDA Installer V2."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

from installer_v2.bootstrap_identity import (
    atomic_create_json_once,
    ensure_bootstrap_identity,
)
from installer_v2.factory_identity import (
    finalize_manufacturing_identity,
)
from manufacturing import read_raspberry_pi_serial


DEFAULT_API_BASE_URL = "https://api.tngiqfanda.cz"
FACTORY_CLAIM_ENDPOINT = "/api/v1/factory/claim-serial"
AUTH_LOGIN_ENDPOINT = "/api/v1/auth/login"

BOOTSTRAP_CREDENTIAL_PATH = Path(
    "/data/installer_v2_bootstrap_credential.json"
)


def _api_base_url() -> str:
    value = str(
        os.getenv(
            "IQF_API_BASE_URL",
            DEFAULT_API_BASE_URL,
        )
        or DEFAULT_API_BASE_URL
    ).strip().rstrip("/")

    return value or DEFAULT_API_BASE_URL


def _load_json(
    path: Path,
) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(payload, dict):
        raise ValueError(
            f"Neplatny JSON objekt: {path}"
        )

    return payload


def _validate_credential(
    *,
    payload: dict[str, Any],
    provisioning_id: str,
    hardware_id: str,
) -> dict[str, Any]:
    if (
        str(
            payload.get("provisioning_id")
            or ""
        ).strip()
        != provisioning_id
    ):
        raise ValueError(
            "Bootstrap credential ma jine provisioning_id."
        )

    if (
        str(
            payload.get("hardware_id")
            or ""
        ).strip().lower()
        != hardware_id
    ):
        raise ValueError(
            "Bootstrap credential patri jinemu hardware."
        )

    token = str(
        payload.get("bootstrap_token")
        or ""
    ).strip()

    if len(token) < 32:
        raise ValueError(
            "Bootstrap credential token je neplatny."
        )

    return payload


def ensure_bootstrap_credential(
    *,
    provisioning_id: str,
    hardware_id: str,
    path: Path = BOOTSTRAP_CREDENTIAL_PATH,
) -> dict[str, Any]:
    target = Path(path)

    if target.is_file():
        existing = _validate_credential(
            payload=_load_json(target),
            provisioning_id=provisioning_id,
            hardware_id=hardware_id,
        )

        return {
            "created": False,
            "credential": existing,
        }

    candidate = {
        "credential_version": 1,
        "provisioning_id": provisioning_id,
        "hardware_id": hardware_id,
        "bootstrap_token":
            secrets.token_urlsafe(48),
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    created = atomic_create_json_once(
        path=target,
        payload=candidate,
    )

    verified = _validate_credential(
        payload=_load_json(target),
        provisioning_id=provisioning_id,
        hardware_id=hardware_id,
    )

    return {
        "created": created,
        "credential": verified,
    }


def _factory_admin_login(
    *,
    email: str,
    password: str,
) -> str:
    """
    Ziska kratkodoby cloudovy bearer token.

    E-mail, heslo ani vysledny token se nikam
    neukladaji.
    """

    normalized_email = str(
        email or ""
    ).strip().lower()

    raw_password = str(
        password or ""
    )

    if (
        not normalized_email
        or "@" not in normalized_email
    ):
        raise ValueError(
            "Admin e-mail je neplatny."
        )

    if not raw_password:
        raise ValueError(
            "Admin heslo je prazdne."
        )

    body = json.dumps(
        {
            "email": normalized_email,
            "password": raw_password,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    http_request = request.Request(
        (
            _api_base_url()
            + AUTH_LOGIN_ENDPOINT
        ),
        data=body,
        method="POST",
        headers={
            "Content-Type":
                "application/json",
            "Accept":
                "application/json",
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
            "Factory admin prihlaseni "
            f"odmitnuto, HTTP {exc.code}: "
            f"{response_body}"
        ) from exc

    except error.URLError as exc:
        raise RuntimeError(
            "Cloud neni dostupny: "
            f"{exc.reason}"
        ) from exc

    result = json.loads(
        response_body
    )

    if not isinstance(
        result,
        dict,
    ):
        raise RuntimeError(
            "Cloud vratil neplatnou "
            "login odpoved."
        )

    role = str(
        result.get("role")
        or ""
    ).strip().lower()

    if role not in {
        "admin",
        "superadmin",
    }:
        raise RuntimeError(
            "Prihlaseny ucet nema "
            "opravneni Factory V2."
        )

    token = str(
        result.get("access_token")
        or ""
    ).strip()

    if not token:
        raise RuntimeError(
            "Cloud nevratil access token."
        )

    return token


def _claim_factory_serial(
    *,
    admin_token: str,
    provisioning_id: str,
    hardware_id: str,
    bootstrap_token: str,
) -> dict[str, Any]:
    token = str(
        admin_token or ""
    ).strip()

    if not token:
        raise ValueError(
            "Chybi factory admin bearer token."
        )

    credential_hash = hashlib.sha256(
        bootstrap_token.encode("utf-8")
    ).hexdigest()

    payload = {
        "provisioning_id":
            provisioning_id,
        "hardware_id":
            hardware_id,
        "bootstrap_credential_hash":
            credential_hash,
    }

    http_request = request.Request(
        (
            _api_base_url()
            + FACTORY_CLAIM_ENDPOINT
        ),
        data=json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8"),
        method="POST",
        headers={
            "Authorization":
                f"Bearer {token}",
            "Content-Type":
                "application/json",
            "Accept":
                "application/json",
            "User-Agent":
                "TNG-IQ-FANDA-Factory-V2",
        },
    )

    try:
        with request.urlopen(
            http_request,
            timeout=30,
        ) as response:
            body = response.read().decode(
                "utf-8",
                errors="strict",
            )

    except error.HTTPError as exc:
        body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            "Factory cloud claim odmitnut, "
            f"HTTP {exc.code}: {body}"
        ) from exc

    except error.URLError as exc:
        raise RuntimeError(
            "Factory cloud neni dostupny: "
            f"{exc.reason}"
        ) from exc

    result = json.loads(body)

    if not isinstance(result, dict):
        raise RuntimeError(
            "Factory cloud vratil neplatny JSON."
        )

    serial_number = str(
        result.get("serial_number")
        or ""
    ).strip().upper()

    if not serial_number:
        raise RuntimeError(
            "Factory cloud nevratil seriove cislo."
        )

    if (
        str(
            result.get("provisioning_id")
            or ""
        ).strip()
        != provisioning_id
    ):
        raise RuntimeError(
            "Factory cloud vratil jine provisioning_id."
        )

    if (
        str(
            result.get("hardware_id")
            or ""
        ).strip().lower()
        != hardware_id
    ):
        raise RuntimeError(
            "Factory cloud vratil jine hardware_id."
        )

    return {
        "created": bool(
            result.get("created")
        ),
        "serial_number":
            serial_number,
    }


def provision_factory_v2(
    *,
    admin_token: str,
) -> dict[str, Any]:
    hardware_id = str(
        read_raspberry_pi_serial()
        or ""
    ).strip().lower()

    if not hardware_id:
        raise RuntimeError(
            "Nelze nacist hardware_id Raspberry Pi."
        )

    bootstrap_result = (
        ensure_bootstrap_identity(
            hardware_id=hardware_id
        )
    )

    bootstrap = bootstrap_result[
        "identity"
    ]

    provisioning_id = str(
        bootstrap[
            "provisioning_id"
        ]
    )

    credential_result = (
        ensure_bootstrap_credential(
            provisioning_id=provisioning_id,
            hardware_id=hardware_id,
        )
    )

    credential = credential_result[
        "credential"
    ]

    claim = _claim_factory_serial(
        admin_token=admin_token,
        provisioning_id=provisioning_id,
        hardware_id=hardware_id,
        bootstrap_token=str(
            credential[
                "bootstrap_token"
            ]
        ),
    )

    software_version = str(
        os.getenv(
            "IQF_SOFTWARE_VERSION",
            "0.1.74",
        )
        or "0.1.74"
    ).strip()

    final = finalize_manufacturing_identity(
        bootstrap_identity=bootstrap,
        serial_number=claim[
            "serial_number"
        ],
        software_version=software_version,
    )

    identity = final[
        "identity"
    ]

    return {
        "ok": True,
        "bootstrap_identity_created":
            bool(
                bootstrap_result[
                    "created"
                ]
            ),
        "bootstrap_credential_created":
            bool(
                credential_result[
                    "created"
                ]
            ),
        "cloud_claim_created":
            bool(
                claim[
                    "created"
                ]
            ),
        "device_identity_created":
            bool(
                final[
                    "created"
                ]
            ),
        "provisioning_id":
            identity[
                "provisioning_id"
            ],
        "hardware_id":
            identity[
                "hardware_id"
            ],
        "serial_number":
            identity[
                "serial_number"
            ],
        "hostname":
            identity[
                "hostname"
            ],
        "software_version":
            identity[
                "software_version"
            ],
        "state":
            identity[
                "state"
            ],
    }


def provision_factory_v2_from_portal(
    *,
    admin_email: str,
    admin_password: str,
) -> dict[str, Any]:
    """
    Factory V2 spoustene AP portalem.

    Admin prihlasovaci udaje se pouziji pouze
    pro ziskani kratkodobeho bearer tokenu.
    Nic se neuklada do souboru.
    """

    token = _factory_admin_login(
        email=admin_email,
        password=admin_password,
    )

    try:
        return provision_factory_v2(
            admin_token=token
        )

    finally:
        token = ""
