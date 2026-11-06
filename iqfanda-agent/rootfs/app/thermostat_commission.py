"""F55 R6 controlled local-thermostat commission/handback.

No relay ownership can be acquired by merely changing add-on options. Commission
requires a fresh local preflight, writes a persistent fence, updates self options,
reports the command result, and only then requests a self restart. On the next
boot thermostat_runtime claims the verified outputs before legacy workers start.

Handback is symmetric: keep ownership, force/verify OFF, save a handback marker,
disable the local mode, report success, then restart. The next boot releases the
claim only after OFF feedback is re-verified.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
import uuid
from urllib import error, request

VERSION = "0.1.172"
COMMISSION_COMMAND = "local_thermostat_commission_v1"
HANDBACK_COMMAND = "local_thermostat_handback_v1"
ROOT = Path("/config/local-thermostat")
COMMISSION_PATH = ROOT / "commission.json"
HANDBACK_PATH = ROOT / "handback.json"
SUPERVISOR_BASE = os.getenv("IQF_SUPERVISOR_API_BASE_URL", "http://supervisor").rstrip("/")


class CommissionError(RuntimeError):
    pass


def _uuid(value, name):
    if not isinstance(value, str):
        raise CommissionError(name + "_INVALID")
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, TypeError) as exc:
        raise CommissionError(name + "_INVALID") from exc
    if normalized != value:
        raise CommissionError(name + "_INVALID")
    return value


def _identity_uuid(identity):
    return _uuid(str(identity.get("device_uuid") or ""), "DEVICE_UUID")


def _token():
    value = str(os.getenv("SUPERVISOR_TOKEN") or "").strip()
    if not value:
        raise CommissionError("SUPERVISOR_TOKEN_MISSING")
    return value


def _supervisor(method, path, payload=None, timeout=30):
    data = None
    headers = {
        "Authorization": "Bearer " + _token(),
        "Accept": "application/json",
        "User-Agent": "TNG-IQ-FANDA-F55-R6",
    }
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(SUPERVISOR_BASE + path, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise CommissionError("SUPERVISOR_HTTP_" + str(exc.code) + ":" + body) from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise CommissionError("SUPERVISOR_TRANSPORT:" + type(exc).__name__) from exc
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommissionError("SUPERVISOR_INVALID_JSON") from exc
    if isinstance(value, dict) and value.get("result") not in (None, "ok"):
        raise CommissionError("SUPERVISOR_REJECTED:" + str(value.get("message") or value.get("result")))
    return value


def _current_options():
    value = _supervisor("GET", "/addons/self/info")
    data = value.get("data") if isinstance(value, dict) else None
    options = data.get("options") if isinstance(data, dict) else None
    if not isinstance(options, dict):
        raise CommissionError("ADDON_OPTIONS_UNAVAILABLE")
    return dict(options)


def _set_options(options):
    if not isinstance(options, dict):
        raise CommissionError("ADDON_OPTIONS_INVALID")
    _supervisor("POST", "/addons/self/options", {"options": options})


def _restart_after_report(delay_seconds=2.0):
    def worker():
        time.sleep(delay_seconds)
        try:
            _supervisor("POST", "/addons/self/restart", timeout=20)
        except Exception:
            # The already persisted marker/options keep the transition fail-closed.
            # A later ordinary restart will resume the same transition.
            return
    thread = threading.Thread(target=worker, name="f55-r6-self-restart", daemon=True)
    thread.start()


def _write_marker(path, value):
    import thermostat_runtime as runtime
    runtime.atomic_json(path, value)


def _read_marker(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def fenced_outputs():
    refs = set()
    for path in (COMMISSION_PATH, HANDBACK_PATH):
        marker = _read_marker(path)
        if not marker:
            continue
        for ref in marker.get("outputs") or []:
            if isinstance(ref, str) and ref.startswith("switch."):
                refs.add(ref)
    try:
        import thermostat_runtime as runtime
        refs.update(str(ref) for ref in runtime.OWNERS if str(ref).startswith("switch."))
    except Exception:
        pass
    return refs


def command_is_fenced(command_type, payload):
    """Reject legacy/cloud writes to an output during/after local takeover."""
    if command_type in (COMMISSION_COMMAND, HANDBACK_COMMAND, "local_thermostat_preflight_v1"):
        return False
    refs = fenced_outputs()
    if not refs or not isinstance(payload, dict):
        return False
    candidates = {
        str(payload.get("output_reference") or "").strip(),
        str(payload.get("entity_id") or "").strip(),
    }
    raw = payload.get("entity_ids")
    if isinstance(raw, list):
        candidates.update(str(x).strip() for x in raw)
    return bool(refs.intersection(x for x in candidates if x))


def _preflight_payload(payload):
    allowed = (
        "device_uuid",
        "request_id",
        "target_ids",
        "expected_config_version",
        "expected_sensor_devices",
    )
    return {key: payload.get(key) for key in allowed}


def execute_commission(*, identity, command_id, command_payload, submit_result):
    import thermostat_preflight as preflight
    import thermostat_runtime as runtime

    try:
        if not isinstance(command_payload, dict) or command_payload.get("activate") is not True:
            raise CommissionError("EXPLICIT_ACTIVATE_TRUE_REQUIRED")
        uid = _identity_uuid(identity)
        if _uuid(command_payload.get("device_uuid"), "DEVICE_UUID") != uid:
            raise CommissionError("FOREIGN_DEVICE")
        report = preflight.inspect(identity=identity, payload=_preflight_payload(command_payload))
        if report.get("checks_passed") is not True:
            raise CommissionError("PREFLIGHT_NOT_PASSED")
        rows = report.get("targets") or []
        if not rows or any(row.get("all_outputs_currently_off") is not True for row in rows):
            raise CommissionError("OUTPUTS_MUST_BE_OFF_BEFORE_TAKEOVER")
        targets = [str(x) for x in command_payload.get("target_ids") or []]
        outputs = sorted({
            str(out.get("reference"))
            for row in rows for out in (row.get("outputs") or [])
            if isinstance(out, dict) and str(out.get("reference") or "").startswith("switch.")
        })
        if not outputs:
            raise CommissionError("NO_VERIFIED_OUTPUTS")
        marker = {
            "schema_version": 1,
            "phase": "commission_pending_restart",
            "version": VERSION,
            "device_uuid": uid,
            "target_ids": targets,
            "config_version": report.get("config_version"),
            "request_id": report.get("request_id"),
            "outputs": outputs,
            "contract_sha256": {
                str(row.get("target_id")): str(row.get("contract_sha256"))
                for row in rows
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_marker(COMMISSION_PATH, marker)
        options = _current_options()
        options["local_thermostat_mode"] = "active"
        options["local_thermostat_targets"] = targets
        _set_options(options)
        runtime.STATUS.update(
            mode="commission_pending_restart",
            targets=targets,
            physical_control_active=False,
            cloud_fence=True,
            owner_outputs=outputs,
        )
        submit_result(
            identity=identity,
            command_id=command_id,
            status="succeeded",
            result={
                "worker": "command_worker",
                "executor": COMMISSION_COMMAND,
                "phase": "options_saved_restart_requested",
                "device_uuid": uid,
                "target_ids": targets,
                "outputs": outputs,
                "config_version": report.get("config_version"),
                "physical_control_active": False,
                "cloud_fence": True,
            },
            error_message=None,
        )
        _restart_after_report()
    except Exception as exc:
        submit_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": COMMISSION_COMMAND,
                "phase": "commission_rejected_fail_closed",
                "physical_control_active": False,
                "error_type": type(exc).__name__,
                "reason": str(exc)[:160],
            },
            error_message="F55 R6 commission rejected; no takeover performed.",
        )


def execute_handback(*, identity, command_id, command_payload, submit_result):
    import thermostat_runtime as runtime
    import zigbee_manager as zm

    try:
        if not isinstance(command_payload, dict) or command_payload.get("confirm_handback") is not True:
            raise CommissionError("EXPLICIT_HANDBACK_CONFIRMATION_REQUIRED")
        uid = _identity_uuid(identity)
        if _uuid(command_payload.get("device_uuid"), "DEVICE_UUID") != uid:
            raise CommissionError("FOREIGN_DEVICE")
        mode, selected, _ = runtime.options()
        expected = [str(x) for x in command_payload.get("target_ids") or []]
        if mode != "active" or not expected or sorted(selected) != sorted(expected):
            raise CommissionError("ACTIVE_TARGET_SET_MISMATCH")
        with runtime.LOCK:
            runtime.off_reserved("explicit_handback_requested")
            outputs = sorted(runtime.OWNERS)
            if not outputs:
                raise CommissionError("NO_LOCAL_OWNERSHIP")
            states = {ref: (zm.get_entity_state(ref) or {}).get("state") for ref in outputs}
            if any(value != "off" for value in states.values()):
                raise CommissionError("OFF_FEEDBACK_REQUIRED")
            _write_marker(HANDBACK_PATH, {
                "schema_version": 1,
                "phase": "handback_pending_restart",
                "version": VERSION,
                "device_uuid": uid,
                "target_ids": expected,
                "outputs": outputs,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            options = _current_options()
            options["local_thermostat_mode"] = "disabled"
            options["local_thermostat_targets"] = []
            _set_options(options)
            runtime.STATUS.update(
                mode="handback_pending_restart",
                physical_control_active=False,
                cloud_fence=True,
                owner_outputs=outputs,
            )
        submit_result(
            identity=identity,
            command_id=command_id,
            status="succeeded",
            result={
                "worker": "command_worker",
                "executor": HANDBACK_COMMAND,
                "phase": "off_verified_options_disabled_restart_requested",
                "device_uuid": uid,
                "outputs": outputs,
                "physical_control_active": False,
                "cloud_fence": True,
            },
            error_message=None,
        )
        _restart_after_report()
    except Exception as exc:
        submit_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": HANDBACK_COMMAND,
                "phase": "handback_rejected_ownership_retained",
                "physical_control_active": False,
                "error_type": type(exc).__name__,
                "reason": str(exc)[:160],
            },
            error_message="F55 R6 handback rejected; local ownership remains fail-closed.",
        )
