"""Fanda-native Wi-Fi infrastructure onboarding helpers.

This module is independent from Home Assistant device/entity registries. It lets
TNG IQ FANDA Agent discover local Wi-Fi/LAN candidates while the installer stays
inside Fanda.

R139 scope:
- no Wi-Fi credential write
- no AP switching
- no Home Assistant UI dependency
- no Home Assistant integration creation
- no device control
- safe read-only discovery only
- hardened Wi-Fi AP parsing and physical-LAN filtering
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from typing import Any
from urllib import error, request


HTTP_TIMEOUT_SECONDS = float(os.getenv("IQF_WIFI_NATIVE_HTTP_TIMEOUT_SECONDS", "0.8"))
SSDP_TIMEOUT_SECONDS = float(os.getenv("IQF_WIFI_NATIVE_SSDP_TIMEOUT_SECONDS", "1.2"))
MAX_LAN_PROBE_CANDIDATES = int(os.getenv("IQF_WIFI_NATIVE_MAX_LAN_PROBE_CANDIDATES", "48"))

KNOWN_WIFI_AP_PREFIXES = (
    "shelly",
    "tasmota",
    "sonoff",
    "ewelink",
    "esp_",
    "esphome",
    "esp-",
    "smartlife",
    "tuya",
)

INTERNAL_INTERFACES = (
    "docker",
    "br-",
    "veth",
    "hassio",
    "lo",
)

PHYSICAL_INTERFACE_PREFIXES = (
    "wlan",
    "wl",
    "eth",
    "en",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _run_command(args: list[str], timeout: float = 3.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "missing": True,
            "args": args,
            "returncode": None,
            "stdout": "",
            "stderr": "command_not_found",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timeout": True,
            "args": args,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "timeout",
        }

    return {
        "ok": completed.returncode == 0,
        "missing": False,
        "args": args,
        "returncode": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
    }


def _split_nmcli_terse_line(line: str) -> list[str]:
    """Split nmcli terse output while respecting backslash escaped separators."""

    values: list[str] = []
    current: list[str] = []
    escaped = False

    for char in line:
        if escaped:
            current.append(char)
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == ":":
            values.append("".join(current))
            current = []
            continue

        current.append(char)

    if escaped:
        current.append("\\")

    values.append("".join(current))
    return values


def _parse_nmcli_wifi_list(output: str, fields: list[str]) -> list[dict[str, Any]]:
    access_points: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    normalized_fields = [field.lower() for field in fields]

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = _split_nmcli_terse_line(line)

        row: dict[str, str] = {}
        for index, field in enumerate(normalized_fields):
            row[field] = parts[index].strip() if index < len(parts) else ""

        ssid = row.get("ssid", "")
        bssid = row.get("bssid", "")
        signal = None
        security = row.get("security", "")
        channel = row.get("chan", "") or row.get("channel", "")

        signal_raw = row.get("signal", "")
        if signal_raw:
            try:
                signal = int(signal_raw)
            except ValueError:
                signal = None

        key = (ssid, bssid, channel)
        if key in seen:
            continue
        seen.add(key)

        lower_ssid = ssid.lower()
        setup_candidate = any(lower_ssid.startswith(prefix) for prefix in KNOWN_WIFI_AP_PREFIXES)

        profile_hint = None
        if lower_ssid.startswith("shelly"):
            profile_hint = "shelly_access_point"
        elif lower_ssid.startswith(("tasmota", "sonoff", "ewelink")):
            profile_hint = "tasmota_or_sonoff_access_point"
        elif lower_ssid.startswith(("esp_", "esp-", "esphome")):
            profile_hint = "esp_access_point"
        elif lower_ssid.startswith(("smartlife", "tuya")):
            profile_hint = "tuya_access_point"

        access_points.append(
            {
                "ssid": ssid,
                "bssid": bssid,
                "signal_percent": signal,
                "security": security,
                "channel": channel,
                "setup_candidate": setup_candidate,
                "profile_hint": profile_hint,
            }
        )

    access_points.sort(
        key=lambda item: (
            not bool(item.get("setup_candidate")),
            -int(item.get("signal_percent") or 0),
            str(item.get("ssid") or ""),
        )
    )
    return access_points


def scan_wifi_access_points() -> dict[str, Any]:
    """Read nearby Wi-Fi access points without connecting to them."""

    attempts: list[dict[str, Any]] = []
    commands = [
        {
            "fields": ["SSID", "SIGNAL", "SECURITY", "CHAN"],
            "args": [
                "nmcli",
                "-t",
                "-e",
                "no",
                "-f",
                "SSID,SIGNAL,SECURITY,CHAN",
                "dev",
                "wifi",
                "list",
                "--rescan",
                "yes",
            ],
        },
        {
            "fields": ["SSID", "BSSID", "SIGNAL", "SECURITY", "CHAN"],
            "args": [
                "nmcli",
                "-t",
                "-e",
                "yes",
                "-f",
                "SSID,BSSID,SIGNAL,SECURITY,CHAN",
                "dev",
                "wifi",
                "list",
            ],
        },
        {
            "fields": ["SSID", "SIGNAL", "SECURITY", "CHAN"],
            "args": [
                "nmcli",
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY,CHAN",
                "device",
                "wifi",
                "list",
            ],
        },
    ]

    for command_spec in commands:
        result = _run_command(command_spec["args"], timeout=12.0)
        attempts.append(
            {
                "args": command_spec["args"],
                "fields": command_spec["fields"],
                "ok": result.get("ok"),
                "returncode": result.get("returncode"),
                "stderr": _as_text(result.get("stderr"))[:500],
            }
        )
        if result.get("ok"):
            return {
                "ok": True,
                "source": "nmcli",
                "parse_version": "phase28_r139_nmcli_fields_without_required_bssid",
                "access_points": _parse_nmcli_wifi_list(
                    str(result.get("stdout") or ""),
                    command_spec["fields"],
                ),
                "attempts": attempts,
            }

    return {
        "ok": False,
        "source": None,
        "parse_version": "phase28_r139_nmcli_fields_without_required_bssid",
        "access_points": [],
        "attempts": attempts,
    }


def _parse_proc_net_arp() -> list[dict[str, Any]]:
    path = "/proc/net/arp"
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []

    neighbors: list[dict[str, Any]] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue

        ip_address, hw_type, flags, mac_address, mask, device = parts[:6]
        if mac_address == "00:00:00:00:00:00":
            continue

        neighbors.append(
            {
                "ip_address": ip_address,
                "mac_address": mac_address,
                "interface": device,
                "source": "proc_net_arp",
                "flags": flags,
                "hw_type": hw_type,
                "mask": mask,
            }
        )

    return neighbors


def _parse_ip_neigh(output: str) -> list[dict[str, Any]]:
    neighbors: list[dict[str, Any]] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split()
        ip_address = parts[0]
        interface = ""
        mac_address = ""
        state = parts[-1] if parts else ""

        if "dev" in parts:
            index = parts.index("dev")
            if index + 1 < len(parts):
                interface = parts[index + 1]

        if "lladdr" in parts:
            index = parts.index("lladdr")
            if index + 1 < len(parts):
                mac_address = parts[index + 1]

        if not mac_address or mac_address == "00:00:00:00:00:00":
            continue

        neighbors.append(
            {
                "ip_address": ip_address,
                "mac_address": mac_address,
                "interface": interface,
                "state": state,
                "source": "ip_neigh",
            }
        )

    return neighbors


def _is_physical_network_neighbor(item: dict[str, Any]) -> bool:
    interface = _as_text(item.get("interface")).lower()
    ip_address = _as_text(item.get("ip_address"))

    if not interface:
        return False

    if interface.startswith(INTERNAL_INTERFACES):
        return False

    if not interface.startswith(PHYSICAL_INTERFACE_PREFIXES):
        return False

    try:
        parsed = ipaddress.ip_address(ip_address)
    except ValueError:
        return False

    if parsed.version != 4:
        return False

    if parsed.is_loopback or parsed.is_link_local or parsed.is_multicast:
        return False

    return True


def discover_lan_neighbors() -> dict[str, Any]:
    """Read local neighbor table without scanning the whole subnet."""

    combined: dict[str, dict[str, Any]] = {}

    for item in _parse_proc_net_arp():
        combined[item["ip_address"]] = item

    result = _run_command(["ip", "neigh", "show"], timeout=3.0)

    if result.get("ok"):
        for item in _parse_ip_neigh(str(result.get("stdout") or "")):
            existing = combined.get(item["ip_address"], {})
            sources = sorted(set(filter(None, [existing.get("source"), item.get("source")])))
            combined[item["ip_address"]] = {
                **existing,
                **item,
                "source": ",".join(sources),
            }

    neighbors = list(combined.values())
    neighbors.sort(key=lambda item: (str(item.get("interface") or ""), str(item.get("ip_address") or "")))

    physical_neighbors = [
        item
        for item in neighbors
        if _is_physical_network_neighbor(item)
    ]

    return {
        "neighbors": neighbors,
        "physical_neighbors": physical_neighbors,
        "physical_neighbor_count": len(physical_neighbors),
        "ip_neigh_available": bool(result.get("ok")),
        "ip_neigh_error": _as_text(result.get("stderr"))[:500],
    }


def _http_get_json_or_text(url: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> dict[str, Any]:
    try:
        req = request.Request(
            url,
            headers={
                "User-Agent": "TNG-IQ-FANDA-WiFi-Native-Discovery",
                "Accept": "application/json,text/plain,*/*",
            },
            method="GET",
        )
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read(65536)
            content_type = response.headers.get("content-type", "")
            text = body.decode("utf-8", errors="replace")
            parsed = None
            if "json" in content_type.lower() or text.strip().startswith(("{", "[")):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None

            return {
                "ok": True,
                "status": getattr(response, "status", None),
                "content_type": content_type,
                "json": parsed,
                "text": text[:4000],
            }
    except error.HTTPError as exc:
        return {
            "ok": False,
            "http_status": exc.code,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


def _looks_like_shelly_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False

    text = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    keys = {str(key).lower() for key in payload.keys()}

    if "shelly" in text:
        return True

    if {"id", "mac", "model"} & keys and {"gen", "app", "fw", "ver"} & keys:
        return True

    return False


def _normalize_shelly_device(
    *,
    ip_address: str,
    neighbor: dict[str, Any],
    profile: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    name = payload.get("name") or payload.get("id") or payload.get("model") or f"Shelly {ip_address}"
    model = payload.get("model") or payload.get("app") or payload.get("type") or ""
    mac = payload.get("mac") or neighbor.get("mac_address") or ""

    return {
        "device_id": f"wifi-native-{profile}-{ip_address}",
        "transport": "wifi",
        "source": "fanda_native_lan_probe",
        "profile": profile,
        "name": _as_text(name),
        "manufacturer": "Shelly",
        "model": _as_text(model),
        "ip_address": ip_address,
        "mac_address": _as_text(mac),
        "classification_reasons": ["fanda_native_lan_probe", profile],
        "entity_count": 0,
        "control_entity_count": 0,
        "entities": [],
        "raw_info": payload,
        "onboarding": {
            "status": "discovered_not_paired",
            "requires_profile": "shelly",
            "requires_ha_ui": False,
        },
    }


def probe_known_lan_devices(neighbors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Probe only known physical neighbors. No subnet brute force."""

    discovered: list[dict[str, Any]] = []
    seen_ips: set[str] = set()

    physical_neighbors = [
        neighbor
        for neighbor in neighbors
        if _is_physical_network_neighbor(neighbor)
    ]

    for neighbor in physical_neighbors[:MAX_LAN_PROBE_CANDIDATES]:
        ip_address = _as_text(neighbor.get("ip_address"))
        if not ip_address or ip_address in seen_ips:
            continue
        seen_ips.add(ip_address)

        gen2 = _http_get_json_or_text(f"http://{ip_address}/rpc/Shelly.GetDeviceInfo")
        if gen2.get("ok") and _looks_like_shelly_payload(gen2.get("json")):
            discovered.append(
                _normalize_shelly_device(
                    ip_address=ip_address,
                    neighbor=neighbor,
                    profile="shelly_gen2_rpc",
                    payload=gen2["json"],
                )
            )
            continue

        gen1 = _http_get_json_or_text(f"http://{ip_address}/shelly")
        if gen1.get("ok") and _looks_like_shelly_payload(gen1.get("json")):
            discovered.append(
                _normalize_shelly_device(
                    ip_address=ip_address,
                    neighbor=neighbor,
                    profile="shelly_gen1_rest",
                    payload=gen1["json"],
                )
            )
            continue

    return discovered


