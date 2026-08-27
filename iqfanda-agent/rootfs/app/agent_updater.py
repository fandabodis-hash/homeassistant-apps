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


SUPERVISOR_BASE_URL = (
    os.getenv(
        "IQF_SUPERVISOR_BASE_URL",
        "http://supervisor",
    )
    .strip()
    .rstrip("/")
)

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
UPDATE_ENTITY_WAIT_SECONDS = 30

VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+$"
)


class AgentUpdateError(RuntimeError):
    """Chyba kontrolovane aktualizace Agentu."""


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


def supervisor_request(
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
            "TNG-IQ-FANDA-Agent-Updater"
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
        SUPERVISOR_BASE_URL + path,
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
            "Supervisor API vratilo "
            f"HTTP {exc.code}: {body}"
        ) from exc

    except (
        error.URLError,
        TimeoutError,
        OSError,
    ) as exc:
        raise AgentUpdateError(
            "Supervisor API neni dostupne: "
            f"{exc}"
        ) from exc

    try:
        response_payload = json.loads(
            raw.decode("utf-8")
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise AgentUpdateError(
            "Supervisor vratil neplatny JSON."
        ) from exc

    if not isinstance(
        response_payload,
        dict,
    ):
        raise AgentUpdateError(
            "Supervisor vratil "
            "neocekavany format."
        )

    if response_payload.get(
        "result"
    ) != "ok":
        raise AgentUpdateError(
            str(
                response_payload.get(
                    "message"
                )
                or response_payload.get(
                    "error"
                )
                or (
                    "Supervisor operaci "
                    "odmitl."
                )
            )
        )

    return response_payload.get(
        "data"
    )


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
        raise AgentUpdateError(
            "Home Assistant Core API "
            f"neni dostupne: {exc}"
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


def refresh_supervisor_store() -> None:
    supervisor_request(
        method="POST",
        path="/store/reload",
    )


def get_self_addon_info() -> dict[str, Any]:
    data = supervisor_request(
        method="GET",
        path="/addons/self/info",
    )

    if not isinstance(data, dict):
        raise AgentUpdateError(
            "Supervisor nevratil "
            "informace o Agentu."
        )

    return data


def _find_update_entity(
    *,
    current_version: str,
    target_version: str,
) -> dict[str, Any] | None:
    states = home_assistant_request(
        method="GET",
        path="/states",
    )

    if not isinstance(states, list):
        raise AgentUpdateError(
            "Home Assistant /states "
            "nevratil seznam."
        )

    candidates: list[
        dict[str, Any]
    ] = []

    for state in states:
        if not isinstance(
            state,
            dict,
        ):
            continue

        entity_id = str(
            state.get("entity_id")
            or ""
        ).strip()

        if not entity_id.startswith(
            "update."
        ):
            continue

        attributes = state.get(
            "attributes"
        )

        if not isinstance(
            attributes,
            dict,
        ):
            continue

        title = str(
            attributes.get("title")
            or ""
        ).strip()

        installed = str(
            attributes.get(
                "installed_version"
            )
            or ""
        ).strip()

        latest = str(
            attributes.get(
                "latest_version"
            )
            or ""
        ).strip()

        if title != AGENT_NAME:
            continue

        if installed != current_version:
            continue

        if latest != target_version:
            continue

        candidates.append(
            {
                "entity_id": entity_id,
                "state": state.get(
                    "state"
                ),
                "supported_features": (
                    attributes.get(
                        "supported_features"
                    )
                ),
                "installed_version": (
                    installed
                ),
                "latest_version": latest,
            }
        )

    if len(candidates) > 1:
        raise AgentUpdateError(
            "Home Assistant obsahuje "
            "vice odpovidajicich update entit."
        )

    if not candidates:
        return None

    return candidates[0]


def wait_for_update_entity(
    *,
    current_version: str,
    target_version: str,
) -> dict[str, Any]:
    deadline = (
        time.monotonic()
        + UPDATE_ENTITY_WAIT_SECONDS
    )

    while True:
        candidate = _find_update_entity(
            current_version=current_version,
            target_version=target_version,
        )

        if candidate is not None:
            return candidate

        if time.monotonic() >= deadline:
            raise AgentUpdateError(
                "Home Assistant update entita "
                f"zatim nevidi verzi "
                f"{target_version}."
            )

        time.sleep(1)


def validate_update_target(
    target_version: str,
) -> dict[str, Any]:
    target = str(
        target_version or ""
    ).strip()

    if not VERSION_PATTERN.fullmatch(
        target
    ):
        raise AgentUpdateError(
            "Cilova verze nema "
            "povoleny format X.Y.Z."
        )

    #
    # Vynutime nacteni posledni verze
    # lokalniho repository.
    #
    refresh_supervisor_store()

    info = get_self_addon_info()

    slug = str(
        info.get("slug") or ""
    ).strip()

    current = str(
        info.get("version") or ""
    ).strip()

    latest = str(
        info.get("version_latest")
        or ""
    ).strip()

    image_version = (
        read_installed_agent_version()
    )

    if not slug:
        raise AgentUpdateError(
            "Supervisor nevratil slug Agentu."
        )

    if not current:
        raise AgentUpdateError(
            "Supervisor nevratil "
            "aktualni verzi Agentu."
        )

    if image_version != current:
        raise AgentUpdateError(
            "Supervisor verze a /app/VERSION "
            "se neshoduji. "
            f"supervisor={current} "
            f"image={image_version}"
        )

    if current == target:
        return {
            "slug": slug,
            "current_version": current,
            "latest_version": latest,
            "target_version": target,
            "already_current": True,
            "update_entity_id": None,
        }

    if latest != target:
        raise AgentUpdateError(
            "Cilova verze neodpovida "
            "posledni verzi repository. "
            f"target={target} latest={latest}"
        )

    if info.get(
        "update_available"
    ) is not True:
        raise AgentUpdateError(
            "Supervisor nehlasi "
            "dostupnou aktualizaci."
        )

    update_entity = (
        wait_for_update_entity(
            current_version=current,
            target_version=target,
        )
    )

    entity_id = str(
        update_entity["entity_id"]
    )

    supported_features = (
        update_entity.get(
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

    return {
        "slug": slug,
        "current_version": current,
        "latest_version": latest,
        "target_version": target,
        "already_current": False,
        "update_entity_id": entity_id,
        "supported_features": (
            supported_features_int
        ),
    }


def trigger_agent_update(
    *,
    entity_id: str,
    backup: bool,
) -> Any:
    normalized_entity_id = str(
        entity_id or ""
    ).strip()

    if not normalized_entity_id.startswith(
        "update."
    ):
        raise AgentUpdateError(
            "Neplatne update entity_id."
        )

    return home_assistant_request(
        method="POST",
        path="/services/update/install",
        payload={
            "entity_id": (
                normalized_entity_id
            ),
            "backup": bool(backup),
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
        payload = json.load(file)

    if not isinstance(payload, dict):
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
        marker.get("requested_at")
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