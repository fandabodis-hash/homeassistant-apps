"""Persistent local owner for manual and leased automatic battery requests.

The cloud transports a request; this module owns its local renewal, SOC safety
and verified STOP. No network request is made by the control loop. A legacy
command authorizes only its existing valid_until lease. A longer explicit
manual_session_expires_at is never inferred from a heartbeat or a cached SOC.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

from communication.battery_control_safety import BatteryControlSafetyStop
from communication.inverter_control_adapter import execute_battery_control_from_cloud_config
from communication.inverter_control_adapter import build_inverter_control_capability_state
from device_config import load_cached_cloud_config
from spot_battery_intent import save_spot_battery_intent

STATE_PATH = Path(os.getenv("IQF_MANUAL_BATTERY_RUNTIME_PATH", "/config/manual_battery_runtime.json"))
POLL_SECONDS = 10.0
MAX_SESSION_SECONDS = 43200
ACTIVE_STATES = {"preparing", "active", "stop_pending"}
_LOCK = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _date(value: str) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Battery deadline requires a timezone.")
    return result.astimezone(timezone.utc)


def _sync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _manual(intent: dict) -> bool:
    return intent.get("manual_immediate_charge") is True


def _signature(intent: dict) -> str:
    keys = ("action", "manual_immediate_charge", "manual_session_id",
            "requested_charge_power_w", "requested_discharge_power_w",
            "target_soc_percent", "minimum_discharge_soc_percent")
    value = json.dumps({key: intent.get(key) for key in keys}, sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


def _control_config(config: dict) -> dict:
    runtimes = [item for item in config.get("module_runtime_configurations", [])
                if isinstance(item, dict) and item.get("module_key") == "photovoltaic"]
    if len(runtimes) != 1:
        raise ValueError("Battery control requires one photovoltaic runtime.")
    # Only the local device routing is persisted, never cloud credentials.
    return {"module_runtime_configurations": json.loads(json.dumps(runtimes))}


def _route(config: dict) -> str:
    runtime = _control_config(config)["module_runtime_configurations"][0]
    keys = ("manufacturer", "model", "profile_id", "communication_type",
            "communicator_id", "modbus_device_id", "modbus_device_ids",
            "matched_device_id",
            "baudrate", "parity", "data_bits", "stop_bits")
    return json.dumps({key: runtime.get(key) for key in keys}, sort_keys=True)


def _canary_evidence_complete(result: dict) -> bool:
    if not (result.get("status") == "passed" and result.get("verified") is True
            and result.get("profile_id") == "foxess_h3_10_0_e"
            and result.get("verification_scope") == "bidirectional_500w_and_stop"
            and result.get("error") is None):
        return False
    phases = result.get("phases")
    if not isinstance(phases, list) or [phase.get("action") for phase in phases] != ["discharge_grid", "charge_grid"]:
        return False
    for phase, sign in zip(phases, (1, -1)):
        samples = phase.get("samples", [])
        if phase.get("status") != "passed" or phase.get("requested_setpoint_w") != 500 * sign or len(samples) < 3:
            return False
        for sample in samples[-3:]:
            registers = sample.get("registers", {})
            power = registers.get("battery_power_w")
            if not (sample.get("readback_verified") is True and sample.get("physical_power_within_tolerance") is True
                    and type(power) is int and 250 <= sign * power <= 750
                    and registers.get("remote_active_power_w") == 500 * sign
                    and registers.get("remote_enable") == 1 and registers.get("work_mode") == 2):
                return False
    for auto in (result.get("preflight", {}).get("auto", {}),
                 result.get("between_phases_auto", {}), result.get("final_auto", {})):
        registers = auto.get("registers", {})
        if not (auto.get("verified") is True and registers.get("remote_enable") == 0
                and registers.get("remote_active_power_w") == 0):
            return False
    return True


class LocalBatteryRuntime:
    def __init__(self, *, path: Path = STATE_PATH, execute=None, config_loader=None, clock=None, feature_guard=None):
        self.path = path
        self.execute = execute or execute_battery_control_from_cloud_config
        self.config_loader = config_loader or load_cached_cloud_config
        self.clock = clock or _now
        self.feature_guard = feature_guard
        self.recovered = False
        self.emergency_state = None

    def _load(self) -> dict:
        if self.emergency_state is not None:
            return json.loads(json.dumps(self.emergency_state))
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise RuntimeError("Local battery owner state is invalid; no start is permitted.")
        return raw

    def _save(self, state: dict) -> None:
        state["schema_version"] = 1
        state["updated_at"] = self.clock().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".battery-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            _sync_directory(self.path.parent)
            self.emergency_state = None
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def status(self) -> dict:
        with _LOCK:
            try:
                state = self._load()
            except (OSError, ValueError, RuntimeError) as exc:
                return {"schema_version": 1, "state": "storage_fault", "error": str(exc)}
            keys = ("state", "action", "manual", "command_id", "session_id", "started_at",
                    "updated_at", "expires_at", "reason", "error", "stop_error",
                    "stop_attempts", "next_stop_retry_at", "applied_power_w", "verified",
                    "observed_soc_percent", "last_verified_at", "profile_id", "feature_fingerprint")
            return {"schema_version": 1, "executor": "local_battery_runtime",
                    **{key: state[key] for key in keys if key in state}}

    def _apply(self, state: dict, *, auto: bool = False):
        intent = state["intent"]
        action = "auto" if auto else intent["action"]
        result = self.execute(
            cloud_config=state["control_config"], action=action,
            allowed_charge_power_w=0 if auto else intent["allowed_charge_power_w"],
            allowed_discharge_power_w=0 if auto else intent["allowed_discharge_power_w"],
            target_soc_percent=intent["target_soc_percent"],
            minimum_discharge_soc_percent=intent["minimum_discharge_soc_percent"],
        )
        if result.verified is not True or result.action != action:
            raise RuntimeError("Battery driver did not verify the requested state.")
        if auto and result.applied_power_w != 0:
            raise RuntimeError("Battery driver did not verify zero remote setpoint.")
        return result

    def _verify_feature(self, state: dict) -> str:
        if self.feature_guard is not None:
            return self.feature_guard()
        from communication.battery_feature_guard import verify_battery_feature
        return verify_battery_feature(profile_id=state.get("profile_id"))

    def _stop(self, state: dict, reason: str, *, clear_latch: bool = False) -> dict:
        # F58: a background AUTO must never re-latch a previously cleared
        # manual request when the hardware was already safely stopped.
        # The replay latch is required only when an ACTIVE manual request is
        # genuinely terminated by safety/expiry/restart. Preserve any
        # pre-existing latch and always perform the physical AUTO readback.
        ended_active_manual = (
            state.get("manual") is True
            and state.get("state") in {"preparing", "active", "stop_pending"}
        )
        state.update(state="stop_pending", reason=reason, verified=False)
        try:
            self._save(state)
        except OSError as exc:
            # Disk failure must not prevent a physical STOP of a known owner.
            self.emergency_state = json.loads(json.dumps(state))
        try:
            result = self._apply(state, auto=True)
        except Exception as exc:
            attempts = int(state.get("stop_attempts", 0)) + 1
            state.update(stop_attempts=attempts, stop_error=str(exc),
                         next_stop_retry_at=(self.clock() + timedelta(seconds=min(300, 10 * 2 ** min(attempts, 5)))).isoformat())
            try:
                self._save(state)
            except OSError:
                self.emergency_state = json.loads(json.dumps(state))
            raise RuntimeError(f"Battery STOP is unverified: {exc}") from exc
        state.update(state="stopped", action="auto", applied_power_w=0, verified=True,
                     stop_error=None, stop_attempts=0, next_stop_retry_at=None,
                     last_verified_at=self.clock().isoformat())
        if clear_latch:
            state["blocked_signature"] = None
            state["blocked_manual_signatures"] = []
        elif ended_active_manual:
            state["blocked_signature"] = state.get("signature")
            blocked = list(state.get("blocked_manual_signatures") or [])
            if state.get("signature") and state["signature"] not in blocked:
                blocked.append(state["signature"])
            state["blocked_manual_signatures"] = blocked[-64:]
        try:
            self._save(state)
        except OSError as exc:
            self.emergency_state = json.loads(json.dumps(state))
            raise RuntimeError(f"Battery is in AUTO but its durable STOP state could not be saved: {exc}") from exc
        return {"phase": "local_battery_stopped", "applied": asdict(result), "local_runtime": self.status()}

    def _recover(self) -> None:
        if self.recovered:
            return
        state = self._load()
        if state.get("state") in ACTIVE_STATES:
            # Reboot never silently re-arms a previous charging request. Restore
            # the old device route before accepting a command for a changed route.
            state["reason"] = "process_restart"
            state["state"] = "stop_pending"
            state["next_stop_retry_at"] = None
            try:
                self._stop(state, "process_restart")
            finally:
                # _stop retains an in-memory STOP owner if disk persistence fails.
                self.recovered = True
        else:
            self.recovered = True

    def _activate(self, state: dict) -> dict:
        try:
            fingerprint = self._verify_feature(state)
            if state.get("feature_fingerprint") not in (None, fingerprint):
                raise RuntimeError("Battery implementation changed during an active session.")
            state["feature_fingerprint"] = fingerprint
            result = self._apply(state)
            state.update(state="active", action=result.action, applied_power_w=result.applied_power_w,
                         verified=True, last_verified_at=self.clock().isoformat(), error=None)
            self._save(state)
        except BatteryControlSafetyStop as exc:
            state["observed_soc_percent"] = exc.observed_soc_percent
            outcome = self._stop(state, exc.reason)
            outcome["phase"] = "local_battery_safety_stop"
            return outcome
        except Exception as exc:
            state["error"] = str(exc)
            try:
                self._stop(state, "control_apply_failed")
            except Exception:
                logging.exception("Local battery cleanup after failed control remains pending")
            raise
        return {"phase": "local_battery_active", "applied": asdict(result), "local_runtime": self.status()}

    def submit(self, *, payload: dict, command_id: str, cloud_config: dict) -> dict:
        with _LOCK:
            now = self.clock()
            intent = save_spot_battery_intent(payload, now=now, persist=False)
            control_config = _control_config(cloud_config)
            self._recover()
            state = self._load()
            if state.get("state") == "stop_pending":
                retry_at = state.get("next_stop_retry_at")
                if retry_at and _date(retry_at) > now and not (_manual(intent) and intent["action"] == "auto"):
                    raise RuntimeError("Battery STOP is pending; automatic retries are in backoff.")
                self._stop(state, state.get("reason", "pending_stop"))
                state = self._load()
            active = state.get("state") in {"preparing", "active"}
            if active and _date(state["expires_at"]) <= now:
                self._stop(state, "authorization_expired")
                state = self._load()
                active = False
            if active and state.get("manual") and not _manual(intent):
                return {"phase": "local_manual_owner_preserved", "applied": None, "local_runtime": self.status()}
            if intent["action"] == "auto":
                if not state or not active:
                    state.update(intent=intent, control_config=control_config)
                state["command_id"] = command_id
                if _manual(intent):
                    state["manual_stop_barrier"] = intent["valid_until"]
                return self._stop(state, intent.get("reason") or "explicit_auto", clear_latch=_manual(intent))
            signature = _signature(intent)
            if _manual(intent) and state.get("manual_stop_barrier") and _date(intent["valid_until"]) <= _date(state["manual_stop_barrier"]):
                raise RuntimeError("Manual command was issued before the last explicit STOP.")
            if state.get("blocked_signature") == signature or signature in state.get("blocked_manual_signatures", []):
                raise RuntimeError("This battery request already ended locally; explicit STOP/new session is required.")
            capability = build_inverter_control_capability_state(control_config).get("capabilities", {}).get("battery_charge_control", {})
            if intent["action"] not in capability.get("supported_actions", []):
                raise RuntimeError("Battery action is unsupported for this inverter profile: " + str(capability.get("unsupported_reason") or intent["action"]))
            same = active and state.get("signature") == signature and _route(state["control_config"]) == _route(control_config)
            if active and not same:
                self._stop(state, "request_replaced")
                state = self._load()
            started = _date(state["started_at"]) if same else now
            deadline = min(_date(intent.get("manual_session_expires_at") or intent["valid_until"]),
                           started + timedelta(seconds=MAX_SESSION_SECONDS))
            if deadline <= now:
                raise ValueError("Local battery session has expired.")
            from communication.inverter_adapter import select_inverter_profile
            runtime = control_config["module_runtime_configurations"][0]
            profile = select_inverter_profile(manufacturer=runtime["manufacturer"], model=runtime["model"])
            state = {**state, "state": "preparing", "action": intent["action"],
                     "manual": _manual(intent), "intent": intent, "control_config": control_config,
                     "signature": signature, "blocked_signature": None, "command_id": command_id,
                     "session_id": intent.get("manual_session_id") or (state.get("session_id") if same else command_id),
                     "started_at": started.isoformat(), "expires_at": deadline.isoformat(),
                     "reason": intent.get("reason"), "verified": False, "error": None,
                     "profile_id": profile["profile_id"],
                     "feature_fingerprint": state.get("feature_fingerprint") if same else None}
            # Reject a corrupted/newly unapproved implementation before the first
            # hardware change. During an existing session, _activate will STOP it.
            if not same:
                state["feature_fingerprint"] = self._verify_feature(state)
            self._save(state)
            return self._activate(state)

    def tick(self) -> dict:
        with _LOCK:
            self._recover()
            state = self._load()
            if state.get("state") not in ACTIVE_STATES:
                return self.status()
            now = self.clock()
            if state.get("state") == "stop_pending":
                retry = state.get("next_stop_retry_at")
                if not retry or _date(retry) <= now:
                    self._stop(state, state.get("reason", "pending_stop"))
                return self.status()
            if now < _date(state["updated_at"]) - timedelta(seconds=5):
                self._stop(state, "clock_moved_backwards")
            elif now >= _date(state["expires_at"]):
                self._stop(state, "authorization_expired")
            else:
                try:
                    current_config = self.config_loader()
                    same_route = isinstance(current_config, dict) and _route(current_config) == _route(state["control_config"])
                except Exception:
                    same_route = False
                if not same_route:
                    self._stop(state, "configuration_changed_or_unavailable")
                else:
                    self._activate(state)
            return self.status()

    def canary(self, *, callback, cloud_config: dict, command_id: str,
               expected_runtime_fingerprint: str) -> dict:
        """Reserve an idle local owner, retain recovery data, and test only FoxESS.

        A PASS pins this exact implementation for this profile and the recorded
        500 W charge/discharge/STOP scope. It does not certify untested limits.
        """
        from communication.inverter_adapter import select_inverter_profile
        from communication.battery_feature_guard import verify_battery_feature
        with _LOCK:
            self._recover()
            previous = self._load()
            if previous.get("state") in ACTIVE_STATES:
                raise RuntimeError("A battery request or unverified STOP already owns the inverter.")
            config = _control_config(cloud_config)
            runtime = config["module_runtime_configurations"][0]
            profile = select_inverter_profile(manufacturer=runtime["manufacturer"], model=runtime["model"])
            if profile["profile_id"] != "foxess_h3_10_0_e":
                raise ValueError("This commissioning test is restricted to FoxESS H3 protocol 115.")
            fingerprint = self.feature_guard() if self.feature_guard else verify_battery_feature()
            if expected_runtime_fingerprint != fingerprint:
                raise RuntimeError("Canary implementation does not match the explicitly selected runtime fingerprint.")
            now = self.clock()
            state = {**previous, "state": "preparing", "action": "auto", "manual": True,
                "command_id": command_id, "session_id": command_id,
                "started_at": now.isoformat(), "expires_at": (now + timedelta(minutes=5)).isoformat(),
                "profile_id": profile["profile_id"], "feature_fingerprint": fingerprint,
                "control_config": config, "signature": "canary:" + command_id,
                "intent": {"action": "auto", "allowed_charge_power_w": 0,
                    "allowed_discharge_power_w": 0, "target_soc_percent": 95,
                    "minimum_discharge_soc_percent": 20},
                "reason": "physical_500w_canary", "verified": False}
            self._save(state)
            result = {"status": "failed", "profile_id": profile["profile_id"]}
            try:
                result = callback(cloud_config=config, command_id=command_id)
                if not isinstance(result, dict):
                    raise RuntimeError("Canary returned an invalid result.")
            except Exception as exc:
                result = {"status": "failed", "profile_id": profile["profile_id"], "error": str(exc)}
            finally:
                try:
                    cleanup = self._stop(state, "physical_canary_finished")
                    result["local_final_auto_verified"] = cleanup["applied"]["verified"] is True
                except Exception as exc:
                    result.update(status="failed", verified=False, local_final_auto_verified=False, final_auto_error=str(exc))
            result.update(runtime_fingerprint=fingerprint, command_id=command_id,
                          finished_at=self.clock().isoformat())
            if result.get("status") == "passed" and not _canary_evidence_complete(result):
                result.update(status="failed", verified=False, error="Physical canary evidence is incomplete.")
            if result.get("status") == "passed" and result.get("local_final_auto_verified") is True:
                after = self.feature_guard() if self.feature_guard else verify_battery_feature()
                if after != fingerprint:
                    result.update(status="failed", verified=False, error="Implementation changed during physical test.")
                else:
                    result["protection"] = self._pin_canary(result, fingerprint)
            if result.get("status") != "passed":
                result["verified"] = False
            return result

    def _pin_canary(self, result: dict, fingerprint: str) -> dict:
        evidence_bytes = json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
        report_hash = hashlib.sha256(evidence_bytes).hexdigest()
        evidence_dir = self.path.parent / "manual_battery_acceptance"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_file = evidence_dir / (report_hash + ".json")
        with evidence_file.open("wb") as stream:
            stream.write(evidence_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(evidence_dir)
        pin_path = self.path.parent / "manual_battery_verified_fingerprint.json"
        pin = json.loads(pin_path.read_text()) if pin_path.exists() else {"schema_version": 1, "profiles": {}}
        if pin.get("schema_version") != 1 or not isinstance(pin.get("profiles"), dict):
            raise RuntimeError("Existing physical verification registry is invalid; it was not replaced.")
        pin["profiles"][result["profile_id"]] = {
            "status": "physically_verified", "runtime_fingerprint": fingerprint,
            "agent_version": (Path(__file__).parent / "VERSION").read_text().strip(),
            "evidence_id": result["command_id"], "report_sha256": report_hash,
            "verification_scope": result["verification_scope"], "verified_at": result["finished_at"],
        }
        fd, temporary = tempfile.mkstemp(prefix=".battery-pin-", dir=pin_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(pin, stream, sort_keys=True, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, pin_path)
            _sync_directory(pin_path.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"status": "locked", "profile_id": result["profile_id"],
                "runtime_fingerprint": fingerprint, "report_sha256": report_hash,
                "verification_scope": result["verification_scope"]}


_RUNTIME = LocalBatteryRuntime()


def submit_local_battery_intent(*, payload: dict, command_id: str, cloud_config: dict) -> dict:
    return _RUNTIME.submit(payload=payload, command_id=command_id, cloud_config=cloud_config)


def get_local_battery_status() -> dict:
    return _RUNTIME.status()


def run_exclusive_battery_canary(*, callback, cloud_config: dict, command_id: str,
                                 expected_runtime_fingerprint: str) -> dict:
    return _RUNTIME.canary(callback=callback, cloud_config=cloud_config, command_id=command_id,
                          expected_runtime_fingerprint=expected_runtime_fingerprint)


def main() -> None:
    while True:
        try:
            _RUNTIME.tick()
        except Exception:
            logging.exception("Local battery supervisor: control stopped or STOP pending")
        time.sleep(POLL_SECONDS)