def ssdp_discovery() -> dict[str, Any]:
    """Small SSDP M-SEARCH discovery. It is read-only broadcast traffic."""

    message = "\r\n".join(
        [
            "M-SEARCH * HTTP/1.1",
            "HOST: 239.255.255.250:1900",
            'MAN: "ssdp:discover"',
            "MX: 1",
            "ST: ssdp:all",
            "",
            "",
        ]
    ).encode("ascii")

    responses: list[dict[str, Any]] = []
    sock = None
    started = time.monotonic()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(SSDP_TIMEOUT_SECONDS)
        sock.sendto(message, ("239.255.255.250", 1900))

        while time.monotonic() - started <= SSDP_TIMEOUT_SECONDS:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break

            text = data.decode("utf-8", errors="replace")
            headers: dict[str, str] = {}
            for line in text.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()

            responses.append(
                {
                    "ip_address": addr[0],
                    "port": addr[1],
                    "server": headers.get("server"),
                    "location": headers.get("location"),
                    "st": headers.get("st"),
                    "usn": headers.get("usn"),
                }
            )
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "responses": responses,
        }
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    return {
        "ok": True,
        "responses": responses,
    }


def discover_native_wifi_infrastructure(payload=None) -> dict[str, Any]:

    # PHASE28_R153_FIX2_SHELLY_AP_ONBOARDING_ACTION_DISPATCH_START
    phase28_r153_payload = payload or {}

    # PHASE28_R177G1_R3_WIFI_ACTION_DISPATCH_START
    phase28_r177g1_requested_action = str(
        phase28_r153_payload.get(
            "requested_action",
            "",
        )
    ).strip() if isinstance(
        phase28_r153_payload,
        dict,
    ) else ""

    if phase28_r177g1_requested_action == "register_wifi_device":
        return _phase28_r177g1_register_wifi_device(
            phase28_r153_payload,
        )

    if phase28_r177g1_requested_action == "set_wifi_switch_state":
        return _phase28_r177g1_set_wifi_switch_state(
            phase28_r153_payload,
        )
    # PHASE28_R177G1_R3_WIFI_ACTION_DISPATCH_END


    if isinstance(
        phase28_r153_payload,
        dict,
    ) and str(
        phase28_r153_payload.get(
            "requested_action",
            "",
        )
    ) in {
        "shelly_ap_onboard",
        "onboard_shelly_access_point",
        "shelly_access_point_onboard",
    }:
        return onboard_shelly_access_point(
            phase28_r153_payload,
        )
    # PHASE28_R153_FIX2_SHELLY_AP_ONBOARDING_ACTION_DISPATCH_END
    """Fanda-native read-only Wi-Fi/LAN discovery."""

    wifi_scan = scan_wifi_access_points()
    lan = discover_lan_neighbors()
    ssdp = ssdp_discovery()

    neighbors = lan.get("neighbors")
    if not isinstance(neighbors, list):
        neighbors = []

    physical_neighbors = lan.get("physical_neighbors")
    if not isinstance(physical_neighbors, list):
        physical_neighbors = []

    access_points = wifi_scan.get("access_points")
    if not isinstance(access_points, list):
        access_points = []

    lan_devices = probe_known_lan_devices(neighbors)

    ap_candidates = [
        ap
        for ap in access_points
        if isinstance(ap, dict) and ap.get("setup_candidate")
    ]

    onboarding_profiles = [
        {
            "profile": "shelly_local_lan",
            "name": "Shelly local LAN",
            "requires_ha_ui": False,
            "status": "discovery_supported",
            "runtime_action": "read_only_probe",
            "next_step": "credential_onboarding_profile_not_enabled_in_r139",
        },
        {
            "profile": "shelly_access_point",
            "name": "Shelly AP onboarding",
            "requires_ha_ui": False,
            "status": "candidate_detection_supported",
            "runtime_action": "read_only_ap_scan",
            "next_step": "temporary_ap_connect_not_enabled_in_r139",
        },
        {
            "profile": "esphome",
            "name": "ESPHome native",
            "requires_ha_ui": False,
            "status": "planned",
            "runtime_action": None,
            "next_step": "profile_specific_implementation",
        },
        {
            "profile": "tasmota_mqtt",
            "name": "Tasmota MQTT",
            "requires_ha_ui": False,
            "status": "planned",
            "runtime_action": None,
            "next_step": "mqtt_profile_specific_implementation",
        },
        {
            "profile": "matter",
            "name": "Matter",
            "requires_ha_ui": False,
            "status": "planned",
            "runtime_action": None,
            "next_step": "commissioning_profile_specific_implementation",
        },
    ]


    # PHASE28_R177G1_R3_WIFI_DISCOVERY_ENRICH_START
    lan_devices = _phase28_r177g1_enrich_wifi_devices(
        lan_devices,
    )
    # PHASE28_R177G1_R3_WIFI_DISCOVERY_ENRICH_END

    return {
        "worker": "command_worker",
        "executor": "wifi_native_onboarding",
        "phase": "wifi_native_discovery_completed",
        "phase28_r135_native_wifi_onboarding": True,
        "phase28_r139_native_wifi_scan_hardened": True,
        "infrastructure_mode": True,
        "requires_home_assistant_ui": False,
        "credential_write": False,
        "wifi_runtime_change": False,
        "transport": "wifi",
        "device_count": len(lan_devices),
        "devices": lan_devices,
        "wifi_scan": {
            "ok": wifi_scan.get("ok"),
            "source": wifi_scan.get("source"),
            "parse_version": wifi_scan.get("parse_version"),
            "attempts": wifi_scan.get("attempts"),
        },
        "wifi_access_point_count": len(access_points),
        "wifi_access_points": access_points[:40],
        "wifi_setup_candidate_count": len(ap_candidates),
        "wifi_setup_candidates": ap_candidates[:20],
        "lan_neighbor_count": len(neighbors),
        "lan_neighbors": neighbors[:MAX_LAN_PROBE_CANDIDATES],
        "lan_physical_neighbor_count": len(physical_neighbors),
        "lan_physical_neighbors": physical_neighbors[:MAX_LAN_PROBE_CANDIDATES],
        "ssdp": ssdp,
        "onboarding_profiles": onboarding_profiles,
        "supported_native_profiles": [
            profile["profile"]
            for profile in onboarding_profiles
            if profile["status"] in {"discovery_supported", "candidate_detection_supported"}
        ],
        "planned_native_profiles": [
            profile["profile"]
            for profile in onboarding_profiles
            if profile["status"] == "planned"
        ],
        "discovered_at": _utc_now_iso(),
    }


