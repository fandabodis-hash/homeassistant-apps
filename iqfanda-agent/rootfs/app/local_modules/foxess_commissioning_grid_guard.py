"""F58 FoxESS protected-grid commissioning: local-only readback, fail closed.

An independent, physically validated, positive-import AC grid meter must be
mapped in a local installer-owned file. This module never changes zero-export
settings or sends Modbus requests. Its checks are necessary but do not replace
the inverter's separately verified hardware export limiter.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from urllib.request import Request, urlopen

CONFIG_PATH = Path("/config/foxess_grid_commissioning_guard.json")
DEVICE_PATH = Path("/config/device.json")
MIN_INITIAL_IMPORT_W = 800    # 500 W canary + 300 W reserve
MIN_RUNNING_IMPORT_W = 300
MAX_DATA_AGE_SECONDS = 3
PROTECTED_UUID_00007 = "1c25a8d4-b986-43ee-b2fc-eaa152e9bdf8"


class GridGuardUnavailable(RuntimeError):
    """The safety evidence is unavailable; no new battery discharge."""


def _document(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise GridGuardUnavailable("verified grid mapping file is absent or a symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise GridGuardUnavailable("verified grid mapping cannot be read") from exc
    if not isinstance(value, dict):
        raise GridGuardUnavailable("verified grid mapping is not a JSON object")
    return value


def is_protected_00007(*, identity_path: Path = DEVICE_PATH) -> bool:
    """Never infer 00007 by IP, hostname or mutable cloud model name."""
    document = _document(identity_path)
    return str(document.get("device_uuid") or "").strip().lower() == PROTECTED_UUID_00007


class LocalGridImportGuard:
    def __init__(self, *, config_path: Path = CONFIG_PATH,
                 identity_path: Path = DEVICE_PATH,
                 token: str | None = None, opener=None, clock=None):
        self.config = _document(config_path)
        identity = _document(identity_path)
        expected_uuid = str(identity.get("device_uuid") or "").strip().lower()
        sensor_uuid = str(self.config.get("device_uuid") or "").strip().lower()
        if not expected_uuid or sensor_uuid != expected_uuid:
            raise GridGuardUnavailable("grid mapping belongs to another device_uuid")
        if (self.config.get("schema_version") != 1 or
                self.config.get("physically_verified") is not True or
                self.config.get("independent_grid_meter") is not True or
                self.config.get("hardware_zero_export_verified") is not True or
                self.config.get("positive_import_watts") is not True or
                not str(self.config.get("evidence_id") or "").strip()):
            raise GridGuardUnavailable("independent meter/zero-export verification is incomplete")
        entity = self.config.get("entity_id")
        if (not isinstance(entity, str) or
                not entity.startswith("sensor.") or len(entity) > 180 or
                not all(ch.isalnum() or ch in "_." for ch in entity)):
            raise GridGuardUnavailable("grid meter entity_id is not a verified sensor")
        self.entity = entity
        self.token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        if not self.token:
            raise GridGuardUnavailable("local Home Assistant API permission is missing")
        self.opener = opener or urlopen
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _import_w(self) -> float:
        request = Request(
            "http://supervisor/core/api/states/" + self.entity,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"},
            method="GET",
        )
        try:
            with self.opener(request, timeout=2.0) as response:
                body = json.load(response)
        except Exception as exc:
            raise GridGuardUnavailable("local meter API read failed") from exc
        if not isinstance(body, dict) or body.get("entity_id") != self.entity:
            raise GridGuardUnavailable("grid meter returned another entity")
        attributes = body.get("attributes")
        if not isinstance(attributes, dict) or attributes.get("unit_of_measurement") != "W":
            raise GridGuardUnavailable("grid meter must report watts")
        try:
            value = float(body["state"])
            updated = datetime.fromisoformat(str(body["last_updated"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise GridGuardUnavailable("grid meter state or timestamp is invalid") from exc
        if (updated.tzinfo is None or not math.isfinite(value) or value < -100000 or
                value > 100000):
            raise GridGuardUnavailable("grid meter value invalid or timestamp unzoned")
        age = (self.clock() - updated.astimezone(timezone.utc)).total_seconds()
        if not 0 <= age <= MAX_DATA_AGE_SECONDS:
            raise GridGuardUnavailable("grid meter is stale or future-dated")
        return value

    def check_before_discharge(self) -> float:
        watts = self._import_w()
        if watts < MIN_INITIAL_IMPORT_W:
            raise GridGuardUnavailable("grid import is below the 500W canary + reserve")
        return watts

    def check_during_discharge(self) -> float:
        watts = self._import_w()
        if watts < MIN_RUNNING_IMPORT_W:
            raise GridGuardUnavailable("grid import fell below zero-export reserve; AUTO required")
        return watts


def require_protected_00007_discharge_safety(*, identity_path: Path = DEVICE_PATH,
                                             config_path: Path = CONFIG_PATH,
                                             renewal: bool = False) -> None:
    """A scoped extra safety for 00007; STOP/AUTO bypasses this check.

    Not a claim that this 10-second supervisor can replace hardware export
    limiting. If a correctly scoped verified local meter is unavailable,
    refuse any new or renewed 00007 discharge request.
    """
    if is_protected_00007(identity_path=identity_path):
        LocalGridImportGuard(
            identity_path=identity_path, config_path=config_path
        ).check_during_discharge() if renewal is True else LocalGridImportGuard(
            identity_path=identity_path, config_path=config_path
        ).check_before_discharge()
