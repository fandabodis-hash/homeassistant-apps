"""Read-only identification of the two SONOFF MINI-ZB2GS relay channels.

A channel is advertised only if HA/ZHA classifies it as a binary power output.
The ordering is deterministic across telemetry refreshes. Actual wiring must
be verified by the installer before assigning loads.
"""
from __future__ import annotations

import re
from typing import Any

OUTPUT = "binary_power_output"
MODEL_RE = re.compile(r"MINI[\s_-]?ZB2GS", re.IGNORECASE)
SUFFIX_RE = re.compile(r"_(\d+)$")


def mini_zb2gs_relay_channels(
    model: object,
    entities: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not MODEL_RE.search(str(model or "")) or not isinstance(entities, list):
        return []

    outputs: dict[str, dict[str, Any]] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("entity_id") or "").strip()
        if not entity_id or entity_id.split(".", 1)[0] not in ("switch", "light"):
            continue
        if str(entity.get("entity_category") or "").lower() in ("config", "diagnostic"):
            continue
        caps = entity.get("capabilities")
        if not isinstance(caps, list):
            caps = []
        if OUTPUT not in caps and entity.get("capability") != OUTPUT:
            continue
        outputs[entity_id.lower()] = entity

    if len(outputs) != 2:
        return []

    def key(entity_id: str) -> tuple[str, int, str]:
        stem = entity_id.split(".", 1)[1]
        match = SUFFIX_RE.search(stem)
        return SUFFIX_RE.sub("", stem), int(match.group(1)) if match else 0, entity_id

    ordered = sorted(outputs, key=key)
    if key(ordered[0])[0] != key(ordered[1])[0]:
        return []

    return [
        {
            "channel": n,
            "entity_id": entity_id,
            "capability": OUTPUT,
            "order_basis": "stable_ha_entity_id_suffix",
        }
        for n, entity_id in enumerate(ordered, start=1)
    ]
