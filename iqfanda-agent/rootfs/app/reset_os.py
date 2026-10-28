"""F52 - autorizovany jednorazovy Reset OS (restart jen Home Assistant Core).

Bez restartu Raspberry Pi, Supervisora, Agenta nebo ostatnich modelu.
Nevykonava shell ani jine vstupy z cloudu jako adresy nebo prikazy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import fcntl
import json
import os
import re
import tempfile
from typing import Any, Callable
from urllib import error, request
import uuid

COMMAND_TYPE = "reset_os"
SERIAL_PATTERN = re.compile(r"^F800711-TNG-[0-9]{5}$")
CONFIRMATION = "RESET_OS"
SOURCE = "admin_portal"
STATE_PATH = Path("/config/reset_os_last_attempt.json")
LOCK_PATH = Path("/config/reset_os.lock")
COOLDOWN = timedelta(minutes=10)
SUPERVISOR_INFO_URL = "http://supervisor/core/info"
SUPERVISOR_RESTART_URL = "http://supervisor/core/restart"


class ResetOsRejected(RuntimeError):
    """Blokace restartu pred jeho provedenim."""


class ResetOsUncertain(RuntimeError):
    """Prikaz mohl byt Supervisorem prijat, neopakovat automaticky."""


def _utc_date(raw: Any) -> datetime:
    if not isinstance(raw, str):
        raise ResetOsRejected("Chybi platnost prikazu.")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResetOsRejected("Neplatny format casu.") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ResetOsRejected("Cas musi mit casovou zonu.")
    return value.astimezone(timezone.utc)


def check_request(
    *,
    cloud_identity: dict[str, Any],
    manufacturing_serial: str | None,
    command_id: str,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, str]:
    """Striktně typovany whitelist, bez libovolneho kodu a bez ciziho modelu."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ResetOsRejected("Neplatny kontrolni cas.")
    if not isinstance(manufacturing_serial, str) or not SERIAL_PATTERN.fullmatch(manufacturing_serial):
        raise ResetOsRejected("Vyrobni seriove cislo neni validni.")
    if not isinstance(payload, dict) or set(payload) != {
        "serial_number", "device_uuid", "confirmation", "nonce", "expires_at", "source"
    }:
        raise ResetOsRejected("Neznamy nebo nekompletni format Reset OS.")
    if payload["serial_number"] != manufacturing_serial:
        raise ResetOsRejected("Prikaz je urcen jinemu seriovemu cislu.")
    if payload["confirmation"] != CONFIRMATION or payload["source"] != SOURCE:
        raise ResetOsRejected("Reset OS nebyl opravnenym zpusobem potvrzen.")
    try:
        cmd_uuid = uuid.UUID(str(command_id))
        nonce = uuid.UUID(str(payload["nonce"]))
        requested_uuid = uuid.UUID(str(payload["device_uuid"]))
        identity_uuid = uuid.UUID(str(cloud_identity.get("device_uuid") or ""))
    except (TypeError, AttributeError, ValueError) as exc:
        raise ResetOsRejected("Neplatna identita prikazu nebo zarizeni.") from exc
    if requested_uuid != identity_uuid or cmd_uuid == nonce:
        raise ResetOsRejected("Nesouhlasi jedinecna identita zarizeni.")
    expiry = _utc_date(payload["expires_at"])
    remaining = (expiry - now.astimezone(timezone.utc)).total_seconds()
    if not 0 < remaining <= 180:
        raise ResetOsRejected("Prikaz vyprsel nebo ma prilis dlouhou platnost.")
    return {
        "serial_number": manufacturing_serial,
        "device_uuid": str(identity_uuid),
        "command_id": str(cmd_uuid),
        "nonce": str(nonce),
    }


