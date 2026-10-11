"""F43: one relay owner for a verified active cloud weekly thermostat.

Pure check only. No relay command, API request, mutation, or I/O.
All inactive/unverified weekly targets retain their existing SOC/Spot path.
"""
from __future__ import annotations

from typing import Any


def is_verified_weekly_owner(
    cloud_config: dict[str, Any],
    *,
    target_id: str,
    output_reference: str,
) -> bool:
    if not isinstance(cloud_config, dict) or not target_id or not output_reference:
        return False
    runtimes = cloud_config.get("module_runtime_configurations")
    if not isinstance(runtimes, list):
        return False
    modules = [
        r for r in runtimes if isinstance(r, dict)
        and r.get("module_key") == "pv_surplus_control"
        and isinstance(r.get("configuration"), dict)
    ]
    if len(modules) != 1:
        return False
    config = modules[0]["configuration"]
    programs = config.get("weekly_temperature_programs")
    if not isinstance(programs, dict):
        return False
    program = programs.get(target_id)
    if not isinstance(program, dict) or program.get("enabled") is not True:
        return False
    targets = config.get("targets")
    if not isinstance(targets, list):
        return False
    matched = [
        t for t in targets if isinstance(t, dict)
        and str(t.get("id") or "") == target_id
    ]
    if len(matched) != 1:
        return False
    target = matched[0]
    if (target.get("type") != "generic_load"
            or target.get("enabled") is not True
            or target.get("configuration_status") != "verified"):
        return False
    output = target.get("output")
    role = target.get("weekly_control_output")
    if not isinstance(output, dict) or not isinstance(role, dict):
        return False
    if not (
        output.get("status") == "verified"
        and output.get("reference") == output_reference
        and role.get("status") == "verified"
        and role.get("role") == "thermostat_permission"
        and role.get("reference") == output_reference
        and bool(str(role.get("device_reg_id") or "").strip())
    ):
        return False
    for other in targets:
        if not isinstance(other, dict):
            continue
        if str(other.get("id") or "") == target_id or other.get("enabled") is not True:
            continue
        other_output = other.get("output")
        other_role = other.get("weekly_control_output")
        if (isinstance(other_output, dict)
                and other_output.get("reference") == output_reference):
            return False
        if (isinstance(other_role, dict)
                and other_role.get("reference") == output_reference):
            return False
    return True
