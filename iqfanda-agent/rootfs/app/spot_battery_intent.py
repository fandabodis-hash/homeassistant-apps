"""Docasny spotovy pozadavek pro nabijeni baterie."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SPOT_BATTERY_INTENT_PATH = Path(
    os.getenv(
        "IQF_SPOT_BATTERY_INTENT_PATH",
        "/data/spot_battery_intent.json",
    )
)


VALID_ACTIONS = {
    "auto",
    "charge_grid",
}


MAXIMUM_ALLOWED_CHARGE_POWER_W = 5000


def _parse_datetime(
    value: Any,
) -> datetime:
    normalized = str(
        value or ""
    ).strip()

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

    return parsed.astimezone(
        timezone.utc
    )


def _required_float(
    payload: dict[str, Any],
    field_name: str,
) -> float:
    value = payload.get(
        field_name
    )

    if value is None:
        raise ValueError(
            f"V prikazu chybi pole: {field_name}"
        )

    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            f"Pole {field_name} neni cislo."
        ) from exc


def _required_int(
    payload: dict[str, Any],
    field_name: str,
) -> int:
    value = payload.get(
        field_name
    )

    if value is None:
        raise ValueError(
            f"V prikazu chybi pole: {field_name}"
        )

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            f"Pole {field_name} neni cele cislo."
        ) from exc


def save_spot_battery_intent(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Overi a atomicky ulozi spotovy battery intent.

    Tato funkce zamerne neprovadi zadny
    Modbus zapis.
    """

    if now is None:
        now = datetime.now(
            timezone.utc
        )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Payload musi byt objekt."
        )

    action = str(
        payload.get("action")
        or ""
    ).strip()

    reason = str(
        payload.get("reason")
        or ""
    ).strip()

    if action not in VALID_ACTIONS:
        raise ValueError(
            f"Nepodporovana battery akce: {action}"
        )

    target_soc_percent = _required_float(
        payload,
        "target_soc_percent",
    )

    current_soc_percent = _required_float(
        payload,
        "current_soc_percent",
    )

    requested_charge_power_w = _required_int(
        payload,
        "requested_charge_power_w",
    )

    allowed_charge_power_w = _required_int(
        payload,
        "allowed_charge_power_w",
    )

    battery_capacity_kwh = _required_float(
        payload,
        "battery_capacity_kwh",
    )

    if not (
        0.0
        <= target_soc_percent
        <= 100.0
    ):
        raise ValueError(
            "target_soc_percent musi byt 0 az 100."
        )

    if not (
        0.0
        <= current_soc_percent
        <= 100.0
    ):
        raise ValueError(
            "current_soc_percent musi byt 0 az 100."
        )

    if battery_capacity_kwh <= 0:
        raise ValueError(
            "battery_capacity_kwh musi byt kladna."
        )

    if requested_charge_power_w < 0:
        raise ValueError(
            "requested_charge_power_w nesmi byt zaporny."
        )

    if allowed_charge_power_w < 0:
        raise ValueError(
            "allowed_charge_power_w nesmi byt zaporny."
        )

    if (
        requested_charge_power_w
        > MAXIMUM_ALLOWED_CHARGE_POWER_W
    ):
        raise ValueError(
            "requested_charge_power_w prekrocil "
            "bezpecnostni limit 5000 W."
        )

    if (
        allowed_charge_power_w
        > MAXIMUM_ALLOWED_CHARGE_POWER_W
    ):
        raise ValueError(
            "allowed_charge_power_w prekrocil "
            "bezpecnostni limit 5000 W."
        )

    if action == "auto":
        if (
            requested_charge_power_w != 0
            or allowed_charge_power_w != 0
        ):
            raise ValueError(
                "Akce auto musi mit vykon 0 W."
            )

    if action == "charge_grid":
        if allowed_charge_power_w <= 0:
            raise ValueError(
                "charge_grid vyzaduje kladny "
                "allowed_charge_power_w."
            )

        if (
            allowed_charge_power_w
            > requested_charge_power_w
        ):
            raise ValueError(
                "Povoleny vykon nesmi byt vyssi "
                "nez pozadovany vykon."
            )

        if current_soc_percent >= target_soc_percent:
            raise ValueError(
                "charge_grid neni povolen pri "
                "dosazenem cilovem SOC."
            )

    valid_until = _parse_datetime(
        payload.get(
            "valid_until"
        )
    )

    if valid_until <= now:
        raise ValueError(
            "Battery intent je jiz prosly."
        )

    stored = {
        "schema_version": 1,
        "action": action,
        "reason": reason,
        "requested_charge_power_w": (
            requested_charge_power_w
        ),
        "allowed_charge_power_w": (
            allowed_charge_power_w
        ),
        "target_soc_percent": (
            target_soc_percent
        ),
        "current_soc_percent": (
            current_soc_percent
        ),
        "battery_capacity_kwh": (
            battery_capacity_kwh
        ),
        "current_interval_selected": bool(
            payload.get(
                "current_interval_selected"
            )
        ),
        "valid_until": (
            valid_until.isoformat()
        ),
        "received_at": (
            now.isoformat()
        ),
    }

    SPOT_BATTERY_INTENT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = (
        SPOT_BATTERY_INTENT_PATH
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
        SPOT_BATTERY_INTENT_PATH
    )

    return stored


def load_spot_battery_intent(
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """
    Vrati pouze platny battery intent.

    Prosly intent se nesmi pouzit.
    """

    if now is None:
        now = datetime.now(
            timezone.utc
        )

    if not SPOT_BATTERY_INTENT_PATH.exists():
        return None

    try:
        raw = json.loads(
            SPOT_BATTERY_INTENT_PATH.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return None

    if not isinstance(
        raw,
        dict,
    ):
        return None

    try:
        valid_until = _parse_datetime(
            raw.get(
                "valid_until"
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    if valid_until <= now:
        return None

    if raw.get("action") not in VALID_ACTIONS:
        return None

    return raw
