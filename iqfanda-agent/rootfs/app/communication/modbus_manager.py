"""Spravce Modbus RTU komunikace."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from communication.json_utils import nacti_json


COMMUNICATION_STATE_PATH = Path(
    os.getenv(
        "IQF_COMMUNICATION_STATE_PATH",
        "/config/communication.json",
    )
)


logger = logging.getLogger(__name__)


registrovane_porty: dict[str, dict[str, Any]] = {}


def ziskej_nebo_registruj_port(
    cesta_portu: str,
) -> dict[str, Any]:
    """Vrati existujici nebo vytvori novy zaznam serioveho portu."""

    normalizovana_cesta = cesta_portu.strip()

    if not normalizovana_cesta:
        raise ValueError(
            "Cesta serioveho portu nesmi byt prazdna."
        )

    zaznam = registrovane_porty.get(
        normalizovana_cesta
    )

    if zaznam is None:
        zaznam = {
            "path": normalizovana_cesta,
            "status": "registered",
            "serial_port": None,
        }

        registrovane_porty[
            normalizovana_cesta
        ] = zaznam

        logger.info(
            "Modbus Manager: registrovan seriovy port %s.",
            normalizovana_cesta,
        )

    return zaznam


def je_modbus_rtu_komunikator(
    komunikator: dict[str, Any],
) -> bool:
    """Overi podporu protokolu Modbus RTU."""

    schopnosti = komunikator.get("capabilities")

    if not isinstance(schopnosti, list):
        return False

    return "modbus_rtu" in schopnosti


def ziskej_modbus_rtu_komunikatory(
    komunikacni_stav: dict[str, Any],
) -> list[dict[str, Any]]:
    """Vrati pouze komunikatory s podporou Modbus RTU."""

    komunikatory = komunikacni_stav.get(
        "communicators"
    )

    if not isinstance(komunikatory, list):
        return []

    return [
        komunikator
        for komunikator in komunikatory
        if (
            isinstance(komunikator, dict)
            and je_modbus_rtu_komunikator(
                komunikator
            )
        )
    ]


def proved_jeden_pruchod() -> list[dict[str, Any]]:
    """Nacte Modbus RTU komunikatory a zaregistruje jejich porty."""

    komunikacni_stav = nacti_json(
        COMMUNICATION_STATE_PATH
    )

    if komunikacni_stav is None:
        logger.warning(
            "Modbus Manager: komunikacni stav neni dostupny."
        )
        return []

    komunikatory = ziskej_modbus_rtu_komunikatory(
        komunikacni_stav
    )

    for komunikator in komunikatory:
        cesta_portu = komunikator.get(
            "preferred_path"
        )

        if not isinstance(cesta_portu, str):
            continue

        if not cesta_portu.strip():
            continue

        ziskej_nebo_registruj_port(
            cesta_portu
        )

    logger.info(
        "Modbus Manager: nalezeno %s Modbus RTU komunikatoru, "
        "registrovano %s portu.",
        len(komunikatory),
        len(registrovane_porty),
    )

    return komunikatory


def main() -> None:
    """Provede jeden kontrolni pruchod Modbus Manageru."""

    logger.info(
        "Modbus Manager spusten."
    )

    proved_jeden_pruchod()


if __name__ == "__main__":
    main()
