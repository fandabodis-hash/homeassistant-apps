"""F59: WAN-independent module execution host.

The cloud can store configuration, but this host consumes only the cached,
locally persisted snapshot. It never fetches cloud data, dispatches DeviceCommand
or performs a Modbus/Zigbee write. Future reviewed modules own their own logic.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Mapping

from device_config import load_cached_cloud_config
from runtime_config import load_device_identity
from local_modules.registry import REGISTERED_LOCAL_TICKS, LocalTick

LOGGER = logging.getLogger(__name__)
POLL_SECONDS = 15.0


def _active_keys(configuration: dict[str, Any]) -> set[str]:
    """Find strictly enabled installation modules, not template availability."""
    raw = configuration.get("active_modules")
    if not isinstance(raw, list):
        return set()
    keys: set[str] = set()
    for module in raw:
        if isinstance(module, str) and module.strip():
            keys.add(module.strip())
        elif isinstance(module, dict):
            key = str(module.get("module_key") or "").strip()
            if key and module.get("enabled", True) is True and module.get("active", True) is True:
                keys.add(key)
    return keys


def run_once(*, registry: Mapping[str, LocalTick] | None = None,
             config_loader: Callable[[], dict | None] = load_cached_cloud_config,
             identity_loader: Callable[[], dict] = load_device_identity) -> dict[str, Any]:
    """Run registered local modules once; never duplicate existing owners."""
    handlers = REGISTERED_LOCAL_TICKS if registry is None else registry
    if not handlers:
        return {"status": "no_migrated_modules", "executed": []}
    identity = identity_loader()
    device_uuid = identity.get("device_uuid") if isinstance(identity, dict) else None
    cached = config_loader()
    if (not isinstance(device_uuid, str) or not device_uuid or
            not isinstance(cached, dict) or cached.get("device_uuid") != device_uuid):
        return {"status": "device_identity_or_cached_config_invalid", "executed": []}
    active = _active_keys(cached)
    selected = sorted(set(handlers) & active)
    for name in selected:
        # Cloud-originated configuration may select from vetted modules, but
        # cannot supply Python paths or arbitrary executable code.
        handlers[name](cached)
    return {"status": "ok", "device_uuid": device_uuid, "executed": selected}


def run_forever() -> None:
    """Dedicated local host; no migrated handlers means a passive idle loop."""
    while True:
        try:
            run_once()
        except Exception:
            LOGGER.exception("F59 local module cycle failed; requires safe local owner handling")
        time.sleep(POLL_SECONDS)
