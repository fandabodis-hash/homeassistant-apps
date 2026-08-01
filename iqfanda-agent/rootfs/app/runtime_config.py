"""Spolecna runtime konfigurace TNG IQ FANDA Agentu."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from communication.json_utils import nacti_json


ADDON_OPTIONS_PATH = Path(
    os.getenv(
        "IQF_ADDON_OPTIONS_PATH",
        "/data/options.json",
    )
)

DEVICE_CONFIG_PATH = Path(
    os.getenv(
        "IQF_DEVICE_CONFIG_PATH",
        "/config/device.json",
    )
)


DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "api_base_url": "https://api.tngiqfanda.cz",
    "software_version": "0.1.0",
    "heartbeat_interval_seconds": 60,
    "log_level": "INFO",
}


PROTECTED_DEVICE_FIELDS = {
    "device_uuid",
    "device_token",
    "device_id",
    "site_id",
    "device_status",
}


def load_addon_options() -> dict[str, Any]:
    """Nacte nastaveni Home Assistant add-onu."""

    options = nacti_json(
        ADDON_OPTIONS_PATH
    )

    if not isinstance(options, dict):
        return {}

    return options


def load_device_identity() -> dict[str, Any]:
    """Nacte cloudovou identitu registrovaneho zarizeni."""

    identity = nacti_json(
        DEVICE_CONFIG_PATH
    )

    if not isinstance(identity, dict):
        return {}

    return identity


def build_runtime_configuration(
    *,
    defaults: dict[str, Any] | None = None,
    addon_options: dict[str, Any] | None = None,
    device_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Slouci vychozi hodnoty, options add-onu a identitu.

    Identita zarizeni ma nejvyssi prioritu.
    """

    configuration: dict[str, Any] = dict(
        DEFAULT_RUNTIME_CONFIG
        if defaults is None
        else defaults
    )

    normalized_options = (
        load_addon_options()
        if addon_options is None
        else addon_options
    )

    normalized_identity = (
        load_device_identity()
        if device_identity is None
        else device_identity
    )

    if not isinstance(
        normalized_options,
        dict,
    ):
        raise ValueError(
            "Nastaveni add-onu nemaji platny format."
        )

    if not isinstance(
        normalized_identity,
        dict,
    ):
        raise ValueError(
            "Identita zarizeni nema platny format."
        )

    configuration.update(
        normalized_options
    )

    for field_name in PROTECTED_DEVICE_FIELDS:
        configuration.pop(
            field_name,
            None,
        )

    configuration.update(
        normalized_identity
    )

    return configuration


def load_runtime_configuration(
    *,
    require_registered_device: bool = True,
) -> dict[str, Any]:
    """Nacte kompletni runtime konfiguraci Agentu."""

    configuration = (
        build_runtime_configuration()
    )

    if require_registered_device:
        for field_name in (
            "device_uuid",
            "device_token",
        ):
            if not configuration.get(
                field_name
            ):
                raise ValueError(
                    "V runtime konfiguraci chybi "
                    f"povinne pole: {field_name}"
                )

    return configuration