def check_cooldown(previous: dict[str, Any], checked: dict[str, str], now: datetime) -> None:
    """Jedna instalace muze byt restartovana max. jednou za 10 minut."""
    if not previous:
        return
    if str(previous.get("command_id") or "") == checked["command_id"]:
        raise ResetOsRejected("Tento reset uz byl jednou vyvolan.")
    when = _utc_date(previous.get("attempted_at"))
    elapsed = now.astimezone(timezone.utc) - when
    if elapsed < timedelta(seconds=-30):
        raise ResetOsRejected("Neplatny cas posledniho restartu.")
    if elapsed < COOLDOWN:
        raise ResetOsRejected("Restart OS je docasne blokovan bezpečnostnim limitem 10 minut.")


def _api(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        url, method=method, data=body,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json", "Content-Type": "application/json",
                 "User-Agent": "TNG-IQ-FANDA-Reset-OS"},
    )
    try:
        with request.urlopen(req, timeout=35) as response:
            raw = response.read(65536)
            if response.status != 200:
                raise ResetOsRejected("Supervisor vratil neocekavany HTTP status.")
    except error.HTTPError as exc:
        raise ResetOsRejected("Supervisor vratil HTTP %s." % exc.code) from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise ResetOsUncertain("Nejista odpoved Supervisora; restart neopakovat.") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ResetOsRejected("Supervisor vratil neocekavanou odpoved.") from exc
    if not isinstance(result, dict) or result.get("result") != "ok":
        raise ResetOsRejected("Supervisor operaci nepotvrdil.")
    return result


def _persist_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".reset_os_", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, sort_keys=True, ensure_ascii=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ResetOsRejected("Neplatny zaznam posledniho restartu.")
    return state


def execute_reset_os(
    *,
    identity: dict[str, Any],
    command_id: str,
    command_payload: dict[str, Any],
    submit_result: Callable[..., Any],
) -> None:
    """Vykona pouze whitelistovany reset OS na lokalnim Supervisoru."""
    supervisor_may_have_accepted = False
    recorded = False
    try:
        from host.identity import DeviceIdentityService
        permanent = DeviceIdentityService().get_serial_number()
        checked = check_request(
            cloud_identity=identity,
            manufacturing_serial=permanent,
            command_id=command_id,
            payload=command_payload,
        )
        token = str(os.getenv("SUPERVISOR_TOKEN") or "").strip()
        if not token:
            raise ResetOsRejected("Supervisor token neni dostupny.")

        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOCK_PATH.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            now = datetime.now(timezone.utc)
            check_cooldown(_read_state(STATE_PATH), checked, now)
            info = _api("GET", SUPERVISOR_INFO_URL, token)
            data = info.get("data")
            if isinstance(data, dict):
                state = str(data.get("state") or "").lower()
                if state and state != "started":
                    raise ResetOsRejected("Home Assistant Core neni ve stavu started.")
            _persist_state(STATE_PATH, {
                "serial_number": checked["serial_number"],
                "device_uuid": checked["device_uuid"],
                "command_id": checked["command_id"],
                "nonce": checked["nonce"],
                "attempted_at": now.isoformat(),
                "status": "request_attempted",
            })
            recorded = True
            # Jediny opravneny zapis do Supervisora: restart Core.
            supervisor_may_have_accepted = True
            _api("POST", SUPERVISOR_RESTART_URL, token,
                 {"safe_mode": False, "force": False})
    except Exception as exc:
        submit_result(
            identity=identity, command_id=command_id, status="failed",
            result={"executor": COMMAND_TYPE,
                    "phase": "rejected_or_uncertain",
                    "may_have_started": supervisor_may_have_accepted,
                    "attempt_recorded": recorded,
                    "repeat_automatically": False},
            error_message=str(exc)[:250],
        )
        return
    submit_result(
        identity=identity, command_id=command_id, status="succeeded",
        result={"executor": COMMAND_TYPE, "phase": "supervisor_accepted",
                "may_have_started": True, "core_running_verified_after": False,
                "serial_number": checked["serial_number"]},
        error_message=None,
    )
