"""F51: jednorazovy vzdaleny restart HA Core jen pro model 00011.

Tento modul nesmi ovladat hostitele, Supervisor ani ostatni modely.
Restart je zamerne bez opakovani pri timeoutu transportu.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib import error, request
import uuid

SERIAL = "F800711-TNG-00011"
COMMAND_TYPE = "home_assistant_core_restart_00011"
SOURCE = "f51_supervisor_core_restart"
CONFIRMATION = "RESTART_HA_CORE_ONLY"
MARKER_PATH = Path("/config/f51_ha_core_restart_00011_once.json")
API_URL = "http://supervisor/core/restart"
INFO_URL = "http://supervisor/core/info"


class RestartRejected(RuntimeError):
    """Bezpecnostni podminky nebyly splneny."""


def _datetime_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise RestartRejected("Neplatna platnost prikazu.")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RestartRejected("Neplatna platnost prikazu.") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise RestartRejected("Platnost musi obsahovat casovou zonu.")
    return dt.astimezone(timezone.utc)


def validate_restart_request(
    *,
    cloud_identity: dict[str, Any],
    permanent_serial: str | None,
    command_id: str,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, str]:
    """Cista validace pred jakoukoli interakci se Supervisorem."""
    now = now or datetime.now(timezone.utc)
    if permanent_serial != SERIAL:
        raise RestartRejected("Zarizeni neni model 00011.")
    if not isinstance(payload, dict) or set(payload) != {
        "target_serial", "device_uuid", "source", "confirmation", "nonce", "expires_at"
    }:
        raise RestartRejected("Neocekavane polozky prikazu.")
    if payload.get("target_serial") != SERIAL or payload.get("source") != SOURCE:
        raise RestartRejected("Nesouhlasi cil ani puvod prikazu.")
    if payload.get("confirmation") != CONFIRMATION:
        raise RestartRejected("Chybi souhlas s restartem samotneho HA Core.")
    expected_uuid = str(cloud_identity.get("device_uuid") or "").lower()
    if not expected_uuid or str(payload.get("device_uuid") or "").lower() != expected_uuid:
        raise RestartRejected("Cloudova identita zarizeni nesouhlasi.")
    try:
        cmd = uuid.UUID(str(command_id))
        nonce = uuid.UUID(str(payload["nonce"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RestartRejected("Neplatny identifikator nebo nonce.") from exc
    if cmd == nonce:
        raise RestartRejected("Nonce musi byt odlisny od ID prikazu.")
    deadline = _datetime_utc(payload["expires_at"])
    if not (now < deadline <= now + timedelta(seconds=180)):
        raise RestartRejected("Prikaz vyprsel nebo jeho platnost prekrocila 180 sekund.")
    return {"command_id": str(cmd), "device_uuid": expected_uuid, "nonce": str(nonce)}


def _supervisor_request(*, method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "TNG-IQ-FANDA-F51-Restart-00011",
    })
    try:
        with request.urlopen(req, timeout=35) as resp:
            raw = resp.read(65536)
            if resp.status != 200:
                raise RestartRejected("Supervisor nevratil HTTP 200.")
    except error.HTTPError as exc:
        raise RestartRejected("Supervisor vratil HTTP %d." % exc.code) from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise RestartRejected("Supervisor spojeni nejiste; restart neopakovat.") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RestartRejected("Supervisor nevratil platne JSON.") from exc
    if not isinstance(result, dict) or result.get("result") != "ok":
        raise RestartRejected("Supervisor operaci nepotvrdil.")
    return result


def _write_once_marker(*, path: Path, command_id: str, device_uuid: str, now: datetime) -> None:
    """Atomicky bez moznosti opakovat pri chybe nebo preruseni procesu."""
    data = json.dumps({
        "serial": SERIAL, "command_id": command_id, "device_uuid": device_uuid,
        "requested_at_utc": now.isoformat(), "state": "restart_request_attempted"
    }, sort_keys=True) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def execute_ha_core_restart_00011(
    *,
    identity: dict[str, Any],
    command_id: str,
    command_payload: dict[str, Any],
    submit_result: Callable[..., Any],
) -> None:
    """Jen striktnim prikazem 00011; standardni cloudovy worker predava vysledky."""
    try:
        from host.identity import DeviceIdentityService
        serial = DeviceIdentityService().get_serial_number()
        checked = validate_restart_request(
            cloud_identity=identity, permanent_serial=serial,
            command_id=command_id, payload=command_payload,
        )
        if MARKER_PATH.exists():
            raise RestartRejected("Jednorazovy restart uz byl vyvolan nebo rezervovan.")
        token = os.getenv("SUPERVISOR_TOKEN", "").strip()
        if not token:
            raise RestartRejected("Chybi opravneni SUPERVISOR_TOKEN.")
        _supervisor_request(method="GET", url=INFO_URL, token=token)
        now = datetime.now(timezone.utc)
        _write_once_marker(
            path=MARKER_PATH,
            command_id=checked["command_id"],
            device_uuid=checked["device_uuid"], now=now,
        )
        _supervisor_request(method="POST", url=API_URL, token=token,
                            payload={"safe_mode": False, "force": False})
    except Exception as exc:
        submit_result(
            identity=identity, command_id=command_id, status="failed",
            result={"worker": "command_worker", "executor": COMMAND_TYPE,
                    "phase": "rejected_or_uncertain", "restart_may_have_started": MARKER_PATH.exists()},
            error_message=str(exc)[:300],
        )
        return
    submit_result(
        identity=identity, command_id=command_id, status="succeeded",
        result={"worker": "command_worker", "executor": COMMAND_TYPE,
                "phase": "supervisor_request_accepted", "core_restart_verified": False,
                "target_serial": SERIAL},
        error_message=None,
    )
