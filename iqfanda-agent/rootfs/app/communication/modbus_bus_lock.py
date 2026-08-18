"""Sdileny per-port zamek pro Modbus RTU komunikaci."""

from __future__ import annotations

import posixpath
import threading
from typing import Any


_REGISTRY_LOCK = threading.Lock()

_ZAMKY_MODBUS_SBERNIC: dict[str, Any] = {}


def normalizuj_cestu_modbus_sbernice(
    cesta: str,
) -> str:
    """Vrati stabilni normalizovany klic fyzicke Modbus sbernice."""
    if type(cesta) is not str:
        raise TypeError(
            "Cesta Modbus sbernice musi byt text."
        )

    hodnota = cesta.strip()

    if not hodnota:
        raise ValueError(
            "Cesta Modbus sbernice nesmi byt prazdna."
        )

    normalizovana = posixpath.normpath(
        hodnota
    )

    if not normalizovana.startswith(
        "/dev/serial/by-id/"
    ):
        raise ValueError(
            "Modbus sbernice musi pouzivat stabilni "
            "/dev/serial/by-id/ cestu."
        )

    return normalizovana


def ziskej_zamek_modbus_sbernice(
    cesta: str,
) -> Any:
    """
    Vrati sdileny zamek pro jednu fyzickou seriovou cestu.

    Stejna stabilni cesta vzdy vraci stejnou instanci.
    Ruzne cesty maji nezavisle zamky a mohou pracovat paralelne.
    """
    klic = normalizuj_cestu_modbus_sbernice(
        cesta
    )

    with _REGISTRY_LOCK:
        zamek = _ZAMKY_MODBUS_SBERNIC.get(
            klic
        )

        if zamek is None:
            zamek = threading.Lock()

            _ZAMKY_MODBUS_SBERNIC[
                klic
            ] = zamek

        return zamek