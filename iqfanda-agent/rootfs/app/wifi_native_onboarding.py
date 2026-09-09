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

        ap_connection_result = _phase28_r153_run_command(
            [
                "nmcli",
                "device",
                "wifi",
                "connect",
                ap_ssid,
                "ifname",
                preferred_ifname,
            ],
            timeout=60,
        )

        if ap_connection_result.get("returncode") != 0:
            raise RuntimeError(
                "Failed to connect Fanda temporarily to Shelly AP."
            )

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
        "shelly_info": shelly_info,
        "set_config_result": set_config_result,
        "reboot_result": reboot_result,
        "reconnect_original_result": reconnect_result,
        "next_step": "discover_shelly_on_lan_after_device_joins_target_wifi",
    }
# PHASE28_R153_FIX2_SHELLY_AP_ONBOARDING_BASE_END
