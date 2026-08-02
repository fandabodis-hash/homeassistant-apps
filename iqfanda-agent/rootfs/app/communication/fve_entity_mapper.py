"""Prevod GoodWe ET snapshotu na entity modulu Fotovoltaika."""

from __future__ import annotations

import math
from typing import Any


DEFINICE_ENTIT = (
    (
        "pv1.napeti",
        "pv_vstup",
        "Nap\u011bt\u00ed PV1",
        ("operating_data", "pv1", "voltage_v"),
        "V",
        "number",
        35103,
        {"device_class": "voltage"},
    ),
    (
        "pv1.proud",
        "pv_vstup",
        "Proud PV1",
        ("operating_data", "pv1", "current_a"),
        "A",
        "number",
        35104,
        {"device_class": "current"},
    ),
    (
        "pv1.vykon",
        "pv_vstup",
        "V\u00fdkon PV1",
        ("operating_data", "pv1", "power_w"),
        "W",
        "number",
        35105,
        {"device_class": "power"},
    ),
    (
        "pv2.napeti",
        "pv_vstup",
        "Nap\u011bt\u00ed PV2",
        ("operating_data", "pv2", "voltage_v"),
        "V",
        "number",
        35107,
        {"device_class": "voltage"},
    ),
    (
        "pv2.proud",
        "pv_vstup",
        "Proud PV2",
        ("operating_data", "pv2", "current_a"),
        "A",
        "number",
        35108,
        {"device_class": "current"},
    ),
    (
        "pv2.vykon",
        "pv_vstup",
        "V\u00fdkon PV2",
        ("operating_data", "pv2", "power_w"),
        "W",
        "number",
        35109,
        {"device_class": "power"},
    ),
    (
        "stridac.vykon_celkem",
        "stridac",
        "Celkov\u00fd v\u00fdkon st\u0159\u00edda\u010de",
        ("inverter_data", "inverter", "total_power_w"),
        "W",
        "integer",
        35137,
        {"device_class": "power"},
    ),
    (
        "stridac.aktivni_vykon",
        "stridac",
        "Aktivn\u00ed v\u00fdkon st\u0159\u00edda\u010de",
        ("inverter_data", "inverter", "active_power_w"),
        "W",
        "integer",
        35139,
        {
            "device_class": "power",
            "sign_convention": "goodwe_raw",
        },
    ),
    (
        "stridac.teplota_vzduchu",
        "stridac",
        "Teplota vzduchu st\u0159\u00edda\u010de",
        ("inverter_data", "temperatures", "air_c"),
        "\u00b0C",
        "number",
        35174,
        {"device_class": "temperature"},
    ),
    (
        "stridac.teplota_chladice",
        "stridac",
        "Teplota chladi\u010de st\u0159\u00edda\u010de",
        ("inverter_data", "temperatures", "radiator_c"),
        "\u00b0C",
        "number",
        35176,
        {"device_class": "temperature"},
    ),
    (
        "stridac.pracovni_rezim",
        "stridac",
        "Pracovn\u00ed re\u017eim st\u0159\u00edda\u010de",
        ("inverter_data", "work_mode_raw"),
        None,
        "integer",
        None,
        {},
    ),
    (
        "stridac.varovani",
        "stridac",
        "Varov\u00e1n\u00ed st\u0159\u00edda\u010de",
        ("inverter_data", "warning_code_raw"),
        None,
        "integer",
        None,
        {},
    ),
    (
        "smartmeter.vykon_l1",
        "smartmeter",
        "V\u00fdkon s\u00edt\u011b L1",
        ("meter_data", "active_power_w", "r"),
        "W",
        "integer",
        36020,
        {
            "device_class": "power",
            "sign_convention": "negative_import_positive_export",
        },
    ),
    (
        "smartmeter.vykon_l2",
        "smartmeter",
        "V\u00fdkon s\u00edt\u011b L2",
        ("meter_data", "active_power_w", "s"),
        "W",
        "integer",
        36021,
        {
            "device_class": "power",
            "sign_convention": "negative_import_positive_export",
        },
    ),
    (
        "smartmeter.vykon_l3",
        "smartmeter",
        "V\u00fdkon s\u00edt\u011b L3",
        ("meter_data", "active_power_w", "t"),
        "W",
        "integer",
        36023,
        {
            "device_class": "power",
            "sign_convention": "negative_import_positive_export",
        },
    ),
    (
        "smartmeter.vykon_celkem",
        "smartmeter",
        "V\u00fdkon s\u00edt\u011b celkem",
        ("meter_data", "active_power_w", "total"),
        "W",
        "integer",
        36025,
        {
            "device_class": "power",
            "sign_convention": "negative_import_positive_export",
        },
    ),
    (
        "smartmeter.frekvence",
        "smartmeter",
        "Frekvence s\u00edt\u011b",
        ("meter_data", "frequency_hz"),
        "Hz",
        "number",
        36014,
        {"device_class": "frequency"},
    ),
    (
        "smartmeter.odber_dnes",
        "smartmeter",
        "Odb\u011br ze s\u00edt\u011b dnes",
        ("energy_data", "grid_buy_today_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total"},
    ),
    (
        "smartmeter.odber_celkem",
        "smartmeter",
        "Odb\u011br ze s\u00edt\u011b celkem",
        ("energy_data", "grid_buy_total_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total_increasing"},
    ),
    (
        "smartmeter.dodavka_dnes",
        "smartmeter",
        "Dod\u00e1vka do s\u00edt\u011b dnes",
        ("energy_data", "grid_sell_today_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total"},
    ),
    (
        "smartmeter.dodavka_celkem",
        "smartmeter",
        "Dod\u00e1vka do s\u00edt\u011b celkem",
        ("energy_data", "grid_sell_total_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total_increasing"},
    ),
    (
        "backup.vykon_l1",
        "backup",
        "Backup v?kon L1",
        ("inverter_data", "backup", "r", "power_w"),
        "W",
        "integer",
        35149,
        {
            "device_class": "power",
            "sign_convention": "positive_consumption",
        },
    ),
    (
        "backup.vykon_l2",
        "backup",
        "Backup v?kon L2",
        ("inverter_data", "backup", "s", "power_w"),
        "W",
        "integer",
        35155,
        {
            "device_class": "power",
            "sign_convention": "positive_consumption",
        },
    ),
    (
        "backup.vykon_l3",
        "backup",
        "Backup v?kon L3",
        ("inverter_data", "backup", "t", "power_w"),
        "W",
        "integer",
        35161,
        {
            "device_class": "power",
            "sign_convention": "positive_consumption",
        },
    ),
    (
        "backup.vykon_celkem",
        "backup",
        "Backup v?kon celkem",
        ("inverter_data", "backup", "total_power_w"),
        "W",
        "integer",
        35169,
        {
            "device_class": "power",
            "sign_convention": "positive_consumption",
        },
    ),
    (
        "backup.zatizeni",
        "backup",
        "Zat??en? backup v?stupu",
        (
            "inverter_data",
            "load",
            "backup_load_percent",
        ),
        "%",
        "number",
        35173,
        {
            "device_class": "power_factor",
        },
    ),
    (
        "spotreba.vykon_l1",
        "spotreba",
        "Spot\u0159eba L1",
        ("inverter_data", "load", "r_power_w"),
        "W",
        "integer",
        None,
        {"device_class": "power"},
    ),
    (
        "spotreba.vykon_l2",
        "spotreba",
        "Spot\u0159eba L2",
        ("inverter_data", "load", "s_power_w"),
        "W",
        "integer",
        None,
        {"device_class": "power"},
    ),
    (
        "spotreba.vykon_l3",
        "spotreba",
        "Spot\u0159eba L3",
        ("inverter_data", "load", "t_power_w"),
        "W",
        "integer",
        None,
        {"device_class": "power"},
    ),
    (
        "spotreba.vykon_celkem",
        "spotreba",
        "Spot\u0159eba celkem",
        ("inverter_data", "load", "total_power_w"),
        "W",
        "integer",
        None,
        {"device_class": "power"},
    ),
    (
        "spotreba.energie_dnes",
        "spotreba",
        "Spot\u0159eba energie dnes",
        ("energy_data", "load_today_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total"},
    ),
    (
        "spotreba.energie_celkem",
        "spotreba",
        "Spot\u0159eba energie celkem",
        ("energy_data", "load_total_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total_increasing"},
    ),
    (
        "baterie.napeti",
        "baterie_souhrn",
        "Nap\u011bt\u00ed baterie",
        ("inverter_data", "battery", "voltage_v"),
        "V",
        "number",
        35180,
        {"device_class": "voltage"},
    ),
    (
        "baterie.proud",
        "baterie_souhrn",
        "Proud baterie",
        ("inverter_data", "battery", "current_a"),
        "A",
        "number",
        35181,
        {
            "device_class": "current",
            "sign_convention": "goodwe_raw",
        },
    ),
    (
        "baterie.vykon",
        "baterie_souhrn",
        "V\u00fdkon baterie",
        ("inverter_data", "battery", "power_w"),
        "W",
        "integer",
        35182,
        {
            "device_class": "power",
            "sign_convention": "negative_charging_positive_discharging",
        },
    ),
    (
        "baterie.rezim",
        "baterie_souhrn",
        "Re\u017eim baterie",
        ("inverter_data", "battery", "mode_raw"),
        None,
        "integer",
        35184,
        {},
    ),
    (
        "baterie.soc",
        "baterie_souhrn",
        "Stav nabit\u00ed baterie",
        ("bms_summary", "soc_percent"),
        "%",
        "integer",
        37007,
        {"device_class": "battery"},
    ),
    (
        "baterie.soh",
        "baterie_souhrn",
        "Zdrav\u00ed baterie",
        ("bms_summary", "soh_percent"),
        "%",
        "integer",
        37008,
        {},
    ),
    (
        "baterie.teplota",
        "baterie_souhrn",
        "Teplota baterie",
        ("bms_summary", "pack_temperature_c"),
        "\u00b0C",
        "number",
        37003,
        {"device_class": "temperature"},
    ),
    (
        "baterie.limit_proudu_nabijeni",
        "baterie_souhrn",
        "Limit proudu nab\u00edjen\u00ed",
        ("bms_summary", "charge_current_limit_a"),
        "A",
        "integer",
        37004,
        {},
    ),
    (
        "baterie.limit_proudu_vybijeni",
        "baterie_souhrn",
        "Limit proudu vyb\u00edjen\u00ed",
        ("bms_summary", "discharge_current_limit_a"),
        "A",
        "integer",
        37005,
        {},
    ),
    (
        "baterie.status",
        "baterie_souhrn",
        "Stav baterie",
        ("bms_summary", "status_raw"),
        None,
        "integer",
        37002,
        {},
    ),
    (
        "baterie.varovani",
        "baterie_souhrn",
        "Varov\u00e1n\u00ed baterie",
        ("bms_summary", "warning_code_raw"),
        None,
        "integer",
        37010,
        {},
    ),
    (
        "baterie.chyba",
        "baterie_souhrn",
        "Chyba baterie",
        ("bms_summary", "error_code_raw"),
        None,
        "integer",
        37006,
        {},
    ),
    (
        "baterie.pocet_stringu",
        "baterie_souhrn",
        "Po\u010det bateriov\u00fdch string\u016f",
        ("bms_summary", "battery_strings"),
        None,
        "integer",
        37009,
        {},
    ),
    (
        "baterie.energie_nabita_dnes",
        "baterie_souhrn",
        "Energie nabit\u00e1 dnes",
        ("energy_data", "battery_charge_today_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total"},
    ),
    (
        "baterie.energie_nabita_celkem",
        "baterie_souhrn",
        "Energie nabit\u00e1 celkem",
        ("energy_data", "battery_charge_total_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total_increasing"},
    ),
    (
        "baterie.energie_vybita_dnes",
        "baterie_souhrn",
        "Energie vybit\u00e1 dnes",
        ("energy_data", "battery_discharge_today_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total"},
    ),
    (
        "baterie.energie_vybita_celkem",
        "baterie_souhrn",
        "Energie vybit\u00e1 celkem",
        ("energy_data", "battery_discharge_total_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total_increasing"},
    ),
    (
        "vyroba.energie_dnes",
        "vyroba",
        "V\u00fdroba energie dnes",
        ("energy_data", "pv_today_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total"},
    ),
    (
        "vyroba.energie_celkem",
        "vyroba",
        "V\u00fdroba energie celkem",
        ("energy_data", "pv_total_kwh"),
        "kWh",
        "number",
        None,
        {"state_class": "total_increasing"},
    ),
)


