#!/usr/bin/env python3
"""Read-only update metadata, run inside the selected Home Assistant add-on.

This script does not provide a VPS tunnel or a new command-worker capability.
It never returns options, environment variables, authorization headers or tokens.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from urllib import error, parse, request


EXPECTED_REPOSITORY = "github.com/fandabodis-hash/homeassistant-apps"
INFO_FIELDS = ("name", "slug", "repository", "version", "version_latest",
               "update_available", "auto_update", "build", "state", "detached", "available")
STATE_FIELDS = ("state", "action", "manual", "profile_id", "expires_at", "verified")
ACTIVE_STATES = {"preparing", "active", "stop_pending"}
KNOWN_STATES = ACTIVE_STATES | {"stopped"}


class PreflightError(RuntimeError):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def supervisor_get(path: str, token: str) -> dict:
    if not path.startswith("/") or "://" in path:
        raise PreflightError("Invalid local Supervisor endpoint")
    query = request.Request("http://supervisor" + path, method="GET", headers={
        "Authorization": "Bearer " + token, "Accept": "application/json",
    })
    try:
        with request.build_opener(request.ProxyHandler({}), NoRedirect()).open(query, timeout=15) as response:
            value = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    except error.HTTPError as exc:
        raise PreflightError("Supervisor returned HTTP " + str(exc.code)) from None
    except (error.URLError, OSError, UnicodeError, ValueError):
        raise PreflightError("Supervisor read failed; no request body or token is logged") from None
    if not isinstance(value, dict) or value.get("result") != "ok" or not isinstance(value.get("data"), dict):
        raise PreflightError("Supervisor returned an unexpected response schema")
    return value["data"]


def repository_summary(value: dict) -> dict:
    source = str(value.get("source") or value.get("url") or "")
    parsed = parse.urlsplit(source)
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    matches = ((parsed.hostname or "").lower() + path).lower() == EXPECTED_REPOSITORY.lower()
    branch = parsed.fragment or None
    if branch is not None and not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", branch):
        branch = "<nonstandard branch omitted>"
    return {"expected_repository": EXPECTED_REPOSITORY, "matches_expected_repository": matches,
            "source_host": parsed.hostname, "configured_branch": branch,
            "uses_default_branch": not bool(parsed.fragment),
            "source_credentials_present": bool(parsed.username or parsed.password or parsed.query)}


def read_optional_json(path: Path):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return {"read_error": True}
    return value if isinstance(value, dict) else {"read_error": True}


def collect_preflight(get, *, config_root=Path("/config"), app_root=Path("/app")) -> dict:
    info = get("/addons/self/info")
    addon = {key: info[key] for key in INFO_FIELDS if key in info}
    repository = str(info.get("repository") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", repository):
        raise PreflightError("Supervisor did not return a usable repository slug")
    source = repository_summary(get("/store/repositories/" + parse.quote(repository, safe="")))
    state = read_optional_json(config_root / "manual_battery_runtime.json")
    pin = read_optional_json(config_root / "manual_battery_verified_fingerprint.json")
    manifest = read_optional_json(app_root / "protection/manual_battery_manifest.json")
    state_error = state is not None and (state.get("read_error") or state.get("schema_version") != 1
                                         or state.get("state") not in KNOWN_STATES)
    active = None if state is None or state_error else state["state"] in ACTIVE_STATES
    pin_profiles = pin.get("profiles") if isinstance(pin, dict) else None
    return {
        "read_only": True, "requests": ["GET /addons/self/info", "GET /store/repositories/<self-repository>"],
        "scope": "Only the add-on and Home Assistant installation running this script",
        "addon": addon, "repository": source,
        "local_battery_state": {key: state[key] for key in STATE_FIELDS if key in state} if state else None,
        "battery_state_read_error": bool(state_error),
        "active_or_pending_stop": active,
        "declared_runtime_fingerprint": manifest.get("runtime_fingerprint") if manifest else None,
        "physical_pins": {profile: {key: entry.get(key) for key in ("status", "runtime_fingerprint", "agent_version")}
                          for profile, entry in pin_profiles.items() if isinstance(entry, dict)}
                         if isinstance(pin_profiles, dict) else None,
        "physical_pin_read_error": bool(pin and (pin.get("read_error") or not isinstance(pin_profiles, dict))),
        "fleet_auto_update_known": False, "update_requested": False,
    }


def main() -> int:
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token:
        print("PREFLIGHT=FAIL: Run inside the selected Home Assistant add-on; SUPERVISOR_TOKEN is unavailable.", file=sys.stderr)
        return 2
    try:
        result = collect_preflight(lambda path: supervisor_get(path, token))
    except (PreflightError, ValueError, TypeError):
        print("PREFLIGHT=FAIL: Local Supervisor metadata could not be read safely.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
