"""Spolecne pomocne funkce pro praci s JSON soubory."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

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
