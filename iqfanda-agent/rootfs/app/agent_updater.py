"""Bezpecna aktualizace TNG IQ FANDA Agentu pres Supervisor API."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request


SUPERVISOR_BASE_URL = (
    os.getenv(
        "IQF_SUPERVISOR_BASE_URL",
        "http://supervisor",
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

DEFAULT_TIMEOUT_SECONDS = 30


class AgentUpdateError(RuntimeError):
    pass


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


def validate_update_target(
    target_version: str,
) -> dict[str, Any]:
    target = str(
        target_version or ""
    ).strip()

    if not target:
        raise AgentUpdateError(
            "Cilova verze chybi."
        )

    if len(target) > 50:
        raise AgentUpdateError(
            "Cilova verze je prilis dlouha."
        )

    info = get_self_addon_info()

    slug = str(
        info.get("slug") or ""
    ).strip()

    current = str(
        info.get("version") or ""
    ).strip()

    latest = str(
        info.get("version_latest") or ""
    ).strip()

    if not slug:
        raise AgentUpdateError(
            "Supervisor nevratil slug Agentu."
        )

    if not current:
        raise AgentUpdateError(
            "Supervisor nevratil "
            "aktualni verzi Agentu."
        )

    if current == target:
        return {
            "slug": slug,
            "current_version": current,
            "latest_version": latest,
            "target_version": target,
            "already_current": True,
        }

    if latest != target:
        raise AgentUpdateError(
            "Cilova verze neodpovida "
            "verzi dostupne v Supervisoru. "
            f"target={target} latest={latest}"
        )

    if info.get(
        "update_available"
    ) is not True:
        raise AgentUpdateError(
            "Supervisor nehlasi "
            "dostupnou aktualizaci."
        )

    encoded_slug = parse.quote(
        slug,
        safe="",
    )

    supervisor_request(
        method="GET",
        path=(
            "/store/addons/"
            f"{encoded_slug}"
            "/availability"
        ),
    )

    return {
        "slug": slug,
        "current_version": current,
        "latest_version": latest,
        "target_version": target,
        "already_current": False,
    }


def trigger_agent_update(
    *,
    slug: str,
    backup: bool,
) -> str:
    encoded_slug = parse.quote(
        str(slug),
        safe="",
    )

    data = supervisor_request(
        method="POST",
        path=(
            "/store/addons/"
            f"{encoded_slug}"
            "/update"
        ),
        payload={
            "backup": bool(backup),
            "background": True,
        },
    )

    if not isinstance(data, dict):
        raise AgentUpdateError(
            "Supervisor update "
            "nevratil job data."
        )

    job_id = str(
        data.get("job_id") or ""
    ).strip()

    if not job_id:
        raise AgentUpdateError(
            "Supervisor update "
            "nevratil job_id."
        )

    return job_id


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
            prefix="pending-agent-update-",
            suffix=".tmp",
            dir=target_path.parent,
        )
    )

    temp_path = Path(temp_name)

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
            os.fsync(file.fileno())

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

    marker.update(changes)

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
            datetime.fromisoformat(raw)
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
            datetime.now(timezone.utc)
            - requested_at.astimezone(
                timezone.utc
            )
        ).total_seconds(),
    )