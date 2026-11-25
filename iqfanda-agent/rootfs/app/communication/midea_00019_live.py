"""F152: local read-only Midea live telemetry, exact 00019/FTDI/BMS1 only.

No command queue, no writes, no control, no cloud dependency for local reads.
The heartbeat only starts the isolated worker and copies its cached snapshot.
"""
from __future__ import annotations

import copy
import threading
import time
from datetime import datetime, timezone
from typing import Any

from communication.midea_00019_readonly import (
    SERIAL, FTDI_PORT, BAUDRATE, STOP_BITS, PARITY, decode, u16,
)
from communication.midea_00019_cloud_probe import DEVICE_UUID, SITE_ID
from communication.midea_00019_ftdi_inventory import enrich_if_verified
from communication.modbus_bus_lock import ziskej_zamek_modbus_sbernice

# Exactly the physically successful F151 FC03 addresses; no scan.
BLOCKS = ((0, 2), (100, 11), (124, 1), (129, 1), (138, 14), (199, 1))
INTERVAL_SECONDS = 30
MAX_AGE_SECONDS = 120
_state_lock = threading.Lock()
_worker = None
_snapshot = None
_snapshot_mono = 0.0


def exact_identity(identity: Any) -> bool:
    return (isinstance(identity, dict)
            and str(identity.get("device_uuid") or "").lower() == DEVICE_UUID
            and str(identity.get("site_id") or "").lower() == SITE_ID)


def verified_port(identity):
    state = enrich_if_verified({}, device_uuid=identity["device_uuid"])
    ports = state.get("communicators", [])
    return len(ports) == 1 and ports[0].get("preferred_path") == FTDI_PORT


def read_sample(identity, *, client_factory=None, lock_factory=ziskej_zamek_modbus_sbernice,
                port_checker=verified_port):
    """Only this function performs serial I/O; never holds a heartbeat lock."""
    if not exact_identity(identity) or not port_checker(identity):
        return None
    lock = lock_factory(FTDI_PORT)
    if not lock.acquire(blocking=False):
        return None
    client = None
    raw = {}
    try:
        if client_factory is None:
            from pymodbus.client import ModbusSerialClient
            client_factory = ModbusSerialClient
        client = client_factory(port=FTDI_PORT, baudrate=BAUDRATE, bytesize=8,
                                parity=PARITY, stopbits=STOP_BITS, timeout=1.2, retries=0)
        if client.connect() is not True:
            return None
        for address, count in BLOCKS:
            try:
                response = client.read_holding_registers(address=address, count=count, device_id=1)
                if response is None or response.isError():
                    continue
                words = getattr(response, "registers", None)
                if not isinstance(words, list) or len(words) != count:
                    continue
                checked = [u16(word) for word in words]
                raw.update({address + i: word for i, word in enumerate(checked)})
            except Exception:
                pass
            finally:
                time.sleep(0.12)
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            lock.release()
    # No old values reused after failure; require actual temperature pair.
    if not {104, 105}.issubset(raw):
        return None
    measurements = decode({k: v for k, v in raw.items() if k >= 100})
    measurements["zone1_enabled"] = bool(raw[0] & 2) if 0 in raw else None
    measurements["mode_setting"] = raw.get(1)
    return {
        "schema": "midea_00019_live_v1", "serial_number": SERIAL,
        "device_uuid": DEVICE_UUID, "site_id": SITE_ID,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "modbus_unit_id": 1, "modbus_function_code": 3,
        "read_only": True, "writes_performed": False, "control_enabled": False,
        "physical_modbus_read": True, "profile_verified": False,
        "raw_registers": {str(k): v for k, v in sorted(raw.items())},
        "measurements": measurements,
    }


def _run():
    global _snapshot, _snapshot_mono
    from runtime_config import load_device_identity
    while True:
        try:
            identity = load_device_identity()
            if not exact_identity(identity):
                with _state_lock:
                    _snapshot = None
                return
            sample = read_sample(identity)
        except Exception:
            sample = None
        with _state_lock:
            _snapshot = sample
            _snapshot_mono = time.monotonic()
        time.sleep(INTERVAL_SECONDS)


def get_or_start_snapshot(identity):
    """Nonblocking caller, exact identity, one worker; no credentials exported."""
    global _worker
    if not exact_identity(identity):
        return None
    with _state_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="midea-00019-live", daemon=True)
            _worker.start()
        if _snapshot is None or time.monotonic() - _snapshot_mono > MAX_AGE_SECONDS:
            return None
        return copy.deepcopy(_snapshot)
