"""Pure, redacted comparison of protected backend configuration snapshots.

No database, application, filesystem, clock, or network access belongs here.
The caller supplies JSON data from a short, consistent, read-only transaction.
``modules`` must retain the SQL ``ORDER BY module_key`` order used by R13.

``configuration_sha256`` reproduces R13 exactly: five config fields and three
module fields, with absent fields represented as null. The per-field audit
additionally covers every supplied field and distinguishes absence from null,
empty objects, and empty arrays. It preserves array order.

Only ``_field_map`` is private comparison data; omit it from printed snapshots.
Even that map contains no input values or unknown raw key/module names. Use the
same >=16-byte random digest key for both snapshots; never print the key. The
public legacy SHA is retained only for continuity with R13, not for protecting
low-entropy individual values. Individual values and dynamic names use HMAC.

Classification evidence: TNG-IQ-FANDA commit
353cc1520f1770bd3130f34f09841f2acba620f7, cloud/backend/app/:
* routers/device.py::_store_building_daily_climate (576-965): only the two
  climate ``state``/``last_updated`` leaves are observations, not sensor IDs.
* services/phase34_00013_heating_permission_service.py::_dhw_r3_cycle
  (977-1049): the named TUV runtime leaves below, including last_loop_at.
* services/phase34_heating_setpoint_service.py::update_runtime and
  update_buffer_runtime (2123-2228): the named 00012 heating runtime leaves.
* services/phase32_dhw_{thermostat,disinfection,circulation}_service.py:
  tick_device/put_config persist the explicitly named runtime leaves below.

No entire runtime subtree, timestamp suffix, or generic ``state`` key is
exempted. Unknown paths and unexpected structures are configuration changes.
Control state is separately reported, never declared harmless. In particular,
the thermostat can automatically disable its top-level ``enabled`` setting;
that remains configuration. This comparison cannot resolve the old R13 event
or identify an actor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math


SCHEMA_VERSION = 1
MAX_CHANGED_PATHS = 64
_CATEGORIES = ("configuration", "control_state", "observation")
_LEGACY_CONFIG = (
    "remote_commands_enabled", "cloud_enabled", "settings",
    "active_modules", "config_version",
)
_LEGACY_MODULE = ("module", "status", "configuration")
_MISSING = object()

_HP = ("config", "settings", "heat_pump")
_PROJECT = ("modules", "individual_project", "configuration")
_HEATING = _PROJECT + ("heating_setpoint_entities_v1",)
_SUMMER = _HEATING + ("summer_winter", "runtime")
_BUFFER = _HEATING + ("buffer_temperature_control", "runtime")
_DHW13 = _HP + ("dhw_automation_v1", "runtime")
_THERMOSTAT = _PROJECT + ("dhw_thermostat_v1", "runtime")
_DISINFECTION = _PROJECT + ("dhw_disinfection_v1", "runtime")
_CIRCULATION = _PROJECT + ("dhw_circulation_schedule_v1", "runtime")
_CLIMATE = ("modules", "building", "configuration", "indoor_climate")

_OBSERVATIONS = {
    _DHW13: frozenset(("current_temp_c", "current_temp_reason", "last_loop_at")),
    _SUMMER: frozenset(("last_weather_temperature_c",)),
    _BUFFER: frozenset(("last_buffer_temperature_c", "last_outdoor_temperature_c")),
    _CLIMATE + ("temperature_entity",): frozenset(("state", "last_updated")),
    _CLIMATE + ("humidity_entity",): frozenset(("state", "last_updated")),
}
_CONTROL = {
    _DHW13: frozenset((
        "demand", "phase", "requested_state", "last_command_id", "last_action",
        "last_action_reason", "last_request_id",
    )),
    _SUMMER: frozenset((
        "manual_intent_enabled", "last_manual_command_id", "last_manual_command_at",
        "release_restore_pending", "last_automation_target", "last_automation_attempt_at",
        "last_automation_request_id", "last_gate_allowed", "last_gate_reason", "last_error",
    )),
    _BUFFER: frozenset(("last_demand", "last_target_c", "last_reason")),
    _THERMOSTAT: frozenset((
        "demand", "phase", "fault", "selected_source", "active_source", "request_id",
        "requested_state", "requested_source", "requested_entities", "sent_at",
        "handled_request", "off_retries", "last_off_at", "was_on",
    )),
    _DISINFECTION: frozenset((
        "active", "phase", "fault", "run_key", "started_at", "completed_at",
        "request_id", "requested_state", "requested_entities", "requested_action",
        "sent_at", "handled_request", "off_retries",
    )),
    _CIRCULATION: frozenset((
        "phase", "fault", "request_id", "last_handled_id", "off_attempts",
        "requested_state", "sent_at", "desired_state",
    )),
}
# These exact fields contain arrays of entity IDs; compare the whole array as
# one control field, including order. No other array gets this special case.
_CONTROL_ARRAYS = frozenset((
    _THERMOSTAT + ("requested_entities",),
    _DISINFECTION + ("requested_entities",),
))

# Visibility is a separate allowlist from classification. Known static schema
# labels aid diagnosis; unknown labels (including module names) are HMAC IDs.
_VISIBLE = {
    (): frozenset(("config", "modules")),
    ("config",): frozenset(_LEGACY_CONFIG + (
        "timezone", "language", "heartbeat_interval_seconds",
        "sync_interval_seconds", "telemetry_interval_seconds",
    )),
    ("config", "settings"): frozenset((
        "heat_pump", "individual_project", "zigbee_device_names",
        "client_installation_mode", "installation_process_type", "installation_type",
    )),
    _HP: frozenset(("heating_permission_v1", "dhw_automation_v1", "aku_control_v1",
                    "house_thermostat_v1", "floor_mixing_v1")),
    ("modules",): frozenset(("individual_project", "building", "heat_pump", "photovoltaic")),
    _PROJECT: frozenset(("heating_setpoint_entities_v1", "dhw_thermostat_v1",
                        "dhw_disinfection_v1", "dhw_circulation_schedule_v1",
                        "schema_entity_bindings_v1", "schema_entity_bindings_version")),
    _HEATING: frozenset(("schema", "revision", "timezone", "range_c", "zones", "summer_winter",
                        "buffer_temperature_control", "room_thermostat_control", "saved_at",
                        "last_actor_role", "last_actor_user_id", "last_request_id")),
    _HEATING + ("summer_winter",): frozenset(("enabled", "outdoor_threshold_c", "runtime")),
    _HEATING + ("buffer_temperature_control",): frozenset((
        "configured", "mode", "constant_target_c", "curve", "hysteresis_c", "runtime",
    )),
    ("modules", "building", "configuration"): frozenset((
        "indoor_climate", "main_breaker_current_a", "main_breaker_phases", "main_breaker_max_power_w",
    )),
    _CLIMATE: frozenset(("status", "primary_source", "device", "entities", "temperature_entity",
                        "humidity_entity", "battery_entity", "system_roles", "entity_count",
                        "verified_by_command_id", "verified_at")),
}
for _entity in ("temperature_entity", "humidity_entity", "battery_entity"):
    _VISIBLE[_CLIMATE + (_entity,)] = frozenset((
        "entity_id", "state", "unit_of_measurement", "device_class", "last_updated",
    ))
for _parent in (_HP + ("heating_permission_v1",), _HP + ("dhw_automation_v1",),
                _HP + ("aku_control_v1",), _HP + ("house_thermostat_v1",),
                _HP + ("floor_mixing_v1",), _PROJECT + ("dhw_thermostat_v1",),
                _PROJECT + ("dhw_disinfection_v1",), _PROJECT + ("dhw_circulation_schedule_v1",)):
    _VISIBLE[_parent] = frozenset((
        "schema", "version", "revision", "configured", "enabled", "mode", "target_c",
        "constant_target_c", "curve", "hysteresis_c", "automation_enabled", "actuation_enabled",
        "updated_at", "updated_by", "updated_role", "actor_role", "last_request_id", "request_id",
        "runtime", "saved_at", "requests", "hours", "output", "outputs", "bivalence_c",
        "bivalence_output", "armed_at", "disinfection_c", "disinfection_weekday",
        "target_supply_c", "target_configured", "bindings", "last_payload_hash", "stroke_seconds",
    ))
for _parent in set(_OBSERVATIONS) | set(_CONTROL):
    _VISIBLE[_parent] = _VISIBLE.get(_parent, frozenset()) | _OBSERVATIONS.get(
        _parent, frozenset()) | _CONTROL.get(_parent, frozenset())


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _hmac(key, domain, value):
    return hmac.new(key, domain + b"\x00" + _canonical(value), hashlib.sha256).hexdigest()


def _validate_json(value, ancestors=None, depth=0):
    if depth > 64:
        raise ValueError("snapshot JSON nesting limit exceeded")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (dict, list):
        raise ValueError("snapshot requires finite JSON values and string object keys")
    ancestors = set() if ancestors is None else ancestors
    identity = id(value)
    if identity in ancestors:
        raise ValueError("snapshot JSON must not contain a cycle")
    ancestors.add(identity)
    try:
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise ValueError("snapshot requires finite JSON values and string object keys")
            children = value.values()
        else:
            children = value
        for item in children:
            _validate_json(item, ancestors, depth + 1)
    finally:
        ancestors.remove(identity)


def _kind(value):
    if value is _MISSING:
        return "missing"
    return {type(None): "null", bool: "boolean", int: "integer", float: "number",
            str: "string", dict: "object", list: "array"}[type(value)]


def _category(path, value):
    # A new object/array injected at an observational leaf is not telemetry.
    if type(value) in (dict, list):
        if path in _CONTROL_ARRAYS and type(value) is list and all(type(v) is str for v in value):
            return "control_state"
        return "configuration"
    if path[-1:] and path[-1] in _OBSERVATIONS.get(path[:-1], ()):
        if path[-1] in ("last_loop_at", "last_updated", "current_temp_reason"):
            expected = (str, type(None))
        elif path[-1] == "state":
            expected = (str, int, float, type(None))
        else:
            expected = (int, float, type(None))
        return "observation" if type(value) in expected else "configuration"
    if path[-1:] and path[-1] in _CONTROL.get(path[:-1], ()):
        return "control_state"
    return "configuration"


def _safe_path(path, key):
    safe = []
    for index, segment in enumerate(path):
        parent = path[:index]
        visible = _VISIBLE.get(parent, ())
        if len(parent) == 2 and parent[0] == "modules":
            visible = _LEGACY_MODULE + ("manufacturer", "model")
        if type(segment) is int:
            safe.append("#" + str(segment))
        elif segment in visible:
            safe.append(segment)
        else:
            safe.append("~" + _hmac(key, b"path-segment", path[:index + 1])[:24])
    return "/" + "/".join(safe)


def make_snapshot(config: dict, modules: list[dict], *, digest_key: bytes) -> dict:
    """Build JSON-serializable summaries plus a private redacted comparison map.

    Extra config/module columns are audited but do not change the legacy hash.
    Module dictionaries use ``module`` for module_key; duplicates are rejected.
    Input data is not mutated. Exceptions contain no supplied values or names.
    """
    if type(config) is not dict or type(modules) is not list:
        raise ValueError("config must be an object and modules must be an array")
    if type(digest_key) is not bytes or len(digest_key) < 16:
        raise ValueError("digest_key must contain at least 16 bytes")
    _validate_json(config)
    _validate_json(modules)
    module_keys = []
    for module in modules:
        if type(module) is not dict or type(module.get("module")) is not str or not module["module"]:
            raise ValueError("every module requires a nonempty string module identifier")
        module_keys.append(module["module"])
    if len(set(module_keys)) != len(module_keys):
        raise ValueError("duplicate module identifiers make the snapshot ambiguous")

    legacy = {
        "config": {name: config.get(name) for name in _LEGACY_CONFIG},
        "modules": [{name: module.get(name) for name in _LEGACY_MODULE} for module in modules],
    }
    # Keep the exact R13 serialization, including spaces and ensure_ascii=True.
    legacy_hash = hashlib.sha256(json.dumps(legacy, sort_keys=True, default=str).encode()).hexdigest()
    entries = {}

    def add(path, value, *, kind=None, category=None, content=_MISSING):
        safe = _safe_path(path, digest_key)
        if safe in entries:
            raise ValueError("redacted path collision makes the snapshot ambiguous")
        value_kind = kind or _kind(value)
        if content is _MISSING:
            content = None if value is _MISSING or type(value) in (dict, list) else value
        entries[safe] = {
            "kind": value_kind,
            "category": category or _category(path, value),
            "hmac_sha256": _hmac(digest_key, b"field", [path, value_kind, content]),
        }

    def walk(path, value):
        if path in _CONTROL_ARRAYS and type(value) is list and all(type(v) is str for v in value):
            add(path, value, content=value)
            return
        # Container digests describe only their type. A changed child must not
        # falsely make every configuration ancestor appear changed as well.
        add(path, value)
        if type(value) is dict:
            for name in sorted(value):
                walk(path + (name,), value[name])
        elif type(value) is list:
            for index, item in enumerate(value):
                walk(path + (index,), item)

    audit_config = {name: config.get(name, _MISSING) for name in _LEGACY_CONFIG}
    audit_config.update(config)
    walk(("config",), audit_config)
    # Preserve R13 module ordering as a distinct field, without exporting names.
    add(("modules",), module_keys, kind="module_order", content=module_keys)
    for module in modules:
        audit_module = {name: module.get(name, _MISSING) for name in _LEGACY_MODULE}
        audit_module.update(module)
        walk(("modules", module["module"]), audit_module)

    counts = {category: sum(e["category"] == category for e in entries.values())
              for category in _CATEGORIES}
    return {
        "schema_version": SCHEMA_VERSION,
        "configuration_sha256": legacy_hash,
        "field_count": len(entries),
        "field_counts": counts,
        "_field_map": {
            "comparison_key_id": _hmac(digest_key, b"comparison-key", SCHEMA_VERSION),
            "entries": entries,
        },
    }


def diff_snapshots(before: dict, after: dict) -> dict:
    """Compare all fields; return bounded detail, complete counts, and no values.

    The old R13 mismatch remains unresolved even when this new interval matches.
    Snapshots with different HMAC keys/schemas cannot be compared and are rejected.
    Configuration/control changes are shown before observations if detail is capped.
    """
    try:
        if before["schema_version"] != SCHEMA_VERSION or after["schema_version"] != SCHEMA_VERSION:
            raise ValueError("snapshot schema mismatch")
        old = before["_field_map"]
        new = after["_field_map"]
        if not hmac.compare_digest(old["comparison_key_id"], new["comparison_key_id"]):
            raise ValueError("snapshots use different digest keys")
        old_entries, new_entries = old["entries"], new["entries"]
    except (KeyError, TypeError):
        raise ValueError("snapshot comparison data is missing or invalid") from None

    changes = []
    counts = {category: 0 for category in _CATEGORIES}
    for path in sorted(set(old_entries) | set(new_entries)):
        previous, current = old_entries.get(path), new_entries.get(path)
        if previous == current:
            continue
        categories = {e["category"] for e in (previous, current) if e is not None}
        # Unexpected structure/schema changes cannot downgrade an existing
        # configuration field to observation/control merely by changing its type.
        category = next(c for c in _CATEGORIES if c in categories)
        counts[category] += 1
        change = "added" if previous is None else "removed" if current is None else "modified"
        public_path = path
        if len(public_path) > 256:
            public_path = path[:192] + "/~path-" + hashlib.sha256(path.encode()).hexdigest()[:24]
        changes.append({
            "path": public_path,
            "path_truncated": public_path != path,
            "category": category,
            "change": change,
            "before": ({"kind": previous["kind"], "hmac_sha256": previous["hmac_sha256"]}
                       if previous is not None else None),
            "after": ({"kind": current["kind"], "hmac_sha256": current["hmac_sha256"]}
                      if current is not None else None),
        })
    changes.sort(key=lambda item: (_CATEGORIES.index(item["category"]), item["path"]))
    legacy_unchanged = before["configuration_sha256"] == after["configuration_sha256"]
    return {
        "schema_version": SCHEMA_VERSION,
        "r13_original_mismatch": "unresolved",
        "legacy_unchanged": legacy_unchanged,
        "unchanged": not changes and legacy_unchanged,
        "configuration_changed": counts["configuration"] > 0,
        "control_state_changed": counts["control_state"] > 0,
        "observation_changed": counts["observation"] > 0,
        "changed_count": len(changes),
        "changed_counts": counts,
        "changed_paths": changes[:MAX_CHANGED_PATHS],
        "changed_paths_limit": MAX_CHANGED_PATHS,
        "changed_paths_truncated": len(changes) > MAX_CHANGED_PATHS,
    }