# PHASE28_R153_FIX2_SHELLY_AP_ONBOARDING_BASE_START
def _phase28_r153_redact_secret(value):
    if value is None:
        return None
    text = str(value)
    if not text:
        return ""
    return "***REDACTED***"


def _phase28_r153_run_command(args, timeout=30):
    import subprocess
    from datetime import datetime, timezone

    started_at = datetime.now(timezone.utc).isoformat()
    completed = subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )

    return {
        "args": [
            "***REDACTED***" if "password" in str(item).lower() else str(item)
            for item in args
        ],
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip()[-2000:],
        "stderr": (completed.stderr or "").strip()[-2000:],
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def _phase28_r153_parse_nmcli_terse_line(line):
    parts = []
    current = []
    escaped = False

    for char in str(line):
        if escaped:
            current.append(char)
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == ":":
            parts.append("".join(current))
            current = []
            continue

        current.append(char)

    parts.append("".join(current))
    return parts


def _phase28_r153_get_active_wifi_connection(preferred_ifname=None):
    result = _phase28_r153_run_command(
        [
            "nmcli",
            "-t",
            "-f",
            "NAME,UUID,TYPE,DEVICE",
            "connection",
            "show",
            "--active",
        ],
        timeout=20,
    )

    connections = []
    for line in result.get("stdout", "").splitlines():
        parts = _phase28_r153_parse_nmcli_terse_line(line)
        while len(parts) < 4:
            parts.append("")
        connections.append(
            {
                "name": parts[0],
                "uuid": parts[1],
                "type": parts[2],
                "device": parts[3],
            }
        )

    wifi = [
        item
        for item in connections
        if item.get("type") in {"802-11-wireless", "wifi"}
    ]

    if preferred_ifname:
        for item in wifi:
            if item.get("device") == preferred_ifname:
                return item, connections, result

    if wifi:
        return wifi[0], connections, result

    return None, connections, result


def _phase28_r153_shelly_rpc(host, method, params=None, timeout=8):
    import json
    import urllib.request

    body = {
        "id": 1,
        "method": method,
    }

    if params is not None:
        body["params"] = params

    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        "http://" + str(host).strip() + "/rpc",
        data=data,
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(response_body)
            except Exception:
                parsed = {
                    "raw": response_body,
                }
            return {
                "ok": True,
                "method": method,
                "http_status": getattr(response, "status", None),
                "response": parsed,
            }
    except Exception as exc:
        return {
            "ok": False,
            "method": method,
            "error": str(exc),
        }


def _phase28_r153_wait_for_shelly_rpc(host, timeout_seconds=45):
    import time

    deadline = time.monotonic() + timeout_seconds
    attempts = []

    while time.monotonic() < deadline:
        result = _phase28_r153_shelly_rpc(
            host,
            "Shelly.GetDeviceInfo",
            {
                "ident": True,
            },
            timeout=6,
        )
        attempts.append(result)

        if result.get("ok"):
            return {
                "ok": True,
                "host": host,
                "result": result,
                "attempt_count": len(attempts),
            }

        time.sleep(3)

    return {
        "ok": False,
        "host": host,
        "attempt_count": len(attempts),
        "attempts": attempts[-5:],
    }


def _phase28_r153_start_wifi_rollback_watchdog(
    *,
    marker_path,
    original_connection_uuid,
    rollback_timeout_seconds,
):
    import shlex
    import subprocess
    from datetime import datetime, timezone
    from pathlib import Path

    if not original_connection_uuid:
        return {
            "started": False,
            "reason": "missing_original_connection_uuid",
        }

    marker = str(marker_path)
    Path(marker).write_text(
        datetime.now(timezone.utc).isoformat(),
        encoding="utf-8",
    )

    command = (
        "sleep "
        + shlex.quote(str(int(rollback_timeout_seconds)))
        + "; "
        + "if [ -f "
        + shlex.quote(marker)
        + " ]; then "
        + "nmcli connection up uuid "
        + shlex.quote(str(original_connection_uuid))
        + " >/tmp/iqfanda-r153-wifi-rollback.log 2>&1; "
        + "fi"
    )

    subprocess.Popen(
        [
            "sh",
            "-c",
            command,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )

    return {
        "started": True,
        "marker_path": marker,
        "rollback_timeout_seconds": rollback_timeout_seconds,
    }


def _phase28_r153_remove_file(path):
    from pathlib import Path

    try:
        Path(path).unlink(missing_ok=True)
        return True
    except Exception:
        return False


# PHASE28_R173_SHELLY_RESULT_SECRET_REDACTION_START
def _phase28_r173_redact_sensitive_result(value):
    """Redact sensitive fields before command results are persisted."""

    sensitive_names = {
        "key",
        "password",
        "wifi_password",
        "target_wifi_password",
        "pass",
        "passwd",
        "psk",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "authorization",
    }

    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lower_key = str(key).lower()
            if (
                lower_key in sensitive_names
                or "password" in lower_key
                or "secret" in lower_key
                or lower_key.endswith("_token")
                or lower_key == "key"
                or lower_key.endswith("_key")
            ):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _phase28_r173_redact_sensitive_result(item)
        return redacted

    if isinstance(value, list):
        return [
            _phase28_r173_redact_sensitive_result(item)
            for item in value
        ]

    return value
# PHASE28_R173_SHELLY_RESULT_SECRET_REDACTION_END

def onboard_shelly_access_point(payload=None):
    import time

    payload = payload or {}

    if not isinstance(payload, dict):
        raise RuntimeError("Shelly onboarding payload must be an object.")

    ap_ssid = str(
        payload.get("shelly_ap_ssid")
        or payload.get("ap_ssid")
        or payload.get("ssid")
        or ""
    ).strip()

    target_wifi_ssid = str(
        payload.get("target_wifi_ssid")
        or payload.get("wifi_ssid")
        or ""
    ).strip()

    target_wifi_password = str(
        payload.get("target_wifi_password")
        or payload.get("wifi_password")
        or ""
    )

    preferred_ifname = str(
        payload.get("interface")
        or payload.get("ifname")
        or "wlan0"
    ).strip()

    shelly_host = str(
        payload.get("shelly_host")
        or "192.168.33.1"
    ).strip()

    dry_run = bool(
        payload.get(
            "dry_run",
            True,
        )
    )

    rollback_timeout_seconds = int(
        payload.get(
            "rollback_timeout_seconds",
            180,
        )
        or 180
    )

    reboot_after_config = bool(
        payload.get(
            "reboot_after_config",
            True,
        )
    )

    if not ap_ssid:
        raise RuntimeError("Shelly AP SSID is required.")

    if not target_wifi_ssid:
        raise RuntimeError("Target WiFi SSID is required.")

    if dry_run:
        return {
            "phase": "shelly_ap_onboarding_dry_run",
            "executor": "wifi_native_onboarding",
            "phase28_r153_shelly_ap_onboarding_base": True,
            "phase28_r153_fix2": True,
            "requires_home_assistant_ui": False,
            "credential_write": False,
            "wifi_runtime_change": False,
            "shelly_ap_ssid": ap_ssid,
            "target_wifi_ssid": target_wifi_ssid,
            "password_redacted": True,
            "next_step": "rerun_with_dry_run_false_after_local_password_prompt",
        }

    if not target_wifi_password:
        raise RuntimeError(
            "Target WiFi password is required for non-dry-run onboarding."
        )

    marker_path = (
        "/tmp/iqfanda-r153-shelly-onboarding-"
        + str(int(time.time()))
        + ".rollback"
    )

    original_connection = None
    active_connections = []
    preflight = {}
    rollback_watchdog = {}
    shelly_info = None
    set_config_result = None
    reboot_result = None
    reconnect_result = None
    ap_connection_result = None
    rescan_result = None
    rollback_marker_removed = False

    try:
        original_connection, active_connections, preflight = (
            _phase28_r153_get_active_wifi_connection(
                preferred_ifname=preferred_ifname,
            )
        )

        if original_connection is None:
            raise RuntimeError(
                "No active WiFi connection found before Shelly onboarding."
            )

        if not original_connection.get("uuid"):
            raise RuntimeError("Active WiFi connection UUID is missing.")

        rollback_watchdog = _phase28_r153_start_wifi_rollback_watchdog(
            marker_path=marker_path,
            original_connection_uuid=original_connection.get("uuid"),
            rollback_timeout_seconds=rollback_timeout_seconds,
        )

        # PHASE28_R170_SHELLY_AP_CONNECT_DIAGNOSTICS_START
        def phase28_r170_compact_command_result(value):
            if not isinstance(value, dict):
                return {
                    "value": str(value)[:1000],
                }

            safe = {}
            for key in (
                "returncode",
                "stdout",
                "stderr",
                "command",
                "timeout",
                "ok",
            ):
                if key in value:
                    item = value.get(key)
                    if isinstance(item, str) and len(item) > 1800:
                        item = item[:1800] + "...TRUNCATED"
                    safe[key] = item
            return safe

        wifi_list_before_result = _phase28_r153_run_command(
            [
                "nmcli",
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY,CHAN",
                "device",
                "wifi",
                "list",
                "ifname",
                preferred_ifname,
            ],
            timeout=25,
        )

        stale_ap_delete_result = _phase28_r153_run_command(
            [
                "nmcli",
                "connection",
                "delete",
                "id",
                ap_ssid,
            ],
            timeout=20,
        )

        rescan_result = _phase28_r153_run_command(
            [
                "nmcli",
                "device",
                "wifi",
                "rescan",
                "ifname",
                preferred_ifname,
            ],
            timeout=25,
        )

        wifi_list_after_rescan_result = _phase28_r153_run_command(
            [
                "nmcli",
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY,CHAN",
                "device",
                "wifi",
                "list",
                "ifname",
                preferred_ifname,
            ],
            timeout=25,
        )

        ap_connection_attempts = []

        ap_connection_result = _phase28_r153_run_command(
            [
                "nmcli",
                "--wait",
                "45",
                "device",
                "wifi",
                "connect",
                ap_ssid,
                "ifname",
                preferred_ifname,
            ],
            timeout=70,
        )
        ap_connection_attempts.append(
            {
                "method": "ssid_connect_after_stale_profile_delete",
                "result": phase28_r170_compact_command_result(
                    ap_connection_result
                ),
            }
        )

        if ap_connection_result.get("returncode") != 0:
            second_rescan_result = _phase28_r153_run_command(
                [
                    "nmcli",
                    "device",
                    "wifi",
                    "rescan",
                    "ifname",
                    preferred_ifname,
                ],
                timeout=25,
            )

            second_wifi_list_result = _phase28_r153_run_command(
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "SSID,SIGNAL,SECURITY,CHAN",
                    "device",
                    "wifi",
                    "list",
                    "ifname",
                    preferred_ifname,
                ],
                timeout=25,
            )

            time.sleep(3)

            ap_connection_retry_result = _phase28_r153_run_command(
                [
                    "nmcli",
                    "--wait",
                    "45",
                    "device",
                    "wifi",
                    "connect",
                    ap_ssid,
                    "ifname",
                    preferred_ifname,
                ],
                timeout=70,
            )

            ap_connection_attempts.append(
                {
                    "method": "ssid_connect_second_rescan_retry",
                    "result": phase28_r170_compact_command_result(
                        ap_connection_retry_result
                    ),
                    "second_rescan_result": phase28_r170_compact_command_result(
                        second_rescan_result
                    ),
                    "second_wifi_list_result": phase28_r170_compact_command_result(
                        second_wifi_list_result
                    ),
                }
            )

            ap_connection_result = ap_connection_retry_result

        if ap_connection_result.get("returncode") != 0:
            diagnostic = {
                "phase28_r170_shelly_ap_connect_diagnostics": True,
                "ap_ssid": ap_ssid,
                "preferred_ifname": preferred_ifname,
                "wifi_list_before_result": phase28_r170_compact_command_result(
                    wifi_list_before_result
                ),
                "stale_ap_delete_result": phase28_r170_compact_command_result(
                    stale_ap_delete_result
                ),
                "rescan_result": phase28_r170_compact_command_result(
                    rescan_result
                ),
                "wifi_list_after_rescan_result": phase28_r170_compact_command_result(
                    wifi_list_after_rescan_result
                ),
                "ap_connection_attempts": ap_connection_attempts,
            }

            raise RuntimeError(
                "Failed to connect Fanda temporarily to Shelly AP. "
                + "R170 diagnostics: "
                + repr(diagnostic)
            )
        # PHASE28_R170_SHELLY_AP_CONNECT_DIAGNOSTICS_END
        shelly_wait = _phase28_r153_wait_for_shelly_rpc(
            shelly_host,
            timeout_seconds=45,
        )

        if not shelly_wait.get("ok"):
            raise RuntimeError("Shelly AP RPC endpoint was not reachable.")

        shelly_info = shelly_wait.get("result")

        set_config_result = _phase28_r153_shelly_rpc(
            shelly_host,
            "WiFi.SetConfig",
            {
                "config": {
                    "sta": {
                        "ssid": target_wifi_ssid,
                        "pass": target_wifi_password,
                        "enable": True,
                    }
                }
            },
            timeout=12,
        )

        if not set_config_result.get("ok"):
            set_config_result = _phase28_r153_shelly_rpc(
                shelly_host,
                "Wifi.SetConfig",
                {
                    "config": {
                        "sta": {
                            "ssid": target_wifi_ssid,
                            "pass": target_wifi_password,
                            "enable": True,
                        }
                    }
                },
                timeout=12,
            )

        if not set_config_result.get("ok"):
            raise RuntimeError("Shelly WiFi.SetConfig failed.")

        if reboot_after_config:
            reboot_result = _phase28_r153_shelly_rpc(
                shelly_host,
                "Shelly.Reboot",
                {},
                timeout=6,
            )

        time.sleep(6)

    finally:
        if original_connection is not None and original_connection.get("uuid"):
            reconnect_result = _phase28_r153_run_command(
                [
                    "nmcli",
                    "connection",
                    "up",
                    "uuid",
                    str(original_connection.get("uuid")),
                ],
                timeout=60,
            )

        rollback_marker_removed = _phase28_r153_remove_file(
            marker_path,
        )

        try:
            _phase28_r153_run_command(
                [
                    "nmcli",
                    "connection",
                    "delete",
                    "id",
                    ap_ssid,
                ],
                timeout=20,
            )
        except Exception:
            pass

    if reconnect_result is None or reconnect_result.get("returncode") != 0:
        raise RuntimeError(
            "Fanda did not reconnect to the original WiFi connection after Shelly onboarding."
        )

    return {
        "phase": "shelly_ap_onboarding_completed",
        "executor": "wifi_native_onboarding",
        "phase28_r153_shelly_ap_onboarding_base": True,
        "phase28_r153_fix2": True,
        "requires_home_assistant_ui": False,
        "credential_write": True,
        "wifi_runtime_change": True,
        "shelly_ap_ssid": ap_ssid,
        "shelly_host": shelly_host,
        "target_wifi_ssid": target_wifi_ssid,
        "target_wifi_password": _phase28_r153_redact_secret(
            target_wifi_password,
        ),
        "password_redacted": True,
        "preferred_ifname": preferred_ifname,
        "original_connection": original_connection,
        "active_connections_before": active_connections,
        "rollback_watchdog": rollback_watchdog,
        "rollback_marker_removed": rollback_marker_removed,
        "preflight_command": preflight,
        "rescan_result": rescan_result,
        "ap_connection_result": ap_connection_result,
        "phase28_r170_wifi_list_before_result": wifi_list_before_result,
        "phase28_r170_wifi_list_after_rescan_result": wifi_list_after_rescan_result,
        "phase28_r170_stale_ap_delete_result": stale_ap_delete_result,
        "phase28_r170_ap_connection_attempts": ap_connection_attempts,
        "shelly_info": _phase28_r173_redact_sensitive_result(shelly_info),
        "set_config_result": _phase28_r173_redact_sensitive_result(set_config_result),
        "reboot_result": _phase28_r173_redact_sensitive_result(reboot_result),
        "reconnect_original_result": reconnect_result,
        "next_step": "discover_shelly_on_lan_after_device_joins_target_wifi",
    }
# PHASE28_R153_FIX2_SHELLY_AP_ONBOARDING_BASE_END

# PHASE28_R177G1_R3_WIFI_REGISTRY_CONTROL_START
def _phase28_r177g1_registry_path():
    from pathlib import Path

    return Path("/data/wifi_native_registry.json")


def _phase28_r177g1_load_registry():
    import json

    path = _phase28_r177g1_registry_path()

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def _phase28_r177g1_save_registry(registry):
    import json
    import os

    path = _phase28_r177g1_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            registry,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        os.chmod(temp, 0o600)
    except Exception:
        pass

    os.replace(temp, path)

    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _phase28_r177g1_registry_key(device):
    import re

    mac = str(
        device.get("mac_address")
        or device.get("mac")
        or ""
    ).strip()

    normalized_mac = re.sub(
        r"[^0-9A-Fa-f]",
        "",
        mac,
    ).upper()

    profile = str(
        device.get("profile")
        or "wifi"
    ).strip().lower()

    if normalized_mac:
        return f"wifi:{profile}:{normalized_mac}"

    return str(
        device.get("registry_id")
        or device.get("device_id")
        or ""
    ).strip()


def _phase28_r177g1_find_registry_entry(registry, device_key):
    key = str(device_key or "").strip()

    if not key:
        return None, None

    if key in registry and isinstance(registry[key], dict):
        return key, registry[key]

    for registry_key, item in registry.items():
        if not isinstance(item, dict):
            continue

        candidates = {
            str(item.get("registry_id") or "").strip(),
            str(item.get("device_id") or "").strip(),
            str(item.get("mac_address") or "").strip(),
        }

        if key in candidates:
            return registry_key, item

    return None, None


def _phase28_r177g1_validate_local_ipv4(value):
    import ipaddress

    ip_text = str(value or "").strip()
    ip = ipaddress.ip_address(ip_text)

    if ip.version != 4:
        raise RuntimeError(
            "Wi-Fi device control requires IPv4."
        )

    if not ip.is_private:
        raise RuntimeError(
            "Wi-Fi device control is allowed only on local private IPv4."
        )

    return ip_text


def _phase28_r177g1_shelly_rpc(ip_address, method, params):
    import json
    from urllib import request

    host = _phase28_r177g1_validate_local_ipv4(
        ip_address,
    )

    body = json.dumps(
        {
            "id": 1,
            "method": str(method),
            "params": dict(params or {}),
        }
    ).encode("utf-8")

    req = request.Request(
        f"http://{host}/rpc",
        data=body,
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=4.0) as response:
            payload = json.loads(
                response.read().decode(
                    "utf-8",
                    errors="replace",
                )
            )
    except Exception as exc:
        raise RuntimeError(
            f"Shelly RPC {method} failed: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Shelly RPC {method} returned invalid payload."
        )

    if payload.get("error"):
        raise RuntimeError(
            f"Shelly RPC {method} error: {payload.get('error')}"
        )

    result = payload.get("result")

    return result if isinstance(result, dict) else {}


def _phase28_r177g1_read_shelly_switch(ip_address):
    result = _phase28_r177g1_shelly_rpc(
        ip_address,
        "Switch.GetStatus",
        {
            "id": 0,
        },
    )

    output = result.get("output")

    if not isinstance(output, bool):
        raise RuntimeError(
            "Shelly Switch.GetStatus did not return boolean output."
        )

    return output


def _phase28_r177g1_enrich_wifi_devices(devices):
    import copy
    from datetime import datetime, timezone

    if not isinstance(devices, list):
        return []

    registry = _phase28_r177g1_load_registry()
    now = datetime.now(timezone.utc).isoformat()
    enriched = []
    changed = False

    for raw_device in devices:
        if not isinstance(raw_device, dict):
            continue

        device = copy.deepcopy(raw_device)
        registry_key = _phase28_r177g1_registry_key(
            device,
        )

        if not registry_key:
            enriched.append(device)
            continue

        old = registry.get(registry_key)
        entry = dict(old) if isinstance(old, dict) else {}

        entry.update(
            {
                "registry_id": registry_key,
                "device_id": device.get("device_id"),
                "profile": device.get("profile"),
                "manufacturer": device.get("manufacturer"),
                "model": device.get("model"),
                "ip_address": device.get("ip_address"),
                "mac_address": device.get("mac_address"),
                "discovered_name": (
                    device.get("discovered_name")
                    or device.get("name")
                ),
                "last_seen_at": now,
            }
        )

        friendly_name = str(
            entry.get("friendly_name") or ""
        ).strip()

        device["registry_id"] = registry_key
        device["discovered_name"] = (
            device.get("discovered_name")
            or device.get("name")
        )

        if friendly_name:
            device["friendly_name"] = friendly_name
            device["name"] = friendly_name

        onboarding = dict(
            device.get("onboarding")
            if isinstance(device.get("onboarding"), dict)
            else {}
        )

        if friendly_name:
            onboarding["status"] = "registered"

        onboarding["requires_ha_ui"] = False
        device["onboarding"] = onboarding

        profile = str(
            device.get("profile")
            or ""
        ).strip().lower()

        if profile == "shelly_gen2_rpc":
            ip_address = device.get("ip_address")

            try:
                switch_state = _phase28_r177g1_read_shelly_switch(
                    ip_address,
                )
            except Exception as exc:
                device["switch_probe_error"] = str(exc)
            else:
                device["capabilities"] = sorted(
                    set(
                        list(device.get("capabilities") or [])
                        + ["switch"]
                    )
                )

                entity = {
                    "entity_id": "switch:0",
                    "entity_key": "switch:0",
                    "domain": "switch",
                    "name": "Spínání",
                    "state": "on" if switch_state else "off",
                    "state_bool": switch_state,
                    "available": True,
                    "controllable": True,
                }

                device["entities"] = [entity]
                device["entity_count"] = 1
                device["control_entity_count"] = 1
                device["switch_state"] = switch_state

                entry["switch_state"] = switch_state
                entry["control_entity_count"] = 1
                entry["capabilities"] = ["switch"]

        registry[registry_key] = entry
        changed = True
        enriched.append(device)

    if changed:
        _phase28_r177g1_save_registry(
            registry,
        )

    return enriched


def _phase28_r177g1_register_wifi_device(payload):
    import re
    from datetime import datetime, timezone

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Wi-Fi registration payload must be an object."
        )

    device_key = str(
        payload.get("device_key")
        or payload.get("registry_id")
        or payload.get("device_id")
        or ""
    ).strip()

    friendly_name = str(
        payload.get("friendly_name")
        or payload.get("name")
        or ""
    ).strip()

    if not device_key:
        raise RuntimeError(
            "Wi-Fi device key is required."
        )

    if not friendly_name or len(friendly_name) > 80:
        raise RuntimeError(
            "Wi-Fi friendly name must contain 1 to 80 characters."
        )

    if re.search(r"[\x00-\x1f\x7f]", friendly_name):
        raise RuntimeError(
            "Wi-Fi friendly name contains invalid control characters."
        )

    registry = _phase28_r177g1_load_registry()
    registry_key, entry = _phase28_r177g1_find_registry_entry(
        registry,
        device_key,
    )

    if entry is None:
        raise RuntimeError(
            "Wi-Fi device is not present in the local registry. Run discovery first."
        )

    entry = dict(entry)
    entry["friendly_name"] = friendly_name
    entry["registered"] = True
    entry["registered_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    registry[registry_key] = entry
    _phase28_r177g1_save_registry(
        registry,
    )

    return {
        "phase": "wifi_native_device_registered",
        "requested_action": "register_wifi_device",
        "registry_id": registry_key,
        "device_id": entry.get("device_id"),
        "friendly_name": friendly_name,
        "registered": True,
        "requires_home_assistant_ui": False,
        "credential_write": False,
        "wifi_runtime_change": False,
    }


def _phase28_r177g1_set_wifi_switch_state(payload):
    if not isinstance(payload, dict):
        raise RuntimeError(
            "Wi-Fi switch payload must be an object."
        )

    device_key = str(
        payload.get("device_key")
        or payload.get("registry_id")
        or payload.get("device_id")
        or ""
    ).strip()

    target_state = payload.get("target_state")

    if not device_key:
        raise RuntimeError(
            "Wi-Fi device key is required."
        )

    if not isinstance(target_state, bool):
        raise RuntimeError(
            "target_state must be boolean."
        )

    registry = _phase28_r177g1_load_registry()
    registry_key, entry = _phase28_r177g1_find_registry_entry(
        registry,
        device_key,
    )

    if entry is None:
        raise RuntimeError(
            "Wi-Fi device is not present in the local registry."
        )

    profile = str(
        entry.get("profile")
        or ""
    ).strip().lower()

    if profile != "shelly_gen2_rpc":
        raise RuntimeError(
            f"Wi-Fi switch control is not supported for profile {profile or 'unknown'}."
        )

    ip_address = _phase28_r177g1_validate_local_ipv4(
        entry.get("ip_address"),
    )

    _phase28_r177g1_shelly_rpc(
        ip_address,
        "Switch.Set",
        {
            "id": 0,
            "on": target_state,
        },
    )

    actual_state = _phase28_r177g1_read_shelly_switch(
        ip_address,
    )

    entry = dict(entry)
    entry["switch_state"] = actual_state
    registry[registry_key] = entry
    _phase28_r177g1_save_registry(
        registry,
    )

    return {
        "phase": "wifi_native_switch_state_changed",
        "requested_action": "set_wifi_switch_state",
        "registry_id": registry_key,
        "device_id": entry.get("device_id"),
        "friendly_name": entry.get("friendly_name"),
        "device_control": True,
        "control_id": "switch:0",
        "target_state": target_state,
        "actual_state": actual_state,
        "requires_home_assistant_ui": False,
        "credential_write": False,
        "wifi_runtime_change": False,
    }
# PHASE28_R177G1_R3_WIFI_REGISTRY_CONTROL_END

# PHASE28_R177G4_STABLE_WIFI_ENTITY_CATALOG_START
_phase28_r177g4_base_enrich_wifi_devices = (
    _phase28_r177g1_enrich_wifi_devices
)


def _phase28_r177g4_shelly_switch_status(ip_address):
    return _phase28_r177g1_shelly_rpc(
        ip_address,
        "Switch.GetStatus",
        {
            "id": 0,
        },
    )


def _phase28_r177g4_float(value):
    if isinstance(value, bool):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _phase28_r177g4_entity_id(registry_id, entity_key):
    return (
        f"{str(registry_id).strip()}:"
        f"{str(entity_key).strip()}"
    )


def _phase28_r177g4_build_shelly_entities(
    *,
    registry_id,
    status,
):
    entities = []

    output = status.get("output")

    if isinstance(output, bool):
        entities.append(
            {
                "entity_id": _phase28_r177g4_entity_id(
                    registry_id,
                    "switch:0",
                ),
                "entity_key": "switch:0",
                "domain": "switch",
                "device_class": "outlet",
                "name": "Spínací výstup",
                "state": "on" if output else "off",
                "state_bool": output,
                "value_type": "boolean",
                "unit": None,
                "available": True,
                "controllable": True,
                "schema_visible": True,
                "statistics_enabled": False,
                "statistics_type": None,
                "source_component": "switch:0",
            }
        )

    def add_numeric(
        *,
        entity_key,
        name,
        source_value,
        device_class,
        unit,
        statistics_type="measurement",
        transform=None,
        raw_value=None,
        raw_unit=None,
    ):
        numeric = _phase28_r177g4_float(source_value)

        if numeric is None:
            return

        if transform is not None:
            numeric = transform(numeric)

        entity = {
            "entity_id": _phase28_r177g4_entity_id(
                registry_id,
                entity_key,
            ),
            "entity_key": entity_key,
            "domain": "sensor",
            "device_class": device_class,
            "name": name,
            "state": numeric,
            "state_numeric": numeric,
            "value_type": "number",
            "unit": unit,
            "available": True,
            "controllable": False,
            "schema_visible": True,
            "statistics_enabled": True,
            "statistics_type": statistics_type,
            "source_component": "switch:0",
        }

        if raw_value is not None:
            entity["raw_value"] = raw_value

        if raw_unit is not None:
            entity["raw_unit"] = raw_unit

        entities.append(entity)

    add_numeric(
        entity_key="power:0",
        name="Okamžitý výkon",
        source_value=status.get("apower"),
        device_class="power",
        unit="W",
    )

    add_numeric(
        entity_key="voltage:0",
        name="Napětí",
        source_value=status.get("voltage"),
        device_class="voltage",
        unit="V",
    )

    add_numeric(
        entity_key="current:0",
        name="Proud",
        source_value=status.get("current"),
        device_class="current",
        unit="A",
    )

    add_numeric(
        entity_key="power_factor:0",
        name="Účiník",
        source_value=status.get("pf"),
        device_class="power_factor",
        unit=None,
    )

    add_numeric(
        entity_key="frequency:0",
        name="Frekvence",
        source_value=status.get("freq"),
        device_class="frequency",
        unit="Hz",
    )

    aenergy = status.get("aenergy")

    if isinstance(aenergy, dict):
        total_wh = _phase28_r177g4_float(
            aenergy.get("total")
        )

        if total_wh is not None:
            add_numeric(
                entity_key="energy_total:0",
                name="Celková spotřeba",
                source_value=total_wh,
                device_class="energy",
                unit="kWh",
                statistics_type="total_increasing",
                transform=lambda value: value / 1000.0,
                raw_value=total_wh,
                raw_unit="Wh",
            )

    temperature = status.get("temperature")

    if isinstance(temperature, dict):
        add_numeric(
            entity_key="temperature:0",
            name="Teplota zařízení",
            source_value=temperature.get("tC"),
            device_class="temperature",
            unit="°C",
        )

    return entities


def _phase28_r177g4_enrich_wifi_devices(devices):
    enriched = _phase28_r177g4_base_enrich_wifi_devices(
        devices,
    )

    if not isinstance(enriched, list):
        return []

    registry = _phase28_r177g1_load_registry()
    registry_changed = False

    for device in enriched:
        if not isinstance(device, dict):
            continue

        profile = str(
            device.get("profile")
            or ""
        ).strip().lower()

        if profile != "shelly_gen2_rpc":
            continue

        registry_id = str(
            device.get("registry_id")
            or _phase28_r177g1_registry_key(device)
            or ""
        ).strip()

        ip_address = str(
            device.get("ip_address")
            or ""
        ).strip()

        if not registry_id or not ip_address:
            continue

        try:
            status = _phase28_r177g4_shelly_switch_status(
                ip_address,
            )
        except Exception as exc:
            device["entity_probe_error"] = str(exc)
            continue

        entities = _phase28_r177g4_build_shelly_entities(
            registry_id=registry_id,
            status=status,
        )

        if not entities:
            continue

        device["entities"] = entities
        device["entity_count"] = len(entities)
        device["control_entity_count"] = sum(
            1
            for entity in entities
            if entity.get("controllable") is True
        )
        device["statistics_entity_count"] = sum(
            1
            for entity in entities
            if entity.get("statistics_enabled") is True
        )

        switch_entity = next(
            (
                entity
                for entity in entities
                if entity.get("entity_key") == "switch:0"
            ),
            None,
        )

        if isinstance(switch_entity, dict):
            device["switch_state"] = switch_entity.get(
                "state_bool"
            )

        entry = registry.get(registry_id)

        if isinstance(entry, dict):
            entry = dict(entry)
            entry["entities"] = entities
            entry["entity_count"] = len(entities)
            entry["control_entity_count"] = (
                device["control_entity_count"]
            )
            entry["statistics_entity_count"] = (
                device["statistics_entity_count"]
            )
            registry[registry_id] = entry
            registry_changed = True

    if registry_changed:
        _phase28_r177g1_save_registry(
            registry,
        )

    return enriched


_phase28_r177g1_enrich_wifi_devices = (
    _phase28_r177g4_enrich_wifi_devices
)
# PHASE28_R177G4_STABLE_WIFI_ENTITY_CATALOG_END
