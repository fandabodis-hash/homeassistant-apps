"""Runtime diagnostika RS485 komunikatoru TNG IQ FANDA."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from communication.json_utils import nacti_json
from communication.models import aktualni_cas_iso


COMMUNICATION_STATE_PATH = Path(
    os.getenv(
        "IQF_COMMUNICATION_STATE_PATH",
        "/config/communication.json",
    )
)

RS485_RUNTIME_STATE_PATH = Path(
    os.getenv(
        "IQF_RS485_RUNTIME_STATE_PATH",
        "/config/rs485_runtime.json",
    )
)

DEFAULT_BAUDRATE = 9600
DEFAULT_TIMEOUT_SECONDS = 0.25


logger = logging.getLogger(__name__)


def vypocitej_otisk_zdroje(
    komunikacni_stav: dict[str, Any],
) -> str:
    """Vypocita stabilni otisk zdrojoveho komunikacniho stavu."""

    data_pro_otisk = dict(komunikacni_stav)
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


def je_rs485_komunikator(
    komunikator: dict[str, Any],
) -> bool:
    """Overi schopnost komunikatoru pracovat s RS485."""

    schopnosti = komunikator.get("capabilities")

    if not isinstance(schopnosti, list):
        return False

    return "rs485" in schopnosti


def ziskej_rs485_komunikatory(
    komunikacni_stav: dict[str, Any],
) -> list[dict[str, Any]]:
    """Vrati pouze RS485 komunikatory."""

    komunikatory = komunikacni_stav.get(
        "communicators"
    )

    if not isinstance(komunikatory, list):
        return []

    vysledek = [
        komunikator
        for komunikator in komunikatory
        if (
            isinstance(komunikator, dict)
            and je_rs485_komunikator(komunikator)
        )
    ]

    vysledek.sort(
        key=lambda komunikator: str(
            komunikator.get("communicator_id") or ""
        )
    )

    return vysledek


def over_port(
    cesta_portu: str | None,
    baudrate: int = DEFAULT_BAUDRATE,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Overi existenci a otevreni serioveho portu."""

    if not cesta_portu:
        return {
            "path": None,
            "path_exists": False,
            "serial_library_available": None,
            "open_test": "not_attempted",
            "error": "Komunikator nema preferovanou cestu.",
        }

    cesta = Path(cesta_portu)
    existuje = cesta.exists()

    if not existuje:
        return {
            "path": cesta_portu,
            "path_exists": False,
            "serial_library_available": None,
            "open_test": "not_attempted",
            "error": "Cesta serioveho portu neexistuje.",
        }

    try:
        import serial
    except ImportError:
        return {
            "path": cesta_portu,
            "path_exists": True,
            "serial_library_available": False,
            "open_test": "not_attempted",
            "error": "Knihovna PySerial neni dostupna.",
        }

    try:
        port = serial.Serial(
            port=cesta_portu,
            baudrate=baudrate,
            timeout=timeout_seconds,
            write_timeout=timeout_seconds,
        )

        port.close()

    except Exception as chyba:
        return {
            "path": cesta_portu,
            "path_exists": True,
            "serial_library_available": True,
            "open_test": "failed",
            "error": str(chyba),
        }

    return {
        "path": cesta_portu,
        "path_exists": True,
        "serial_library_available": True,
        "open_test": "passed",
        "error": None,
    }


def vytvor_runtime_stav(
    komunikacni_stav: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Vytvori diagnosticky stav RS485 runtime."""

    if komunikacni_stav is None:
        komunikacni_stav = nacti_json(
            COMMUNICATION_STATE_PATH
        )

    if komunikacni_stav is None:
        return {
            "schema_version": 1,
            "generated_at": aktualni_cas_iso(),
            "source_status": "communication_unavailable",
            "communicators": [],
            "summary": {
                "total": 0,
                "paths_available": 0,
                "open_test_passed": 0,
                "open_test_failed": 0,
            },
        }

    runtime_komunikatory: list[dict[str, Any]] = []

    for komunikator in ziskej_rs485_komunikatory(
        komunikacni_stav
    ):
        kontrola = over_port(
            komunikator.get("preferred_path")
        )

        runtime_komunikatory.append(
            {
                "communicator_id": komunikator.get(
                    "communicator_id"
                ),
                "type": komunikator.get("type"),
                "manufacturer": komunikator.get(
                    "manufacturer"
                ),
                "product": komunikator.get("product"),
                "connected": komunikator.get(
                    "connected"
                ),
                "preferred_path": komunikator.get(
                    "preferred_path"
                ),
                "runtime": kontrola,
            }
        )

    return {
        "schema_version": 1,
        "generated_at": aktualni_cas_iso(),
        "source_status": "available",
        "source_communication_generated_at": (
            komunikacni_stav.get("generated_at")
        ),
        "communicators": runtime_komunikatory,
        "summary": {
            "total": len(runtime_komunikatory),
            "paths_available": sum(
                1
                for komunikator in runtime_komunikatory
                if komunikator["runtime"][
                    "path_exists"
                ]
            ),
            "open_test_passed": sum(
                1
                for komunikator in runtime_komunikatory
                if komunikator["runtime"][
                    "open_test"
                ] == "passed"
            ),
            "open_test_failed": sum(
                1
                for komunikator in runtime_komunikatory
                if komunikator["runtime"][
                    "open_test"
                ] == "failed"
            ),
        },
    }


def uloz_runtime_stav(
    stav: dict[str, Any],
) -> None:
    """Atomicky ulozi RS485 runtime stav."""

    RS485_RUNTIME_STATE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    docasna_cesta = (
        RS485_RUNTIME_STATE_PATH.with_suffix(
            RS485_RUNTIME_STATE_PATH.suffix
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
        RS485_RUNTIME_STATE_PATH,
    )


def proved_jeden_pruchod() -> dict[str, Any]:
    """Vytvori a ulozi jeden RS485 runtime stav."""

    stav = vytvor_runtime_stav()
    uloz_runtime_stav(stav)

    return stav


if __name__ == "__main__":
    print(
        json.dumps(
            proved_jeden_pruchod(),
            ensure_ascii=False,
            indent=2,
        )
    )
