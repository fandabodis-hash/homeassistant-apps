"""Bezpecna vzdálena aktualizace TNG IQ FANDA Agentu."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


HOME_ASSISTANT_API_BASE_URL = (
    os.getenv(
        "IQF_HOME_ASSISTANT_API_BASE_URL",
        "http://supervisor/core/api",
    )
    .strip()
    .rstrip("/")
)

VERSION_PATH = Path(
    os.getenv(
        "IQF_VERSION_PATH",
        "/app/VERSION",
    )
)

PENDING_UPDATE_PATH = Path(
    os.getenv(
        "IQF_PENDING_AGENT_UPDATE_PATH",
        "/config/pending_agent_update.json",
    )
)

AGENT_NAME = "TNG IQ FANDA Agent"

DEFAULT_TIMEOUT_SECONDS = 30

UPDATE_ENTITY_REFRESH_TIMEOUT_SECONDS = 180
UPDATE_ENTITY_WAIT_SECONDS = 180

VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+$"
)


class AgentUpdateError(RuntimeError):
    """Chyba kontrolovane aktualizace Agentu."""


class AgentUpdateTransportError(
    AgentUpdateError
):
    """
    Transportni chyba, u ktere mohl
    vzdaleny pozadavek stale dobehnout.
    """


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def read_installed_agent_version(
    path: Path | None = None,
) -> str:
    version_path = (
        VERSION_PATH
        if path is None
        else Path(path)
    )

    version = version_path.read_text(
        encoding="utf-8-sig"
    ).strip()

    if not version:
        raise AgentUpdateError(
            "Soubor VERSION je prazdny."
        )

    return version


def _supervisor_token() -> str:
    token = str(
        os.getenv(
            "SUPERVISOR_TOKEN",
            "",
        )
        or ""
    ).strip()

    if not token:
        raise AgentUpdateError(
            "SUPERVISOR_TOKEN neni dostupny."
        )

    return token


def home_assistant_request(
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    data = None

    headers = {
        "Authorization": (
            f"Bearer {_supervisor_token()}"
        ),
        "Accept": "application/json",
        "User-Agent": (
            "TNG-IQ-FANDA-"
            "Home-Assistant-Updater"
        ),
    }

    if payload is not None:
        data = json.dumps(
            payload
        ).encode("utf-8")

        headers[
            "Content-Type"
        ] = "application/json"

    http_request = request.Request(
        HOME_ASSISTANT_API_BASE_URL
        + path,
        data=data,
        method=method,
        headers=headers,
    )

    try:
        with request.urlopen(
            http_request,
            timeout=timeout,
        ) as response:
            raw = response.read()

    except error.HTTPError as exc:
        body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise AgentUpdateError(
            "Home Assistant Core API "
            f"vratilo HTTP {exc.code}: "
            f"{body}"
        ) from exc

    except (
        error.URLError,
        TimeoutError,
        OSError,
    ) as exc:
        raise AgentUpdateTransportError(
            "Home Assistant Core API "
            f"transport selhal: {exc}"
        ) from exc

    if not raw:
        return None

    try:
        return json.loads(
            raw.decode("utf-8")
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise AgentUpdateError(
            "Home Assistant Core API "
            "vratilo neplatny JSON."
        ) from exc


def _get_update_states() -> list[dict[str, Any]]:
    states = home_assistant_request(
        method="GET",
        path="/states",
    )

    if not isinstance(
        states,
        list,
    ):
        raise AgentUpdateError(
            "Home Assistant /states "
            "nevratil seznam."
        )

    return [
        state
        for state in states
        if isinstance(
            state,
            dict,
        )
    ]


def _normalize_update_entity(
    state: dict[str, Any],
) -> dict[str, Any] | None:
    entity_id = str(
        state.get("entity_id")
        or ""
    ).strip()

    if not entity_id.startswith(
        "update."
    ):
        return None

    attributes = state.get(
        "attributes"
    )

    if not isinstance(
        attributes,
        dict,
    ):
        return None

    return {
        "entity_id": entity_id,
        "title": str(
            attributes.get("title")
            or ""
        ).strip(),
        "state": state.get(
            "state"
        ),
        "installed_version": str(
            attributes.get(
                "installed_version"
            )
            or ""
        ).strip(),
        "latest_version": str(
            attributes.get(
                "latest_version"
            )
            or ""
        ).strip(),
        "supported_features": (
            attributes.get(
                "supported_features"
            )
        ),
        "in_progress": (
            attributes.get(
                "in_progress"
            )
        ),
    }


def find_agent_update_entity(
    *,
    current_version: str,
) -> dict[str, Any]:
    candidates = []

    for state in _get_update_states():
        candidate = (
            _normalize_update_entity(
                state
            )
        )

        if candidate is None:
            continue

        if (
            candidate["title"]
            != AGENT_NAME
        ):
            continue

        if (
            candidate[
                "installed_version"
            ]
            != current_version
        ):
            continue

        candidates.append(
            candidate
        )

    if not candidates:
        raise AgentUpdateError(
            "Home Assistant update entita "
            "TNG IQ FANDA Agentu "
            "nebyla nalezena."
        )

    if len(candidates) > 1:
        raise AgentUpdateError(
            "Home Assistant obsahuje "
            "vice odpovidajicich update entit."
        )

    return candidates[0]


def refresh_update_entity_metadata(
    *,
    entity_id: str,
) -> str | None:
    """
    Vynuti refresh pres Home Assistant Core.

    Transportni timeout neznamena,
    ze refresh nebyl prijat. V takovem
    pripade pokracujeme pollingem stavu.
    """

    try:
        home_assistant_request(
            method="POST",
            path=(
                "/services/"
                "homeassistant/"
                "update_entity"
            ),
            payload={
                "entity_id": (
                    entity_id
                )
            },
            timeout=(
                UPDATE_ENTITY_REFRESH_TIMEOUT_SECONDS
            ),
        )

    except AgentUpdateTransportError as exc:
        return str(exc)

    return None


def wait_for_update_entity(
    *,
    entity_id: str,
    current_version: str,
    target_version: str,
    refresh_error: str | None = None,
) -> dict[str, Any]:
    deadline = (
        time.monotonic()
        + UPDATE_ENTITY_WAIT_SECONDS
    )

    last_latest = ""

    while True:
        matching = []

        for state in _get_update_states():
            candidate = (
                _normalize_update_entity(
                    state
                )
            )

            if candidate is None:
                continue

            if (
                candidate["entity_id"]
                != entity_id
            ):
                continue

            matching.append(
                candidate
            )

        if len(matching) != 1:
            raise AgentUpdateError(
                "Update entita zmenila "
                "nebo ztratila identitu."
            )

        candidate = matching[0]

        if (
            candidate["title"]
            != AGENT_NAME
        ):
            raise AgentUpdateError(
                "Update entita jiz nepatri "
                "TNG IQ FANDA Agentu."
            )

        installed = str(
            candidate[
                "installed_version"
            ]
        )

        latest = str(
            candidate[
                "latest_version"
            ]
        )

        last_latest = latest

        if installed != current_version:
            raise AgentUpdateError(
                "Nainstalovana verze se "
                "behem validace zmenila. "
                f"expected={current_version} "
                f"actual={installed}"
            )

        if latest == target_version:
            return candidate

        #
        # current == latest znamena,
        # ze Core jeste stale vidi
        # predchozi metadata.
        #
        if (
            latest
            and latest
            not in {
                current_version,
                target_version,
            }
        ):
            raise AgentUpdateError(
                "Cilova verze jiz neni "
                "posledni verzi dostupnou "
                "v Home Assistantu. "
                f"target={target_version} "
                f"latest={latest}"
            )

        if time.monotonic() >= deadline:
            detail = (
                f" Posledni latest={last_latest}."
            )

            if refresh_error:
                detail += (
                    " Refresh transport: "
                    + refresh_error
                )

            raise AgentUpdateError(
                "Home Assistant update entita "
                "v casovem limitu nevidi "
                f"verzi {target_version}."
                + detail
            )

        time.sleep(1)


def validate_update_target(
    target_version: str,
) -> dict[str, Any]:
    target = str(
        target_version
        or ""
    ).strip()

    if not VERSION_PATTERN.fullmatch(
        target
    ):
        raise AgentUpdateError(
            "Cilova verze nema "
            "povoleny format X.Y.Z."
        )

    current = (
        read_installed_agent_version()
    )

    if current == target:
        return {
            "current_version": current,
            "latest_version": current,
            "target_version": target,
            "already_current": True,
            "update_entity_id": None,
            "supported_features": None,
            "metadata_refresh_error": None,
        }

    #
    # Nejdrive najdeme svou entitu
    # podle title + SKUTECNE image verze.
    #
    entity = find_agent_update_entity(
        current_version=current
    )

    entity_id = str(
        entity["entity_id"]
    )

    #
    # Zadne prime /store/reload.
    #
    # Core si pri update_entity
    # sam synchronizuje Hassio
    # add-on coordinator.
    #
    refresh_error = (
        refresh_update_entity_metadata(
            entity_id=entity_id
        )
    )

    entity = wait_for_update_entity(
        entity_id=entity_id,
        current_version=current,
        target_version=target,
        refresh_error=refresh_error,
    )

    supported_features = (
        entity.get(
            "supported_features"
        )
    )

    try:
        supported_features_int = int(
            supported_features
        )

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise AgentUpdateError(
            "Update entita nema platne "
            "supported_features."
        ) from exc

    #
    # BACKUP feature = 8.
    #
    if (
        supported_features_int & 8
    ) != 8:
        raise AgentUpdateError(
            "Update entita nepodporuje "
            "zalohu pred aktualizaci."
        )

    in_progress = entity.get(
        "in_progress"
    )

    if in_progress not in (
        False,
        None,
    ):
        raise AgentUpdateError(
            "Update entita jiz hlasi "
            "probihajici aktualizaci."
        )

    return {
        "current_version": current,
        "latest_version": str(
            entity[
                "latest_version"
            ]
        ),
        "target_version": target,
        "already_current": False,
        "update_entity_id": (
            entity_id
        ),
        "supported_features": (
            supported_features_int
        ),
        "metadata_refresh_error": (
            refresh_error
        ),
    }


def trigger_agent_update(
    *,
    entity_id: str,
    backup: bool,
) -> Any:
    normalized_entity_id = str(
        entity_id
        or ""
    ).strip()

    if not normalized_entity_id.startswith(
        "update."
    ):
        raise AgentUpdateError(
            "Neplatne update entity_id."
        )

    if backup is not True:
        raise AgentUpdateError(
            "Vzdaleny Agent update "
            "vyzaduje backup=true."
        )

    return home_assistant_request(
        method="POST",
        path="/services/update/install",
        payload={
            "entity_id": (
                normalized_entity_id
            ),
            "backup": True,
        },
        #
        # Core muze cekat na Supervisor,
        # ale stary Agent muze byt mezitim
        # sam zastaven.
        #
        timeout=120,
    )


def load_pending_agent_update(
    path: Path | None = None,
) -> dict[str, Any] | None:
    target_path = (
        PENDING_UPDATE_PATH
        if path is None
        else Path(path)
    )

    if not target_path.exists():
        return None

    with target_path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        payload = json.load(
            file
        )

    if not isinstance(
        payload,
        dict,
    ):
        raise AgentUpdateError(
            "Pending update marker "
            "ma neplatny format."
        )

    return payload


def save_pending_agent_update(
    payload: dict[str, Any],
    path: Path | None = None,
) -> None:
    target_path = (
        PENDING_UPDATE_PATH
        if path is None
        else Path(path)
    )

    target_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    descriptor, temp_name = (
        tempfile.mkstemp(
            prefix=(
                "pending-agent-update-"
            ),
            suffix=".tmp",
            dir=target_path.parent,
        )
    )

    temp_path = Path(
        temp_name
    )

    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.write("\n")
            file.flush()
            os.fsync(
                file.fileno()
            )

        temp_path.replace(
            target_path
        )

    except Exception:
        temp_path.unlink(
            missing_ok=True
        )
        raise


def update_pending_agent_update(
    changes: dict[str, Any],
    path: Path | None = None,
) -> None:
    marker = load_pending_agent_update(
        path=path
    )

    if marker is None:
        raise AgentUpdateError(
            "Pending update marker neexistuje."
        )

    marker.update(
        changes
    )

    save_pending_agent_update(
        marker,
        path=path,
    )


def clear_pending_agent_update(
    path: Path | None = None,
) -> None:
    target_path = (
        PENDING_UPDATE_PATH
        if path is None
        else Path(path)
    )

    target_path.unlink(
        missing_ok=True
    )


def pending_update_age_seconds(
    marker: dict[str, Any],
) -> float | None:
    raw = str(
        marker.get(
            "requested_at"
        )
        or ""
    ).strip()

    if not raw:
        return None

    try:
        requested_at = (
            datetime.fromisoformat(
                raw
            )
        )

    except ValueError:
        return None

    if (
        requested_at.tzinfo is None
        or requested_at.utcoffset()
        is None
    ):
        return None

    return max(
        0.0,
        (
            datetime.now(
                timezone.utc
            )
            - requested_at.astimezone(
                timezone.utc
            )
        ).total_seconds(),
    )