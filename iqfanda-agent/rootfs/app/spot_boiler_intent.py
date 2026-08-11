"""Docasny spotovy pozadavek pro bojler."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SPOT_BOILER_INTENT_PATH = Path(
    os.getenv(
        "IQF_SPOT_BOILER_INTENT_PATH",
        "/data/spot_boiler_intent.json",
    )
)


VALID_ACTIONS = {
    "heat_now",
    "emergency_heat",
    "off",
}


def _parse_datetime(value: Any) -> datetime:
    normalized = str(value or "").strip()

    if not normalized:
        raise ValueError(
            "valid_until nesmi byt prazdne."
        )

    parsed = datetime.fromisoformat(
        normalized.replace(
            "Z",
            "+00:00",
        )
    )

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(timezone.utc)


def save_spot_boiler_intent(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Overi a atomicky ulozi spotovy pozadavek.

    Tato funkce fyzicky neovlada zadny vystup.
    """

    if now is None:
        now = datetime.now(timezone.utc)

    if not isinstance(payload, dict):
        raise ValueError(
            "Payload musi byt objekt."
        )

    resource_key = str(
        payload.get("resource_key") or ""
    ).strip()

    output_reference = str(
        payload.get("output_reference") or ""
    ).strip()

    action = str(
        payload.get("action") or ""
    ).strip()

    reason = str(
        payload.get("reason") or ""
    ).strip()

    if not resource_key.startswith(
        "pv_surplus_target:"
    ):
        raise ValueError(
            "Neplatny resource_key."
        )

    if not output_reference.startswith(
        "switch."
    ):
        raise ValueError(
            "Spotovy bojler smi cilit pouze "
            "na switch.* entitu."
        )

    if action not in VALID_ACTIONS:
        raise ValueError(
            f"Nepodporovana spot akce: {action}"
        )

    valid_until = _parse_datetime(
        payload.get("valid_until")
    )

    if valid_until <= now:
        raise ValueError(
            "Spotovy pozadavek je jiz prosly."
        )

    desired_on = action in {
        "heat_now",
        "emergency_heat",
    }

    stored = {
        "schema_version": 1,
        "resource_key": resource_key,
        "output_reference": output_reference,
        "action": action,
        "desired_on": desired_on,
        "reason": reason,
        "valid_until": valid_until.isoformat(),
        "received_at": now.isoformat(),
    }

    SPOT_BOILER_INTENT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = (
        SPOT_BOILER_INTENT_PATH
        .with_suffix(".tmp")
    )

    temporary.write_text(
        json.dumps(
            stored,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temporary.replace(
        SPOT_BOILER_INTENT_PATH
    )

    return stored


def load_spot_boiler_intent(
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """
    Vrati pouze platny spotovy pozadavek.
    Prosly pozadavek se nepouzije.
    """

    if now is None:
        now = datetime.now(timezone.utc)

    if not SPOT_BOILER_INTENT_PATH.exists():
        return None

    try:
        raw = json.loads(
            SPOT_BOILER_INTENT_PATH.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return None

    if not isinstance(raw, dict):
        return None

    try:
        valid_until = _parse_datetime(
            raw.get("valid_until")
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    if valid_until <= now:
        return None

    return raw


def combine_boiler_requests(
    *,
    pv_should_be_on: bool,
    spot_intent: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Jediny rozhodovaci bod pred fyzickym aktuatorem.

    Prebytek z FVE ma smysl vzdy vyuzit.
    Spot muze pridat dalsi duvod k ohrevu.
    """

    pv_on = bool(pv_should_be_on)

    spot_on = (
        isinstance(spot_intent, dict)
        and spot_intent.get("desired_on") is True
    )

    should_be_on = pv_on or spot_on

    if pv_on and spot_on:
        source = "pv_surplus_and_spot"
    elif pv_on:
        source = "pv_surplus"
    elif spot_on:
        source = "spot"
    else:
        source = "none"

    return {
        "should_be_on": should_be_on,
        "source": source,
        "pv_should_be_on": pv_on,
        "spot_should_be_on": spot_on,
    }
