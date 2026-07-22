"""Propojeni Installeru s aktivnim provisioning modulem IQ FANDA."""

from typing import Any

from provisioning import (
    register_device as cloud_register_device,
    save_device_identity as ulozit_identitu_zarizeni,
)


def registrovat_zarizeni(payload: dict[str, Any]) -> dict[str, Any]:
    """Odesle registracni data do aktivniho IQ FANDA cloudu."""
    return cloud_register_device(payload)


def ulozit_registraci(registration: dict[str, Any]) -> Any:
    """Ulozi vysledek registrace do aktivniho device.json."""
    return ulozit_identitu_zarizeni(registration)
