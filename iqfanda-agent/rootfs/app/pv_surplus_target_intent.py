from __future__ import annotations

import json
import os

from datetime import datetime
from datetime import timezone

from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MAX_TARGET_INTENTS = 5

VALID_ACTIONS = {
    "on",
    "off",
}

DEFAULT_PATH = Path(
    os.getenv(
        "IQF_PV_SURPLUS_TARGET_INTENTS_PATH",
        "/data/pv_surplus_target_intents.json",
    )
)


def _parse_datetime(
    value: Any,
) -> datetime:
    normalized = str(
        value or ""
    ).strip()

    if not normalized:
        raise ValueError(
            "valid_until must not be empty."
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


def _normalize_resource_key(
    value: Any,
) -> str:
    resource_key = str(
        value or ""
    ).strip()

    prefix = "pv_surplus_target:"

    if not resource_key.startswith(
        prefix
    ):
        raise ValueError(
            "Invalid target resource_key."
        )

    target_id = resource_key[
        len(prefix):
    ].strip()

    if not target_id:
        raise ValueError(
            "Target ID missing in resource_key."
        )

    return resource_key


def _normalize_output_reference(
    value: Any,
) -> str:
    reference = str(
        value or ""
    ).strip()

    if not reference.startswith(
        "switch."
    ):
        raise ValueError(
            "Target output must be switch.*."
        )

    return reference


def _empty_store() -> dict[str, Any]:
    return {
        "schema_version":
            SCHEMA_VERSION,

        "intents": {},
    }


def _read_store(
    path: Path,
) -> dict[str, Any]:
    if not path.exists():
        return _empty_store()

    try:
        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        raise ValueError(
            "Intent store is unreadable."
        )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Intent store must be an object."
        )

    if payload.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        raise ValueError(
            "Unsupported intent store schema."
        )

    intents = payload.get(
        "intents"
    )

    if not isinstance(
        intents,
        dict,
    ):
        raise ValueError(
            "Intent map missing."
        )

    return payload


def _is_valid_now(
    intent: dict[str, Any],
    now: datetime,
) -> bool:
    try:
        valid_until = _parse_datetime(
            intent.get(
                "valid_until"
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        return False

    return valid_until > now


def load_pv_surplus_target_intents(
    *,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    if now is None:
        now = datetime.now(
            timezone.utc
        )

    store_path = (
        path
        if path is not None
        else DEFAULT_PATH
    )

    store = _read_store(
        store_path
    )

    result: dict[
        str,
        dict[str, Any],
    ] = {}

    for (
        resource_key,
        intent,
    ) in store[
        "intents"
    ].items():

        if not isinstance(
            intent,
            dict,
        ):
            continue

        if (
            intent.get(
                "resource_key"
            )
            != resource_key
        ):
            continue

        if not _is_valid_now(
            intent,
            now,
        ):
            continue

        result[
            resource_key
        ] = intent

    return result


def load_pv_surplus_target_intent(
    *,
    resource_key: str,
    output_reference: str,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    normalized_resource = (
        _normalize_resource_key(
            resource_key
        )
    )

    normalized_output = (
        _normalize_output_reference(
            output_reference
        )
    )

    intents = (
        load_pv_surplus_target_intents(
            now=now,
            path=path,
        )
    )

    intent = intents.get(
        normalized_resource
    )

    if intent is None:
        return None

    if (
        str(
            intent.get(
                "output_reference"
            )
            or ""
        ).strip()
        != normalized_output
    ):
        return None

    return intent


def save_pv_surplus_target_intent(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    if now is None:
        now = datetime.now(
            timezone.utc
        )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Intent payload must be an object."
        )

    store_path = (
        path
        if path is not None
        else DEFAULT_PATH
    )

    resource_key = (
        _normalize_resource_key(
            payload.get(
                "resource_key"
            )
        )
    )

    output_reference = (
        _normalize_output_reference(
            payload.get(
                "output_reference"
            )
        )
    )

    action = str(
        payload.get(
            "action"
        )
        or ""
    ).strip().lower()

    if action not in VALID_ACTIONS:
        raise ValueError(
            "Unsupported target action."
        )

    reason = str(
        payload.get(
            "reason"
        )
        or ""
    ).strip()

    valid_until = (
        _parse_datetime(
            payload.get(
                "valid_until"
            )
        )
    )

    if valid_until <= now:
        raise ValueError(
            "Target intent is already expired."
        )

    store = _read_store(
        store_path
    )

    active = {}

    for (
        existing_resource,
        existing_intent,
    ) in store[
        "intents"
    ].items():

        if (
            isinstance(
                existing_intent,
                dict,
            )
            and _is_valid_now(
                existing_intent,
                now,
            )
        ):
            active[
                existing_resource
            ] = existing_intent

    #
    # One physical output may have only one
    # active target owner.
    #
    for (
        existing_resource,
        existing_intent,
    ) in active.items():

        if (
            existing_resource
            != resource_key
            and str(
                existing_intent.get(
                    "output_reference"
                )
                or ""
            ).strip()
            == output_reference
        ):
            raise ValueError(
                "Output is already owned "
                "by another active target."
            )

    if (
        resource_key
        not in active
        and len(active)
        >= MAX_TARGET_INTENTS
    ):
        raise ValueError(
            "Maximum number of active "
            "target intents reached."
        )

    stored = {
        "schema_version":
            SCHEMA_VERSION,

        "resource_key":
            resource_key,

        "output_reference":
            output_reference,

        "action":
            action,

        "desired_on":
            action == "on",

        "reason":
            reason,

        "valid_until":
            valid_until.isoformat(),

        "received_at":
            now.isoformat(),
    }

    active[
        resource_key
    ] = stored

    serialized = {
        "schema_version":
            SCHEMA_VERSION,

        "intents":
            active,
    }

    store_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = (
        store_path.with_suffix(
            store_path.suffix
            + ".tmp"
        )
    )

    temporary.write_text(
        json.dumps(
            serialized,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    temporary.replace(
        store_path
    )

    return stored
