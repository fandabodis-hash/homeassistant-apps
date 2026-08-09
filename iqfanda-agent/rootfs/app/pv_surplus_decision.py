"""Cisty rozhodovaci engine rizeni prebytku z FVE."""

from __future__ import annotations

import math
from typing import Any


STAV_OFF = "OFF"
STAV_CONFIRMING = "CONFIRMING"
STAV_ACTIVE = "ACTIVE"
STAV_BLOCKED = "BLOCKED"
STAV_FAULT = "FAULT"


def _cislo(
    hodnota: Any,
    nazev: str,
) -> float:
    """Prevede vstup na konecne cislo."""
    if isinstance(hodnota, bool):
        raise ValueError(f"{nazev} nema platnou ciselnu hodnotu.")

    try:
        vysledek = float(hodnota)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{nazev} nema platnou ciselnu hodnotu."
        ) from exc

    if not math.isfinite(vysledek):
        raise ValueError(
            f"{nazev} nema konecnou ciselnu hodnotu."
        )

    return vysledek


def vytvorit_mapu_entit(
    entity: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Prevede GoodWe entity na mapu podle entity_key."""
    vysledek: dict[str, dict[str, Any]] = {}

    for entita in entity:
        if not isinstance(entita, dict):
            continue

        klic = str(
            entita.get("entity_key") or ""
        ).strip()

        if not klic:
            continue

        if klic in vysledek:
            raise ValueError(
                f"Duplicitni GoodWe entita: {klic}"
            )

        vysledek[klic] = entita

    return vysledek


def nacti_goodwe_hodnotu(
    mapa_entit: dict[str, dict[str, Any]],
    entity_key: str,
) -> float:
    """Nacte validni ciselny GoodWe vstup."""
    entita = mapa_entit.get(entity_key)

    if not isinstance(entita, dict):
        raise ValueError(
            f"Chybi GoodWe entita: {entity_key}"
        )

    if entita.get("quality") != "good":
        raise ValueError(
            f"GoodWe entita nema kvalitu good: {entity_key}"
        )

    return _cislo(
        entita.get("value"),
        entity_key,
    )


def vyhodnotit_battery_soc_source(
    *,
    konfigurace: dict[str, Any],
    goodwe_entity: list[dict[str, Any]],
    predchozi_stav: str = STAV_OFF,
) -> dict[str, Any]:
    """
    Vyhodnoti zdroj prebytku battery_soc.

    Funkce nema zadne I/O a nikdy neovlada fyzicky vystup.
    """
    mapa = vytvorit_mapu_entit(goodwe_entity)

    soc = nacti_goodwe_hodnotu(
        mapa,
        "baterie.soc",
    )

    pv1 = nacti_goodwe_hodnotu(
        mapa,
        "pv1.vykon",
    )

    pv2 = nacti_goodwe_hodnotu(
        mapa,
        "pv2.vykon",
    )

    sit = nacti_goodwe_hodnotu(
        mapa,
        "smartmeter.vykon_celkem",
    )

    enable_soc = _cislo(
        konfigurace.get("enable_soc_percent"),
        "enable_soc_percent",
    )

    disable_soc = _cislo(
        konfigurace.get("disable_soc_percent"),
        "disable_soc_percent",
    )

    minimum_pv = _cislo(
        konfigurace.get("minimum_pv_power_w"),
        "minimum_pv_power_w",
    )

    require_no_grid_import = (
        konfigurace.get("require_no_grid_import") is True
    )

    if not 0 <= disable_soc <= enable_soc <= 100:
        raise ValueError(
            "SOC hystereze nema platne meze."
        )

    if minimum_pv < 0:
        raise ValueError(
            "minimum_pv_power_w nesmi byt zaporne."
        )

    pv_vykon = pv1 + pv2
    odber_ze_site = sit < 0

    blokace: list[str] = []

    if pv_vykon < minimum_pv:
        blokace.append("minimum_pv_power")

    if require_no_grid_import and odber_ze_site:
        blokace.append("grid_import")

    if blokace:
        return {
            "state": STAV_BLOCKED,
            "reason": ",".join(blokace),
            "surplus_available": False,
            "soc_percent": soc,
            "pv_power_w": pv_vykon,
            "grid_power_w": sit,
            "grid_import": odber_ze_site,
        }

    aktivni_predtim = predchozi_stav in {
        STAV_ACTIVE,
        STAV_CONFIRMING,
    }

    if aktivni_predtim:
        soc_povoluje = soc > disable_soc
    else:
        soc_povoluje = soc >= enable_soc

    if not soc_povoluje:
        return {
            "state": STAV_OFF,
            "reason": "battery_soc",
            "surplus_available": False,
            "soc_percent": soc,
            "pv_power_w": pv_vykon,
            "grid_power_w": sit,
            "grid_import": odber_ze_site,
        }

    return {
        "state": STAV_CONFIRMING,
        "reason": "conditions_met",
        "surplus_available": True,
        "soc_percent": soc,
        "pv_power_w": pv_vykon,
        "grid_power_w": sit,
        "grid_import": odber_ze_site,
    }

def vyhodnotit_stav_prebytku(
    *,
    konfigurace: dict[str, Any],
    goodwe_entity: list[dict[str, Any]],
    predchozi_stav: str = STAV_OFF,
    confirming_since: float | None = None,
    now: float,
) -> dict[str, Any]:
    """
    Vyhodnoti stavovy automat zdroje prebytku.

    Funkce je deterministicka, nema I/O a cas dostava explicitne.
    """
    if predchozi_stav not in {
        STAV_OFF,
        STAV_CONFIRMING,
        STAV_ACTIVE,
        STAV_BLOCKED,
        STAV_FAULT,
    }:
        raise ValueError(
            f"Neznamy predchozi stav: {predchozi_stav}"
        )

    now_value = _cislo(
        now,
        "now",
    )

    confirmation_seconds = _cislo(
        konfigurace.get("confirmation_seconds"),
        "confirmation_seconds",
    )

    if confirmation_seconds < 0:
        raise ValueError(
            "confirmation_seconds nesmi byt zaporne."
        )

    try:
        okamzity = vyhodnotit_battery_soc_source(
            konfigurace=konfigurace,
            goodwe_entity=goodwe_entity,
            predchozi_stav=predchozi_stav,
        )
    except ValueError as exc:
        return {
            "state": STAV_FAULT,
            "reason": str(exc),
            "surplus_available": False,
            "confirming_since": None,
            "confirmation_elapsed_seconds": 0.0,
        }

    okamzity_stav = okamzity["state"]

    if okamzity_stav in {
        STAV_OFF,
        STAV_BLOCKED,
    }:
        return {
            **okamzity,
            "confirming_since": None,
            "confirmation_elapsed_seconds": 0.0,
        }

    if predchozi_stav == STAV_ACTIVE:
        return {
            **okamzity,
            "state": STAV_ACTIVE,
            "reason": "active_conditions_met",
            "confirming_since": None,
            "confirmation_elapsed_seconds":
                confirmation_seconds,
        }

    if confirmation_seconds == 0:
        return {
            **okamzity,
            "state": STAV_ACTIVE,
            "reason": "confirmation_complete",
            "confirming_since": None,
            "confirmation_elapsed_seconds": 0.0,
        }

    if (
        predchozi_stav != STAV_CONFIRMING
        or confirming_since is None
    ):
        start = now_value
    else:
        start = _cislo(
            confirming_since,
            "confirming_since",
        )

        if start > now_value:
            return {
                **okamzity,
                "state": STAV_FAULT,
                "reason": "confirming_since_is_in_future",
                "surplus_available": False,
                "confirming_since": None,
                "confirmation_elapsed_seconds": 0.0,
            }

    elapsed = now_value - start

    if elapsed >= confirmation_seconds:
        return {
            **okamzity,
            "state": STAV_ACTIVE,
            "reason": "confirmation_complete",
            "confirming_since": None,
            "confirmation_elapsed_seconds": elapsed,
        }

    return {
        **okamzity,
        "state": STAV_CONFIRMING,
        "reason": "confirmation_in_progress",
        "confirming_since": start,
        "confirmation_elapsed_seconds": elapsed,
    }

def vyhodnotit_teplotu_cile(
    *,
    target: dict[str, Any],
    temperature_c: Any,
    vystup_aktivni: bool = False,
) -> dict[str, Any]:
    """
    Vyhodnoti teplotni pozadavek jednoho cile.

    Funkce nema I/O a pouze rozhoduje, zda cil potrebuje energii.
    """
    if not isinstance(target, dict):
        raise ValueError(
            "Target nema platny format."
        )

    if target.get("enabled") is not True:
        return {
            "state": STAV_BLOCKED,
            "reason": "target_disabled",
            "heat_demand": False,
        }

    if target.get("configuration_status") != "verified":
        return {
            "state": STAV_BLOCKED,
            "reason": "target_not_verified",
            "heat_demand": False,
        }

    conditions = target.get("conditions")

    if not isinstance(conditions, dict):
        raise ValueError(
            "Target nema conditions."
        )

    temperature = _cislo(
        temperature_c,
        "temperature_c",
    )

    target_temperature = _cislo(
        conditions.get("target_temperature_c"),
        "target_temperature_c",
    )

    hysteresis = _cislo(
        conditions.get("hysteresis_c"),
        "hysteresis_c",
    )

    maximum_temperature = _cislo(
        conditions.get("maximum_temperature_c"),
        "maximum_temperature_c",
    )

    if hysteresis < 0:
        raise ValueError(
            "hysteresis_c nesmi byt zaporne."
        )

    start_temperature = (
        target_temperature - hysteresis
    )

    if maximum_temperature < target_temperature:
        raise ValueError(
            "maximum_temperature_c nesmi byt nizsi "
            "nez target_temperature_c."
        )

    if temperature >= maximum_temperature:
        return {
            "state": STAV_BLOCKED,
            "reason": "maximum_temperature",
            "heat_demand": False,
            "temperature_c": temperature,
            "start_temperature_c": start_temperature,
            "target_temperature_c": target_temperature,
            "maximum_temperature_c": maximum_temperature,
        }

    if temperature >= target_temperature:
        return {
            "state": STAV_OFF,
            "reason": "target_temperature_reached",
            "heat_demand": False,
            "temperature_c": temperature,
            "start_temperature_c": start_temperature,
            "target_temperature_c": target_temperature,
            "maximum_temperature_c": maximum_temperature,
        }

    if vystup_aktivni:
        return {
            "state": STAV_ACTIVE,
            "reason": "heating_hysteresis",
            "heat_demand": True,
            "temperature_c": temperature,
            "start_temperature_c": start_temperature,
            "target_temperature_c": target_temperature,
            "maximum_temperature_c": maximum_temperature,
        }

    if temperature < start_temperature:
        return {
            "state": STAV_ACTIVE,
            "reason": "below_start_temperature",
            "heat_demand": True,
            "temperature_c": temperature,
            "start_temperature_c": start_temperature,
            "target_temperature_c": target_temperature,
            "maximum_temperature_c": maximum_temperature,
        }

    return {
        "state": STAV_OFF,
        "reason": "temperature_hysteresis_band",
        "heat_demand": False,
        "temperature_c": temperature,
        "start_temperature_c": start_temperature,
        "target_temperature_c": target_temperature,
        "maximum_temperature_c": maximum_temperature,
    }

def vyhodnotit_cil_prebytku(
    *,
    surplus_result: dict[str, Any],
    target_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Spoji energeticke a cilove rozhodnuti.

    Funkce pouze vraci pozadovany stav vystupu.
    Fyzicky vystup nikdy neovlada.
    """
    if not isinstance(surplus_result, dict):
        raise ValueError(
            "surplus_result nema platny format."
        )

    if not isinstance(target_result, dict):
        raise ValueError(
            "target_result nema platny format."
        )

    surplus_state = str(
        surplus_result.get("state") or ""
    ).strip()

    target_state = str(
        target_result.get("state") or ""
    ).strip()

    if surplus_state not in {
        STAV_OFF,
        STAV_CONFIRMING,
        STAV_ACTIVE,
        STAV_BLOCKED,
        STAV_FAULT,
    }:
        raise ValueError(
            f"Neznamy surplus state: {surplus_state}"
        )

    if target_state not in {
        STAV_OFF,
        STAV_ACTIVE,
        STAV_BLOCKED,
        STAV_FAULT,
    }:
        raise ValueError(
            f"Neznamy target state: {target_state}"
        )

    heat_demand = (
        target_result.get("heat_demand") is True
    )

    should_be_on = (
        surplus_state == STAV_ACTIVE
        and target_state == STAV_ACTIVE
        and heat_demand
    )

    if should_be_on:
        reason = "surplus_active_and_heat_demand"
    elif surplus_state == STAV_FAULT:
        reason = "surplus_fault"
    elif target_state == STAV_FAULT:
        reason = "target_fault"
    elif surplus_state == STAV_BLOCKED:
        reason = "surplus_blocked"
    elif target_state == STAV_BLOCKED:
        reason = "target_blocked"
    elif surplus_state == STAV_CONFIRMING:
        reason = "surplus_confirming"
    elif surplus_state != STAV_ACTIVE:
        reason = "surplus_not_active"
    elif not heat_demand:
        reason = "no_heat_demand"
    else:
        reason = "target_not_active"

    return {
        "should_be_on": should_be_on,
        "reason": reason,
        "surplus_state": surplus_state,
        "surplus_reason": surplus_result.get("reason"),
        "target_state": target_state,
        "target_reason": target_result.get("reason"),
        "heat_demand": heat_demand,
    }
