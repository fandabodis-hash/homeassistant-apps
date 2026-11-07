#!/usr/bin/env python3
"""Offline source/evidence guard. It never contacts an inverter or marks a test passed.

The manifest is a review gate, not an unchangeable lock or a device safety control.
Run --refresh REASON after a reviewed change, then commit the updated manifest.
Physical evidence always refers to exact source bytes and actual test hardware.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = "iqfanda-agent/contracts/manual_battery_release.json"
EVIDENCE_PATH = "iqfanda-agent/contracts/manual_battery_physical_evidence.json"
ROOTFS = "iqfanda-agent/rootfs/"
APP = ROOTFS + "app/"
PRODUCTION_MANIFEST_PATH = APP + "protection/manual_battery_manifest.json"
PROFILE_ROOT = APP + "communication/inverter_profiles/"
PROFILE_BRANDS = {
    "goodwe_et": "GoodWe",
    "goodwe_et_15_30kw": "GoodWe",
    "foxess_h3_10_0_e": "FoxESS",
    "solax_x3_hybrid_g4": "SolaX",
    "deye_3x_lv_off_grid": "Deye",
    "deye_50_hybrid_hv": "Deye",
}
MINIMUM_PATHS = {
    "iqfanda-agent/Dockerfile",
    "iqfanda-agent/config.yaml",
    ROOTFS + "etc/services.d/iqfanda/run",
    APP + "VERSION",
    APP + "manual_battery_runtime.py",
    APP + "foxess_zero_remote_release.py",
    APP + "battery_control_canary.py",
    APP + "spot_battery_intent.py",
    APP + "command_worker.py",
    APP + "startup.py",
    APP + "heartbeat.py",
    APP + "device_config.py",
    APP + "communication/battery_feature_guard.py",
    APP + "communication/battery_control_safety.py",
    APP + "local_modules/foxess_commissioning_grid_guard.py",
    APP + "local_modules/battery_pin_transition.py",
    "iqfanda-agent/tests/test_f58_foxess_grid_commissioning_guard.py",
    "iqfanda-agent/tests/test_f58_battery_pin_transition.py",
    APP + "communication/inverter_control_adapter.py",
    APP + "communication/inverter_adapter.py",
    APP + "communication/goodwe_probe.py",
    APP + "communication/json_utils.py",
    APP + "communication/inverter_profiles/catalog.json",
    APP + "communication/foxess_ems_controller.py",
    APP + "communication/foxess_remote_control_probe.py",
    APP + "communication/goodwe_ems_controller.py",
    APP + "communication/solax_ems_controller.py",
    APP + "communication/modbus_bus_lock.py",
    "iqfanda-agent/tests/test_manual_battery_runtime.py",
    "iqfanda-agent/tests/test_foxess_zero_remote_release.py",
    "iqfanda-agent/tests/test_manual_battery_integration.py",
    "iqfanda-agent/tests/test_manual_battery_release_guard.py",
    "iqfanda-agent/tests/test_manual_battery_feature_guard.py",
    "iqfanda-agent/tests/test_foxess_battery_control.py",
    "iqfanda-agent/tests/test_solax_battery_control.py",
    "iqfanda-agent/tests/test_battery_control_canary.py",
    "iqfanda-agent/tests/test_foxess_remote_control_probe.py",
    "iqfanda-agent/tests/test_manual_battery_preflight.py",
    "iqfanda-agent/scripts/check_manual_battery_release.py",
    "iqfanda-agent/scripts/read_addon_update_preflight.py",
    "iqfanda-agent/scripts/check_agent_version.py",
    "iqfanda-agent/MANUAL_BATTERY_RELEASE.md",
    ".github/workflows/manual-battery-release-guard.yml",
} | {PROFILE_ROOT + key + ".json" for key in PROFILE_BRANDS}
REQUIRED_ACTIONS = {"auto", "charge_grid", "discharge_grid"}
PHYSICAL_CHECKS = {
    "charge_measured", "discharge_measured", "manual_stop_confirmed",
    "target_soc_stop", "minimum_soc_stop", "expiry_stop",
    "wan_loss_local_control", "agent_restart_safe", "bms_limits_respected",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def safe_path(root, relative):
    require(isinstance(relative, str) and relative and "\\" not in relative,
            "Invalid protected path")
    require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
            "Protected paths must stay inside the repository")
    root = Path(root).resolve()
    path = root / relative
    require(not path.is_symlink(), "Protected file must not be a symlink: " + relative)
    require(path.resolve().is_relative_to(root), "Protected path escapes repository")
    return path


def read_version(root):
    version = (Path(root) / (APP + "VERSION")).read_text().strip()
    require(re.fullmatch(r"\d+\.\d+\.\d+", version) is not None, "Invalid VERSION")
    config = (Path(root) / "iqfanda-agent/config.yaml").read_text(encoding="utf-8-sig")
    require(re.search(r'^version:\s*"' + re.escape(version) + r'"\s*$', config, re.M),
            "config.yaml version differs from VERSION")
    require(re.search(r'^  software_version:\s*"' + re.escape(version) + r'"\s*$', config, re.M),
            "software_version differs from VERSION")
    return version


def source_snapshot(root, paths):
    require(MINIMUM_PATHS <= set(paths), "Manifest removed a mandatory integration or driver")
    result = {}
    for relative in sorted(set(paths)):
        path = safe_path(root, relative)
        require(path.is_file(), "Protected file is missing: " + relative)
        result[relative] = digest(path.read_bytes())
    return result


def profile_snapshot(root):
    result = {}
    for profile_id, brand in PROFILE_BRANDS.items():
        profile = load_json(safe_path(root, PROFILE_ROOT + profile_id + ".json"))
        require(isinstance(profile, dict), profile_id + ": invalid profile")
        capabilities = profile.get("control_capabilities", {})
        require(isinstance(capabilities, dict), profile_id + ": invalid capabilities")
        capability = capabilities.get("battery_charge_control")
        require(capability is None or isinstance(capability, dict), profile_id + ": invalid battery capability")
        unavailable = (isinstance(capability, dict) and capability.get("supported_actions") == []
                       and capability.get("discharge_allowed") is False
                       and isinstance(capability.get("unsupported_reason"), str)
                       and bool(capability["unsupported_reason"].strip()))
        present = isinstance(capability, dict) and not unavailable
        if present:
            require(capability.get("schema_version") == 1, profile_id + ": unsupported capability schema")
            require(REQUIRED_ACTIONS <= set(capability.get("supported_actions", [])),
                    profile_id + ": charge, discharge and AUTO must remain together")
            require(capability.get("discharge_allowed") is True,
                    profile_id + ": discharge capability cannot be silently disabled")
            module = capability.get("driver_module", "")
            function = capability.get("driver_function", "")
            require(isinstance(module, str) and module.startswith("communication.")
                    and all(part.isidentifier() for part in module.split(".")),
                    profile_id + ": invalid driver module")
            require(isinstance(function, str) and function.isidentifier(),
                    profile_id + ": invalid driver function")
            require(safe_path(root, APP + module.replace(".", "/") + ".py").is_file(),
                    profile_id + ": declared driver file is missing")
        result[profile_id] = {
            "brand": brand,
            "control_available": present,
            "driver_module": capability.get("driver_module") if present else None,
            "driver_function": capability.get("driver_function") if present else None,
            "supported_actions": sorted(capability.get("supported_actions", [])) if present else [],
            "unavailable_reason": capability["unsupported_reason"] if unavailable else None,
        }
    return result


def expected_snapshot(root, protected_paths):
    profiles = profile_snapshot(root)
    paths = set(protected_paths)
    paths.update(str(path.relative_to(root)) for path in (Path(root) / "iqfanda-agent/tests").glob("test_*battery*.py"))
    require(PRODUCTION_MANIFEST_PATH not in paths, "Derived production manifest cannot hash itself")
    for profile in profiles.values():
        if profile["control_available"]:
            paths.add(APP + profile["driver_module"].replace(".", "/") + ".py")
    files = source_snapshot(root, paths)
    version = read_version(root)
    runtime = production_manifest(version, files)
    fingerprint = digest(canonical({"agent_version": version, "files": files, "profiles": profiles}))
    return {"agent_version": version, "files": files, "profiles": profiles,
            "source_fingerprint": fingerprint, "runtime_fingerprint": runtime["runtime_fingerprint"]}


def production_manifest(version, files):
    production_files = {path[len(APP):]: value for path, value in sorted(files.items()) if path.startswith(APP)}
    rootfs_files = {path[len(ROOTFS):]: value for path, value in sorted(files.items())
                    if path.startswith(ROOTFS) and not path.startswith(APP)}
    build_inputs = {name: files["iqfanda-agent/" + name] for name in ("Dockerfile", "config.yaml")}
    payload = {"agent_version": version, "files": production_files,
               "rootfs_files": rootfs_files, "build_inputs": build_inputs}
    return {"schema_version": 1, "feature": "local_manual_battery", **payload,
            "runtime_fingerprint": digest(canonical(payload))}


def validate_evidence(evidence):
    require(isinstance(evidence, dict) and evidence.get("schema_version") == 1,
            "Invalid physical evidence schema")
    records = evidence.get("records")
    revocations = evidence.get("revocations")
    require(isinstance(records, list) and isinstance(revocations, list), "Evidence lists are required")
    ids = set()
    for record in records:
        require(isinstance(record, dict), "Invalid evidence record")
        record_id = record.get("id")
        require(isinstance(record_id, str) and record_id and record_id not in ids, "Duplicate/missing evidence id")
        ids.add(record_id)
        require(record.get("profile_id") in PROFILE_BRANDS, "Evidence names an unknown profile")
        require(bool(HEX64.fullmatch(str(record.get("source_fingerprint", "")))),
                "Evidence requires an exact source fingerprint")
        require(bool(HEX64.fullmatch(str(record.get("runtime_fingerprint", "")))),
                "Evidence requires an exact runtime fingerprint")
        require(record.get("status") == "physically_verified", "Only completed evidence belongs in records")
        require(record.get("verification_origin") == "physical_installation",
                "Unit tests or source inspection cannot certify physical operation")
        for name in ("agent_version", "device_serial_number", "inverter_model", "inverter_firmware",
                     "battery_model", "verified_at", "reviewed_by", "report_reference"):
            require(isinstance(record.get(name), str) and record[name].strip(), "Evidence lacks " + name)
        require(bool(HEX64.fullmatch(str(record.get("report_sha256", "")))), "Evidence lacks report SHA256")
        try:
            when = datetime.fromisoformat(record["verified_at"].replace("Z", "+00:00"))
            require(when.tzinfo is not None and when <= datetime.now(timezone.utc),
                    "Evidence timestamp must be a past UTC-aware time")
        except (ValueError, TypeError) as error:
            raise ContractError("Invalid evidence timestamp") from error
        checks = record.get("checks")
        require(isinstance(checks, dict) and PHYSICAL_CHECKS <= set(checks), "Physical checks are incomplete")
        for check in PHYSICAL_CHECKS:
            value = checks[check]
            require(isinstance(value, dict) and value.get("passed") is True
                    and isinstance(value.get("report_section"), str) and value["report_section"].strip(),
                    "Missing physical evidence section: " + check)
    revoked = set()
    for revocation in revocations:
        require(isinstance(revocation, dict) and revocation.get("evidence_id") in ids,
                "Revocation names unknown evidence")
        require(revocation["evidence_id"] not in revoked, "Duplicate revocation")
        require(isinstance(revocation.get("reason"), str) and revocation["reason"].strip(),
                "Revocation needs a reason")
        revoked.add(revocation["evidence_id"])
    return records, revoked


def evidence_status(contract, evidence):
    records, revoked = validate_evidence(evidence)
    history = {record["source_fingerprint"]: record for record in contract["change_history"]}
    for record in records:
        original = history.get(record["source_fingerprint"], {})
        require(original.get("runtime_fingerprint") == record["runtime_fingerprint"]
                and original.get("agent_version") == record["agent_version"],
                "Physical evidence must refer to a recorded source/runtime revision")
    statuses = {}
    for profile_id, profile in contract["profiles"].items():
        matches = [record["id"] for record in records
                   if record["id"] not in revoked and record["profile_id"] == profile_id
                   and record["runtime_fingerprint"] == contract["runtime_fingerprint"]
                   and record["agent_version"] == contract["agent_version"]]
        statuses[profile_id] = {
            "brand": profile["brand"],
            "control_available": profile["control_available"],
            "physical_status": "physically_verified" if matches and profile["control_available"] else "pending",
            "evidence_ids": matches,
            "scope": "Only the recorded installation, firmware and battery; not every model of the brand",
        }
    return statuses


def validate_history(contract, base=None):
    require(isinstance(contract, dict) and contract.get("schema_version") == 1
            and contract.get("feature") == "local_manual_battery",
            "Invalid release contract")
    require(isinstance(contract.get("files"), dict) and isinstance(contract.get("profiles"), dict),
            "Invalid release contract source maps")
    history = contract.get("change_history")
    require(isinstance(history, list) and history, "Reviewed change history is required")
    for index, record in enumerate(history, 1):
        require(isinstance(record, dict), "Invalid change record")
        require(record.get("revision") == index, "Change revisions must be sequential")
        require(isinstance(record.get("reason"), str) and len(record["reason"].strip()) >= 12,
                "Every source revision needs a meaningful reason")
        require(bool(HEX64.fullmatch(str(record.get("source_fingerprint", "")))), "Invalid historical fingerprint")
        require(bool(HEX64.fullmatch(str(record.get("runtime_fingerprint", "")))), "Invalid historical runtime fingerprint")
    require(history[-1]["source_fingerprint"] == contract["source_fingerprint"],
            "Current bytes have no matching change record")
    require(history[-1]["runtime_fingerprint"] == contract["runtime_fingerprint"]
            and history[-1]["agent_version"] == contract["agent_version"],
            "Current runtime has no matching change record")
    if base:
        old = base.get("change_history", [])
        require(history[:len(old)] == old, "Existing change records must not be edited or deleted")
        if base.get("source_fingerprint") != contract["source_fingerprint"]:
            require(len(history) > len(old), "Source changes require a new review record")
        for profile_id, profile in base.get("profiles", {}).items():
            if profile.get("control_available"):
                require(contract["profiles"].get(profile_id, {}).get("control_available") is True,
                        "Existing battery capability was removed: " + profile_id)


def check(root, contract, evidence, base_contract=None, base_evidence=None):
    validate_history(contract, base_contract)
    snapshot = expected_snapshot(root, contract.get("files", {}).keys())
    for key, value in snapshot.items():
        require(contract.get(key) == value,
                "Release contract drift in " + key + "; review change, then --refresh REASON")
    production = load_json(safe_path(root, PRODUCTION_MANIFEST_PATH))
    require(production == production_manifest(snapshot["agent_version"], snapshot["files"]),
            "Production manifest drift; review change, then --refresh REASON")
    if base_evidence:
        for key in ("records", "revocations"):
            old = base_evidence.get(key, [])
            require(evidence.get(key, [])[:len(old)] == old,
                    "Historical evidence must remain append-only: " + key)
    return evidence_status(contract, evidence)


def git_json(root, ref, path):
    require(re.fullmatch(r"[0-9a-fA-F]{40,64}", ref) is not None, "Base ref must be an exact commit SHA")
    process = subprocess.run(["git", "show", ref + ":" + path], cwd=root,
                             capture_output=True, text=True, check=False)
    if process.returncode:
        # A first introduction of this contract is allowed; an invalid ref is not.
        valid = subprocess.run(["git", "cat-file", "-e", ref + "^{commit}"], cwd=root,
                               capture_output=True, check=False)
        require(valid.returncode == 0, "Base commit is unavailable")
        return None
    return json.loads(process.stdout)


def refresh(root, reason):
    require(len(reason.strip()) >= 12, "Use a meaningful reviewed-change reason")
    path = Path(root) / CONTRACT_PATH
    previous = load_json(path) if path.exists() else {}
    snapshot = expected_snapshot(root, set(previous.get("files", {})) | MINIMUM_PATHS)
    history = list(previous.get("change_history", []))
    if previous.get("source_fingerprint") != snapshot["source_fingerprint"]:
        history.append({"revision": len(history) + 1, "reason": reason.strip(),
                        "source_fingerprint": snapshot["source_fingerprint"],
                        "runtime_fingerprint": snapshot["runtime_fingerprint"],
                        "agent_version": snapshot["agent_version"]})
    result = {"schema_version": 1, "feature": "local_manual_battery", **snapshot,
              "change_history": history}
    validate_history(result, previous or None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    production_path = safe_path(root, PRODUCTION_MANIFEST_PATH)
    production_path.parent.mkdir(parents=True, exist_ok=True)
    production_path.write_text(json.dumps(production_manifest(snapshot["agent_version"], snapshot["files"]),
                                          indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Never create, edit, or promote physical evidence here.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--refresh", metavar="REASON")
    parser.add_argument("--base-ref")
    parser.add_argument("--require-physical", action="append", choices=sorted(PROFILE_BRANDS), default=[])
    args = parser.parse_args()
    try:
        if args.refresh:
            refresh(args.root, args.refresh)
        contract = load_json(args.root / CONTRACT_PATH)
        evidence = load_json(args.root / EVIDENCE_PATH)
        base_contract = git_json(args.root, args.base_ref, CONTRACT_PATH) if args.base_ref else None
        base_evidence = git_json(args.root, args.base_ref, EVIDENCE_PATH) if args.base_ref else None
        status = check(args.root, contract, evidence, base_contract, base_evidence)
        for profile in args.require_physical:
            require(status[profile]["physical_status"] == "physically_verified",
                    "Physical verification is still pending for " + profile)
        print(json.dumps({"guard": "pass", "source_fingerprint": contract["source_fingerprint"],
                          "runtime_fingerprint": contract["runtime_fingerprint"],
                          "agent_version": contract["agent_version"], "profiles": status,
                          "live_device_access": False, "physical_evidence_created": False}, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as error:
        print("MANUAL_BATTERY_RELEASE_GUARD=FAIL: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
