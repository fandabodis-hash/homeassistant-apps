"""Spravce komunikacniho centra TNG IQ FANDA Agentu."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from communication.models import (
    aktualni_cas_iso,
    vytvor_komunikator,
    vytvor_prazdny_stav,
)


USB_INVENTORY_PATH = Path(
    os.getenv(
        "IQF_USB_INVENTORY_PATH",
        "/config/usb_inventory.json",
    )
)

COMMUNICATION_STATE_PATH = Path(
    os.getenv(
        "IQF_COMMUNICATION_STATE_PATH",
        "/config/communication.json",
    )
)

SCAN_INTERVAL_SECONDS = 5

SYSTEM_USB_VENDOR_IDS = {
    "1D6B",
}


logger = logging.getLogger(__name__)


def nacti_json(
    cesta: Path,
) -> dict[str, Any] | None:
    """Bezpecne nacte JSON objekt."""

    if not cesta.exists():
        return None

    try:
        with cesta.open(
            "r",
            encoding="utf-8-sig",
        ) as soubor:
            data = json.load(soubor)

    except (
        OSError,
        json.JSONDecodeError,
    ):
        logger.exception(
            "Nepodarilo se nacist JSON soubor %s.",
            cesta,
        )
        return None

    if not isinstance(data, dict):
        logger.error(
            "Soubor %s neobsahuje JSON objekt.",
            cesta,
        )
        return None

    return data


def je_systemove_usb_zarizeni(
    zarizeni: dict[str, Any],
) -> bool:
    """Rozpozna systemovy Linux USB root hub."""

    vid = str(
        zarizeni.get("vid") or ""
    ).strip().upper()

    return vid in SYSTEM_USB_VENDOR_IDS


def vytvor_komunikacni_stav() -> dict[str, Any]:
    """Vytvori komunikacni stav z USB inventare."""

    stav = vytvor_prazdny_stav()

    usb_inventar = nacti_json(
        USB_INVENTORY_PATH
    )

    if usb_inventar is None:
        stav["source_status"] = (
            "inventory_unavailable"
        )
        stav["source_status_name"] = (
            "USB inventář zatím není dostupný"
        )
        return stav

    usb_zarizeni = usb_inventar.get("devices")

    if not isinstance(usb_zarizeni, list):
        usb_zarizeni = []

    komunikatory: list[dict[str, Any]] = []

    for zarizeni in usb_zarizeni:
        if not isinstance(zarizeni, dict):
            continue

        if je_systemove_usb_zarizeni(zarizeni):
            continue

        komunikatory.append(
            vytvor_komunikator(zarizeni)
        )

    komunikatory.sort(
        key=lambda polozka: (
            str(polozka.get("type_name") or ""),
            str(polozka.get("manufacturer") or ""),
            str(polozka.get("product") or ""),
            str(
                polozka.get("communicator_id") or ""
            ),
        )
    )

    stav["generated_at"] = aktualni_cas_iso()
    stav["source_status"] = "available"
    stav["source_status_name"] = (
        "USB inventář je dostupný"
    )
    stav["source_inventory_generated_at"] = (
        usb_inventar.get("generated_at")
    )
    stav["source_inventory_hash"] = (
        usb_inventar.get("inventory_hash")
    )
    stav["communicators"] = komunikatory

    stav["summary"][
        "communicators_total"
    ] = len(komunikatory)

    stav["summary"][
        "communicators_connected"
    ] = sum(
        1
        for komunikator in komunikatory
        if komunikator.get("connected")
    )

    return stav


def vypocitej_otisk(
    stav: dict[str, Any],
) -> str:
    """Vypocita stabilni otisk komunikacniho stavu."""

    data_pro_otisk = dict(stav)
    data_pro_otisk.pop("generated_at", None)

    obsah = json.dumps(
        data_pro_otisk,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        obsah.encode("utf-8")
    ).hexdigest()


def uloz_komunikacni_stav(
    stav: dict[str, Any],
) -> None:
    """Atomicky ulozi komunikacni stav."""

    COMMUNICATION_STATE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    docasna_cesta = (
        COMMUNICATION_STATE_PATH.with_suffix(
            COMMUNICATION_STATE_PATH.suffix
            + ".tmp"
        )
    )

    with docasna_cesta.open(
        "w",
        encoding="utf-8",
    ) as soubor:
        json.dump(
            stav,
            soubor,
            ensure_ascii=False,
            indent=2,
        )
        soubor.flush()
        os.fsync(soubor.fileno())

    os.replace(
        docasna_cesta,
        COMMUNICATION_STATE_PATH,
    )


def nacti_komunikacni_stav() -> dict[str, Any]:
    """Nacte posledni ulozeny komunikacni stav."""

    stav = nacti_json(
        COMMUNICATION_STATE_PATH
    )

    if stav is None:
        return vytvor_prazdny_stav()

    return stav


def proved_jeden_pruchod() -> dict[str, Any]:
    """Vytvori a ulozi jeden komunikacni stav."""

    stav = vytvor_komunikacni_stav()
    uloz_komunikacni_stav(stav)

    return stav


def main() -> None:
    """Prubezne aktualizuje komunikacni stav."""

    logger.info(
        "Communication Manager spusten. "
        "USB inventar: %s, vystup: %s, "
        "interval: %s s.",
        USB_INVENTORY_PATH,
        COMMUNICATION_STATE_PATH,
        SCAN_INTERVAL_SECONDS,
    )

    posledni_otisk: str | None = None

    while True:
        try:
            stav = vytvor_komunikacni_stav()
            otisk = vypocitej_otisk(stav)

            if otisk != posledni_otisk:
                uloz_komunikacni_stav(stav)
                posledni_otisk = otisk

                logger.info(
                    "Komunikacni stav aktualizovan. "
                    "Komunikatory: %s, pripojene: %s.",
                    stav["summary"][
                        "communicators_total"
                    ],
                    stav["summary"][
                        "communicators_connected"
                    ],
                )

        except Exception:
            logger.exception(
                "Aktualizace komunikacniho stavu "
                "skoncila chybou."
            )

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
