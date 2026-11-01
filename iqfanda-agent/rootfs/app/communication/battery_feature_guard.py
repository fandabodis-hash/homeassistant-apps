"""Local integrity check for battery START/renewal; never put it before STOP.

The release manifest detects accidental replacement or partial updates. It is
not a signature: an administrator who changes both code and manifest can change
the accepted feature. An optional, persistent physical-verification pin makes
that choice explicit across add-on upgrades. This module only reads files.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


APP_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = APP_ROOT / "protection/manual_battery_manifest.json"
PIN_PATH = Path("/config/manual_battery_verified_fingerprint.json")
REQUIRED_ROOTFS_FILES = {"etc/services.d/iqfanda/run"}
PROFILE_IDS = {
    "goodwe_et", "goodwe_et_15_30kw", "foxess_h3_10_0_e",
    "solax_x3_hybrid_g4", "deye_3x_lv_off_grid", "deye_50_hybrid_hv",
}
REQUIRED_FILES = {
    "VERSION", "manual_battery_runtime.py", "spot_battery_intent.py", "battery_control_canary.py",
    "foxess_zero_remote_release.py",
    "command_worker.py", "startup.py", "heartbeat.py", "device_config.py",
    "communication/battery_feature_guard.py",
    "communication/battery_control_safety.py",
    "communication/inverter_control_adapter.py",
    "communication/inverter_adapter.py", "communication/json_utils.py",
    "communication/goodwe_probe.py", "communication/inverter_profiles/catalog.json",
    "communication/foxess_ems_controller.py",
    "communication/foxess_remote_control_probe.py",
    "communication/goodwe_ems_controller.py",
    "communication/solax_ems_controller.py",
    "communication/modbus_bus_lock.py",
} | {"communication/inverter_profiles/" + key + ".json" for key in PROFILE_IDS}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Battery feature integrity: " + message)


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("Battery feature integrity: cannot read " + str(path)) from exc
    _require(isinstance(value, dict), "invalid document " + str(path))
    return value


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def _file(root: Path, relative: str) -> Path:
    _require(isinstance(relative, str) and bool(relative) and "\\" not in relative,
             "invalid protected path")
    _require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
             "protected path escapes app")
    path = root / relative
    _require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve()),
             "protected path is a symlink or escapes app")
    _require(path.is_file(), "missing file " + relative)
    return path


def verify_battery_feature(profile_id: str | None = None) -> str:
    """Return the current verified runtime fingerprint, or raise RuntimeError.

    No physical success is inferred. An absent/pending pin permits candidate
    tests when all source bytes match. A physically verified pin requires its
    exact runtime. Callers must still be able to attempt safe STOP after failure.
    """
    manifest = _load(MANIFEST_PATH)
    _require(manifest.get("schema_version") == 1
             and manifest.get("feature") == "local_manual_battery", "invalid manifest schema")
    files = manifest.get("files")
    _require(isinstance(files, dict) and REQUIRED_FILES <= set(files),
             "manifest omits a required production file")
    try:
        version = _file(APP_ROOT, "VERSION").read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("Battery feature integrity: cannot read VERSION") from exc
    _require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is not None, "invalid VERSION")
    _require(manifest.get("agent_version") == version, "VERSION differs from manifest")
    for relative, expected in files.items():
        _require(relative != "protection/manual_battery_manifest.json", "manifest cannot hash itself")
        _require(isinstance(expected, str) and HEX64.fullmatch(expected) is not None,
                 "invalid hash for " + str(relative))
        try:
            actual = hashlib.sha256(_file(APP_ROOT, relative).read_bytes()).hexdigest()
        except OSError as exc:
            raise RuntimeError("Battery feature integrity: cannot read " + relative) from exc
        _require(actual == expected, "hash differs for " + relative)
    rootfs_files = manifest.get("rootfs_files")
    _require(isinstance(rootfs_files, dict) and set(rootfs_files) == REQUIRED_ROOTFS_FILES,
             "manifest omits or adds an unexpected rootfs integration file")
    for relative, expected in rootfs_files.items():
        _require(isinstance(expected, str) and HEX64.fullmatch(expected) is not None,
                 "invalid rootfs hash for " + str(relative))
        try:
            actual = hashlib.sha256(_file(APP_ROOT.parent, relative).read_bytes()).hexdigest()
        except OSError as exc:
            raise RuntimeError("Battery feature integrity: cannot read rootfs " + relative) from exc
        _require(actual == expected, "hash differs for rootfs " + relative)
    # A profile must not point to a driver outside the protected source set.
    for protected_profile_id in PROFILE_IDS:
        profile = _load(APP_ROOT / ("communication/inverter_profiles/" + protected_profile_id + ".json"))
        capabilities = profile.get("control_capabilities", {})
        _require(isinstance(capabilities, dict), "invalid profile capabilities")
        capability = capabilities.get("battery_charge_control")
        if capability is None:
            continue
        _require(isinstance(capability, dict), "invalid battery capability")
        unavailable = (capability.get("supported_actions") == []
                       and capability.get("discharge_allowed") is False
                       and isinstance(capability.get("unsupported_reason"), str)
                       and bool(capability["unsupported_reason"].strip()))
        if unavailable:
            continue
        module = capability.get("driver_module", "")
        _require(isinstance(module, str) and module.startswith("communication.")
                 and all(part.isidentifier() for part in module.split(".")), "invalid driver module")
        _require(module.replace(".", "/") + ".py" in files, "declared driver is not protected")
    build_inputs = manifest.get("build_inputs")
    _require(isinstance(build_inputs, dict) and {"Dockerfile", "config.yaml"} <= set(build_inputs),
             "manifest omits build inputs")
    _require(all(isinstance(value, str) and HEX64.fullmatch(value) is not None
                 for value in build_inputs.values()), "invalid build input hashes")
    fingerprint = hashlib.sha256(_canonical({
        "agent_version": version, "files": files, "rootfs_files": rootfs_files,
        "build_inputs": build_inputs,
    })).hexdigest()
    _require(manifest.get("runtime_fingerprint") == fingerprint, "runtime fingerprint differs")
    _require(profile_id is None or (isinstance(profile_id, str) and profile_id in PROFILE_IDS),
             "unknown selected profile")
    if profile_id is not None and (PIN_PATH.exists() or PIN_PATH.is_symlink()):
        _require(not PIN_PATH.is_symlink(), "physical pin must be a regular file")
        registry = _load(PIN_PATH)
        _require(registry.get("schema_version") == 1, "invalid physical pin schema")
        profiles = registry.get("profiles")
        _require(isinstance(profiles, dict), "invalid physical pin profile map")
        pin = profiles.get(profile_id, {"status": "pending"})
        _require(isinstance(pin, dict), "invalid selected profile pin")
        status = pin.get("status")
        _require(isinstance(status, str) and status in {"pending", "physically_verified"},
                 "invalid physical pin status")
        if status == "physically_verified":
            _require(isinstance(pin.get("evidence_id"), str) and bool(pin["evidence_id"].strip()),
                     "physical pin has no evidence reference")
            _require(isinstance(pin.get("report_sha256"), str)
                     and HEX64.fullmatch(pin["report_sha256"]) is not None, "physical pin has no report hash")
            _require(pin.get("runtime_fingerprint") == fingerprint
                     and pin.get("agent_version") == version,
                     "runtime differs from the physically verified pin; reviewed revalidation is required")
    return fingerprint