def _nacti_hodnotu(
    snapshot: dict[str, Any],
    cesta: tuple[str, ...],
) -> Any:
    hodnota: Any = snapshot

    for cast in cesta:
        if not isinstance(hodnota, dict):
            return None

        hodnota = hodnota.get(cast)

    return hodnota


def _over_hodnotu(
    *,
    klic: str,
    hodnota: Any,
    typ_hodnoty: str,
) -> None:
    if typ_hodnoty == "number":
        if (
            type(hodnota) not in {int, float}
            or not math.isfinite(float(hodnota))
        ):
            raise ValueError(
                f"Entita {klic} nema platnou ciselnu hodnotu."
            )

        return

    if typ_hodnoty == "integer":
        if type(hodnota) is not int:
            raise ValueError(
                f"Entita {klic} nema platnou celociselnou hodnotu."
            )

        return

    raise ValueError(
        f"Entita {klic} ma nepodporovany datovy typ."
    )


def vytvorit_goodwe_fve_entity(
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Vytvori validni cloudove entity z GoodWe ET snapshotu."""
    if not isinstance(snapshot, dict):
        raise ValueError(
            "GoodWe snapshot nema platny format."
        )

    entity: list[dict[str, Any]] = []

    for (
        klic,
        kategorie,
        nazev,
        cesta,
        jednotka,
        typ_hodnoty,
        adresa,
        atributy,
    ) in DEFINICE_ENTIT:
        hodnota = _nacti_hodnotu(
            snapshot,
            cesta,
        )

        if hodnota is None:
            continue

        _over_hodnotu(
            klic=klic,
            hodnota=hodnota,
            typ_hodnoty=typ_hodnoty,
        )

        entita = {
            "entity_key": klic,
            "category": kategorie,
            "name": nazev,
            "value": hodnota,
            "unit": jednotka,
            "value_type": typ_hodnoty,
            "quality": "good",
            "source_address": adresa,
            "attributes": dict(atributy),
        }

        entity.append(entita)

    klice = [
        entita["entity_key"]
        for entita in entity
    ]

    if len(klice) != len(set(klice)):
        raise RuntimeError(
            "Mapper vytvoril duplicitni entity."
        )

    return entity
