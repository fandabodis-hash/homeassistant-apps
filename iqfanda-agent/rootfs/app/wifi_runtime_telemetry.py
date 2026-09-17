"""F30.4C R6: read-only runtime telemetry from the existing Wi-Fi registry.

No discovery, onboarding, registry writes or actuator commands. The existing
0.1.123 entity builders define identifiers/units; this module supplies fresh
status responses instead of discovery snapshots. Physical measurement time is
NOT fabricated: agent_received_at means the time the RPC response was received.
"""
from __future__ import annotations

import copy
import http.client
import ipaddress
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any

VERSION = "F30.4C-R6-WIFI-RUNTIME"
POLL_START_INTERVAL_SECONDS = 10.0
CYCLE_BUDGET_SECONDS = 4.0
RPC_TIMEOUT_SECONDS = 1.0
MAX_WORKERS = 8
MAX_RESPONSE_BYTES = 262144
RPC_METHODS = frozenset({"Shelly.GetDeviceInfo", "EM.GetStatus", "EMData.GetStatus", "Switch.GetStatus"})
_ENTITY_FIELDS = ("entity_id", "entity_key", "domain", "device_class", "name", "unit",
                  "source_component", "statistics_enabled", "statistics_type", "measurement_profile")
_lock = threading.Lock()
_rotation = 0
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="iqf-wifi-read")
_pending = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReadError(Exception):
    """Only a controlled code is propagated; never response bodies/credentials."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def mac_id(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9A-Fa-f:-]+", text):
        raise ReadError("invalid_hardware_identity")
    result = re.sub(r"[:-]", "", text).upper()
    if len(result) != 12:
        raise ReadError("invalid_hardware_identity")
    return result


def local_host(value: Any) -> str:
    try:
        address = ipaddress.ip_address(str(value).strip())
    except ValueError:
        raise ReadError("invalid_registry_address") from None
    networks = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"))
    if address.version != 4 or not any(address in n for n in networks):
        raise ReadError("non_lan_address_rejected")
    return str(address)


def read_rpc(host: str, method: str, params: dict, deadline: float) -> dict:
    if method not in RPC_METHODS:
        raise ReadError("write_or_unknown_method_rejected")
    host = local_host(host)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ReadError("cycle_budget_exceeded")
    connection = http.client.HTTPConnection(host, 80, timeout=min(RPC_TIMEOUT_SECONDS, remaining))
    try:
        # http.client does not follow redirects or environment proxy settings.
        body = json.dumps({"id": 1, "method": method, "params": params}, allow_nan=False).encode("utf-8")
        connection.request("POST", "/rpc", body=body, headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "TNG-IQ-FANDA-WiFi-Runtime", "Connection": "close"})
        response = connection.getresponse()
        if response.status in (401, 403):
            raise ReadError("device_authentication_required")
        if response.status != 200:
            raise ReadError("device_http_error")
        data = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReadError("cycle_budget_exceeded")
            if connection.sock is not None:
                connection.sock.settimeout(min(RPC_TIMEOUT_SECONDS, remaining))
            block = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            if len(data) > MAX_RESPONSE_BYTES:
                raise ReadError("response_too_large")
        payload = json.loads(data.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(payload, dict) or payload.get("id") != 1:
            raise ReadError("invalid_rpc_response")
        if payload.get("error"):
            raise ReadError("device_rpc_error")
        if not isinstance(payload.get("result"), dict):
            raise ReadError("invalid_rpc_result")
        return payload["result"]
    except ReadError:
        raise
    except (TimeoutError, OSError, http.client.HTTPException):
        raise ReadError("device_connection_failed") from None
    except (ValueError, UnicodeError, TypeError):
        raise ReadError("invalid_rpc_response") from None
    finally:
        connection.close()


def native_module():
    import wifi_native_onboarding
    return wifi_native_onboarding


def read_registry(native=None) -> dict:
    native = native or native_module()
    path = native._phase28_r177g1_registry_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError:
        raise ReadError("registry_read_failed") from None
    if len(raw) > 8 * 1024 * 1024:
        raise ReadError("registry_too_large")
    try:
        registry = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise ReadError("invalid_registry") from None
    if not isinstance(registry, dict):
        raise ReadError("invalid_registry")
    return registry


def registered_entries(registry: dict) -> list[tuple[str, dict]]:
    result = []
    for key, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        # Explicit registration only: no discovered-but-unregistered addresses.
        # A name alone is not permission to begin polling an arbitrary device.
        if entry.get("registered") is True and entry.get("telemetry_enabled") is not False:
            result.append((str(key), entry))
    return sorted(result, key=lambda item: item[0])


def fast_cycle_required(registry: dict | None = None) -> bool:
    try:
        return bool(registered_entries(read_registry() if registry is None else registry))
    except Exception:
        # Retry a broken registry without busy looping. No data are fabricated.
        return True


def cycle_sleep(configured_interval: Any, started: float, *, fast: bool,
                now: float | None = None) -> float:
    try:
        interval = float(configured_interval)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        interval = 60.0
    if not fast:
        return interval  # Keep the original non-Wi-Fi heartbeat cadence.
    # The next cycle is relative to the previous start, not completion + delay.
    interval = min(interval, POLL_START_INTERVAL_SECONDS)
    elapsed = (time.monotonic() if now is None else now) - started
    return max(1.0, interval - max(0.0, elapsed))


def _base_device(key: str, entry: dict) -> dict:
    try:
        identity = mac_id(entry.get("mac_address") or key.rsplit(":", 1)[-1])
    except ReadError:
        identity = ""
    return {"device_id": identity, "mac_address": identity, "registry_id": key,
            "transport": "wifi", "source": "wifi_runtime_poll",
            "name": str(entry.get("friendly_name") or entry.get("discovered_name") or key),
            "manufacturer": str(entry.get("manufacturer") or ""),
            "model": str(entry.get("model") or ""), "registered": True,
            "available": False, "entities": []}


def _metadata(entity: dict, registry_id: str) -> dict | None:
    entity_id = str(entity.get("entity_id") or "")
    if not entity_id.startswith(registry_id + ":") or any(c.isspace() for c in entity_id):
        return None
    safe = {field: entity[field] for field in _ENTITY_FIELDS if field in entity}
    safe["entity_id"] = entity_id
    if entity.get("domain") == "switch":
        safe["capability"] = "switch"
        safe["capabilities"] = ["switch", "binary"]
    else:
        safe["capability"] = str(entity.get("device_class") or "state")
    safe.update(transport="wifi", source="wifi_runtime_poll", data_kind="runtime")
    return safe


def _missing_entities(key: str, entry: dict, reason: str) -> list[dict]:
    result = []
    for raw in entry.get("entities") or []:
        if not isinstance(raw, dict):
            continue
        item = _metadata(raw, key)
        if item is not None:
            item.update(state="unavailable", value=None, available=False, has_value=False,
                        error_code=reason)
            result.append(item)
    return result


def failed_device(key: str, entry: dict, reason: str) -> dict:
    result = _base_device(key, entry)
    result.update(error_code=reason, entities=_missing_entities(key, entry, reason),
                  attempted_at=utc_now())
    return result


def poll_one(key: str, entry: dict, native, rpc, deadline: float) -> dict:
    result = _base_device(key, entry)
    try:
        if time.monotonic() >= deadline:
            raise ReadError("cycle_budget_exceeded")
        identity = mac_id(entry.get("mac_address") or key.rsplit(":", 1)[-1])
        if mac_id(key.rsplit(":", 1)[-1]) != identity or not key.startswith("wifi:"):
            raise ReadError("registry_identity_mismatch")
        host = local_host(entry.get("ip_address"))
        profile = str(entry.get("profile") or "").lower()
        if str(entry.get("manufacturer") or "").lower() != "shelly" or profile not in (
                "shelly_gen2_rpc", "shelly_pro_3em"):
            raise ReadError("runtime_adapter_not_supported")
        # Validate the hardware at the registered address on each cycle.
        info = rpc(host, "Shelly.GetDeviceInfo", {}, deadline)
        if mac_id(info.get("mac")) != identity:
            raise ReadError("hardware_identity_mismatch")
        model = str(info.get("model") or "").upper()
        app = str(info.get("app") or "").lower()
        meter = profile == "shelly_pro_3em" or model.startswith("SPEM-003CEBEU") or app == "pro3em"
        energy_error = None
        if meter:
            status = rpc(host, "EM.GetStatus", {"id": 0}, deadline)
            status_time = utc_now()
            try:
                energy = rpc(host, "EMData.GetStatus", {"id": 0}, deadline)
                energy_time = utc_now()
            except ReadError as exc:
                energy, energy_time, energy_error = {}, None, exc.code
            # Reuse the RELEASED normalizer and all of its stable entity keys.
            built = native._phase29_07i_build_pro3em_entities(
                registry_id=key, em_status=status, emdata_status=energy)
        else:
            # Only a known switch:0 measurement/control profile is supported in
            # 0.1.123. Never guess support for other Shelly models/components.
            known_switch = any(isinstance(e, dict) and e.get("source_component") == "switch:0"
                               for e in entry.get("entities") or [])
            if not known_switch:
                raise ReadError("runtime_adapter_not_supported")
            status = rpc(host, "Switch.GetStatus", {"id": 0}, deadline)
            status_time, energy_time = utc_now(), None
            built = native._phase28_r177g4_build_shelly_entities(registry_id=key, status=status)
        samples = {}
        for raw in built:
            item = _metadata(raw, key)
            if item is None:
                raise ReadError("normalizer_identity_mismatch")
            value = raw.get("state")
            if isinstance(value, bool):
                valid = item.get("capability") == "switch"
            elif isinstance(value, (int, float)):
                valid = math.isfinite(float(value))
            else:
                valid = item.get("capability") == "switch" and value in ("on", "off")
            if not valid:
                item.update(state="unavailable", value=None, has_value=False, available=False,
                            error_code="invalid_sample")
            else:
                received = energy_time if raw.get("source_component") == "emdata:0" else status_time
                item.update(state=value, value=value, available=True, has_value=True,
                            agent_received_at=received, updated_at=received,
                            timestamp_kind="rpc_response_received_not_physical_measurement")
            samples[item["entity_id"]] = item
        # Keep identities of temporarily unavailable channels, never their old values.
        for item in _missing_entities(key, entry, energy_error or "sample_not_reported"):
            samples.setdefault(item["entity_id"], item)
        result.update(available=True, entities=list(samples.values()),
                      updated_at=status_time, error_code=energy_error)
        return result
    except ReadError as exc:
        return failed_device(key, entry, exc.code)
    except Exception:
        return failed_device(key, entry, "runtime_adapter_failed")


def collect_registered_wifi_telemetry(*, native=None, registry=None, rpc=None,
                                      budget_seconds=CYCLE_BUDGET_SECONDS) -> dict:
    global _rotation
    started = time.monotonic()
    result = {"version": VERSION, "transport": "wifi", "source": "wifi_runtime_poll",
              "cycle_started_at": utc_now(), "devices": [], "registered_device_count": 0,
              "poll_start_interval_seconds": POLL_START_INTERVAL_SECONDS,
              "discovery_performed": False, "registry_write": False,
              "physical_measurement_time_verified": False}
    if not _lock.acquire(blocking=False):
        return {**result, "available": False, "error_code": "collector_already_running"}
    try:
        native = native or native_module()
        registry = read_registry(native) if registry is None else copy.deepcopy(registry)
        entries = registered_entries(registry)
        result["registered_device_count"] = len(entries)
        if not entries:
            return {**result, "available": True, "updated_at": utc_now()}
        shift = _rotation % len(entries)
        entries = entries[shift:] + entries[:shift]
        _rotation += MAX_WORKERS
        deadline = started + min(CYCLE_BUDGET_SECONDS, max(.01, float(budget_seconds)))
        for key, future in list(_pending.items()):
            if future.done():
                del _pending[key]
        futures = {}
        for key, entry in entries:
            if key in _pending:
                result["devices"].append(failed_device(key, entry, "previous_read_still_running"))
                continue
            future = _executor.submit(poll_one, key, entry, native, rpc or read_rpc, deadline)
            _pending[key] = future
            futures[future] = (key, entry)
        done, pending = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        for future in done:
            key, entry = futures[future]
            try:
                result["devices"].append(future.result())
            except Exception:
                result["devices"].append(failed_device(key, entry, "runtime_adapter_failed"))
        for future in pending:
            key, entry = futures[future]
            future.cancel()
            result["devices"].append(failed_device(key, entry, "cycle_budget_exceeded"))
        result.update(available=True, updated_at=utc_now(), cycle_budget_exceeded=bool(pending),
                      cycle_duration_seconds=round(time.monotonic() - started, 3))
        result["devices"].sort(key=lambda item: item["registry_id"])
        return result
    except ReadError as exc:
        return {**result, "available": False, "error_code": exc.code, "updated_at": utc_now()}
    except Exception:
        return {**result, "available": False, "error_code": "wifi_runtime_failed", "updated_at": utc_now()}
    finally:
        _lock.release()
