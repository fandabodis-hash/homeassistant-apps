"""Prevod FoxESS H3 snapshotu na FVE entity."""

from __future__ import annotations

import math
from typing import Any


def _read(
    snapshot: dict[str, Any],
    *path: str,
) -> Any:
    value: Any = snapshot

    for key in path:
        if not isinstance(value, dict):
            return None

        value = value.get(key)

    return value


def _create_entity(
    *,
    key: str,
    category: str,
    name: str,
    value: Any,
    unit: str | None,
    value_type: str,
    address: int | None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if value_type == "number":
        if (
            type(value) not in {int, float}
            or not math.isfinite(float(value))
        ):
            raise ValueError(
                f"Entita {key} nema platnou hodnotu."
            )

    elif value_type == "integer":
        if type(value) is not int:
            raise ValueError(
                f"Entita {key} nema platny integer."
            )

    else:
        raise ValueError(
            f"Entita {key} ma nepodporovany typ."
        )

    return {
        "entity_key": key,
        "category": category,
        "name": name,
        "value": value,
        "unit": unit,
        "value_type": value_type,
        "quality": "good",
        "source_address": address,
        "attributes": dict(attributes or {}),
    }


def vytvorit_foxess_h3_fve_entity(
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Vytvori FVE entity FoxESS H3."""
    if not isinstance(snapshot, dict):
        raise ValueError(
            "FoxESS snapshot nema platny format."
        )

    if snapshot.get("complete") is not True:
        raise ValueError(
            "FoxESS snapshot neni kompletni."
        )

    entities: list[dict[str, Any]] = []

    def add(
        key: str,
        category: str,
        name: str,
        path: tuple[str, ...],
        unit: str | None,
        value_type: str,
        address: int | None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        value = _read(snapshot, *path)

        if value is None:
            return

        entities.append(
            _create_entity(
                key=key,
                category=category,
                name=name,
                value=value,
                unit=unit,
                value_type=value_type,
                address=address,
                attributes=attributes,
            )
        )

    for number, base_address in (
        (1, 31000),
        (2, 31003),
    ):
        prefix = f"pv{number}"

        add(
            f"{prefix}.napeti",
            "pv_vstup",
            f"Napětí PV{number}",
            (prefix, "voltage_v"),
            "V",
            "number",
            base_address,
            {"device_class": "voltage"},
        )

        add(
            f"{prefix}.proud",
            "pv_vstup",
            f"Proud PV{number}",
            (prefix, "current_a"),
            "A",
            "number",
            base_address + 1,
            {"device_class": "current"},
        )

        add(
            f"{prefix}.vykon",
            "pv_vstup",
            f"Výkon PV{number}",
            (prefix, "power_w"),
            "W",
            "integer",
            base_address + 2,
            {
                "device_class": "power",
                "sign_convention":
                    "positive_generation",
            },
        )

    add(
        "stridac.vykon_celkem",
        "stridac",
        "Celkový výkon střídače",
        ("inverter", "total_power_raw_w"),
        "W",
        "integer",
        None,
        {
            "device_class": "power",
            "derived_from":
                "31012+31013+31014",
            "verification_status":
                "pending_grid_test",
        },
    )

    add(
        "stridac.aktivni_vykon",
        "stridac",
        "Aktivní výkon střídače",
        ("inverter", "total_power_raw_w"),
        "W",
        "integer",
        None,
        {
            "device_class": "power",
            "verification_status":
                "pending_grid_test",
        },
    )

    add(
        "stridac.teplota_chladice",
        "stridac",
        "Teplota střídače",
        ("inverter", "temperature_c"),
        "°C",
        "number",
        31032,
        {"device_class": "temperature"},
    )

    add(
        "stridac.teplota_vzduchu",
        "stridac",
        "Vnitřní teplota střídače",
        ("inverter", "ambient_temperature_c"),
        "°C",
        "number",
        31033,
        {"device_class": "temperature"},
    )

    add(
        "stridac.pracovni_rezim",
        "stridac",
        "Pracovní režim střídače",
        ("inverter", "state_code"),
        None,
        "integer",
        31041,
        {
            "verified_state_3":
                "eps_off_grid",
        },
    )

    for phase, address in (
        ("l1", 31022),
        ("l2", 31023),
        ("l3", 31024),
    ):
        add(
            f"backup.vykon_{phase}",
            "backup",
            f"Backup výkon {phase.upper()}",
            ("backup", f"power_{phase}_w"),
            "W",
            "integer",
            address,
            {
                "device_class": "power",
                "sign_convention":
                    "positive_consumption",
                "verification_status":
                    "physically_verified_00007",
            },
        )

    add(
        "backup.vykon_celkem",
        "backup",
        "Backup výkon celkem",
        ("backup", "total_power_w"),
        "W",
        "integer",
        None,
        {
            "device_class": "power",
            "derived_from":
                "31022+31023+31024",
            "sign_convention":
                "positive_consumption",
            "verification_status":
                "physically_verified_00007",
        },
    )

    for phase, address in (
        ("l1", 31029),
        ("l2", 31030),
        ("l3", 31031),
    ):
        add(
            f"spotreba.vykon_{phase}",
            "spotreba",
            f"Spotřeba {phase.upper()}",
            ("load", f"power_{phase}_w"),
            "W",
            "integer",
            address,
            {
                "device_class": "power",
                "sign_convention":
                    "positive_consumption",
                "verification_status":
                    "physically_verified_00007",
            },
        )

    add(
        "spotreba.vykon_celkem",
        "spotreba",
        "Spotřeba celkem",
        ("load", "total_power_w"),
        "W",
        "integer",
        None,
        {
            "device_class": "power",
            "derived_from":
                "31029+31030+31031",
            "sign_convention":
                "positive_consumption",
            "verification_status":
                "physically_verified_00007",
        },
    )

    add(
        "baterie.napeti",
        "baterie_souhrn",
        "Napětí baterie",
        ("battery", "voltage_v"),
        "V",
        "number",
        31034,
        {"device_class": "voltage"},
    )

    add(
        "baterie.proud",
        "baterie_souhrn",
        "Proud baterie",
        ("battery", "current_a"),
        "A",
        "number",
        31035,
        {
            "device_class": "current",
            "sign_convention":
                "negative_charging_positive_discharging",
            "verification_status":
                "physically_verified_00007",
        },
    )

    add(
        "baterie.vykon",
        "baterie_souhrn",
        "Výkon baterie",
        ("battery", "power_w"),
        "W",
        "integer",
        31036,
        {
            "device_class": "power",
            "sign_convention":
                "negative_charging_positive_discharging",
            "verification_status":
                "physically_verified_00007",
        },
    )

    add(
        "baterie.teplota",
        "baterie_souhrn",
        "Teplota baterie",
        ("battery", "temperature_c"),
        "°C",
        "number",
        31037,
        {"device_class": "temperature"},
    )

    add(
        "baterie.soc",
        "baterie_souhrn",
        "Stav nabití baterie",
        ("battery", "soc_percent"),
        "%",
        "integer",
        31038,
        {
            "device_class": "battery",
            "verification_status":
                "physically_verified_00007",
        },
    )

    # Smartmeter registry 31026-31028 jsou nacteny,
    # ale entity se nevydavaji, dokud nebude fyzicky
    # overen smer import/export pri pripojenem GRIDu.

    keys = [
        item["entity_key"]
        for item in entities
    ]

    if len(keys) != len(set(keys)):
        raise RuntimeError(
            "FoxESS mapper vytvoril duplicitni entity."
        )

    return entities