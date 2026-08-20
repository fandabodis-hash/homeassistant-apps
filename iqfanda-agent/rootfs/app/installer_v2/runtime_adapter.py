"""Read-only runtime adapter TNG IQ FANDA Installer V2.

Tento modul smi pouze cist skutecny stav zarizeni.

Nesmi:
- menit NetworkManager,
- vytvaret nebo mazat Wi-Fi profily,
- vypinat ani zapinat AP,
- zapisovat device.json,
- menit vyrobni identitu,
- volat cloudovou registraci.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from installer_v2.identity_state import (
    evaluate_cloud_identity,
)
from installer_v2.network_state import (
    NetworkInterfaceState,
    select_wired_uplink,
)
from installer_v2.state_machine import (
    determine_installer_state,
)


DEFAULT_DEVICE_CONFIG_PATH = Path("/config/device.json")


def read_cloud_identity(
    path: Path = DEFAULT_DEVICE_CONFIG_PATH,
) -> dict[str, Any]:
    """Bezpecne a pouze pro cteni nacte cloudovou identitu."""

    identity_path = Path(path)

    if not identity_path.exists():
        return {
            "exists": False,
            "read_error": False,
            "payload": None,
            "path": str(identity_path),
        }

    try:
        with identity_path.open(
            "r",
            encoding="utf-8-sig",
        ) as handle:
            payload = json.load(handle)

    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        return {
            "exists": True,
            "read_error": True,
            "payload": None,
            "path": str(identity_path),
            "error": str(exc),
        }

    return {
        "exists": True,
        "read_error": False,
        "payload": payload,
        "path": str(identity_path),
    }


def sanitize_cloud_identity(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Vrati pouze bezpecny diagnosticky souhrn.

    device_token se nikdy nevraci ani neloguje.
    """

    if not isinstance(payload, dict):
        return {
            "device_uuid_present": False,
            "device_token_present": False,
            "device_id": None,
            "site_id": None,
            "device_status": None,
        }

    return {
        "device_uuid_present": bool(
            str(payload.get("device_uuid") or "").strip()
        ),
        "device_token_present": bool(
            str(payload.get("device_token") or "").strip()
        ),
        "device_id": payload.get("device_id"),
        "site_id": payload.get("site_id"),
        "device_status": payload.get("device_status"),
    }


def normalize_network_interfaces(
    network_result: dict[str, Any],
) -> list[NetworkInterfaceState]:
    """Prevede legacy read-only sitovy snapshot do modelu V2."""

    interfaces: list[NetworkInterfaceState] = []

    raw_interfaces = network_result.get("interfaces", [])

    if not isinstance(raw_interfaces, list):
        return interfaces

    for item in raw_interfaces:
        if not isinstance(item, dict):
            continue

        name = str(
            item.get("name") or ""
        ).strip()

        kind = str(
            item.get("type") or ""
        ).strip()

        if not name or not kind:
            continue

        interfaces.append(
            NetworkInterfaceState(
                name=name,
                kind=kind,
                connected=bool(
                    item.get("connected")
                ),
                ip_address=(
                    str(item.get("ip_address")).strip()
                    if item.get("ip_address")
                    else None
                ),
                default_route=False,
            )
        )

    return interfaces


def build_runtime_snapshot(
    *,
    device_config_path: Path = DEFAULT_DEVICE_CONFIG_PATH,
    network_reader: Callable[[], dict[str, Any]] | None = None,
    wifi_reader: Callable[[], dict[str, Any]] | None = None,
    service_window_active: bool = False,
    wifi_maintenance_requested: bool = False,
) -> dict[str, Any]:
    """
    Sestavi skutecny read-only snapshot Installer V2.

    Defaultni network_reader a wifi_reader pouzivaji pouze
    cteci funkce stavajiciho installer.network_manager.
    """

    if network_reader is None or wifi_reader is None:
        from installer.network_manager import (
            get_network_info,
            get_wifi_status,
        )

        if network_reader is None:
            network_reader = get_network_info

        if wifi_reader is None:
            wifi_reader = get_wifi_status

    identity_source = read_cloud_identity(
        path=device_config_path,
    )

    identity_evaluation = evaluate_cloud_identity(
        identity_exists=identity_source["exists"],
        identity_payload=identity_source["payload"],
        identity_read_error=identity_source[
            "read_error"
        ],
    )

    installer_state = determine_installer_state(
        cloud_identity_valid=identity_evaluation[
            "valid"
        ],
        cloud_identity_missing=identity_evaluation[
            "missing"
        ],
        cloud_identity_recovery=identity_evaluation[
            "recovery"
        ],
        service_window_active=service_window_active,
        wifi_maintenance_requested=(
            wifi_maintenance_requested
        ),
    )

    network_result = network_reader()

    if not isinstance(network_result, dict):
        network_result = {
            "ok": False,
            "interfaces": [],
            "error": "network_reader vratil neplatny format",
        }

    normalized_interfaces = normalize_network_interfaces(
        network_result
    )

    wired_uplink = select_wired_uplink(
        normalized_interfaces
    )

    wifi_result = wifi_reader()

    if not isinstance(wifi_result, dict):
        wifi_result = {
            "ok": False,
            "connected": False,
            "error": "wifi_reader vratil neplatny format",
        }

    return {
        "installer": {
            "mode": installer_state.mode.value,
            "installed": installer_state.installed,
            "access_point_required": (
                installer_state.access_point_required
            ),
            "service_window_seconds": (
                installer_state.service_window_seconds
            ),
            "reason": installer_state.reason,
        },
        "identity": {
            "evaluation": identity_evaluation,
            "summary": sanitize_cloud_identity(
                identity_source["payload"]
            ),
            "path": identity_source["path"],
            "read_error": identity_source[
                "read_error"
            ],
        },
        "network": {
            "ok": bool(network_result.get("ok")),
            "interfaces": [
                {
                    "name": interface.name,
                    "kind": interface.kind,
                    "connected": interface.connected,
                    "ip_address": interface.ip_address,
                }
                for interface in normalized_interfaces
            ],
            "wired_uplink": (
                {
                    "name": wired_uplink.name,
                    "ip_address": (
                        wired_uplink.ip_address
                    ),
                }
                if wired_uplink is not None
                else None
            ),
        },
        "wifi": {
            "ok": bool(wifi_result.get("ok")),
            "connected": bool(
                wifi_result.get("connected")
            ),
            "interface": wifi_result.get(
                "interface"
            ),
            "connection_name": wifi_result.get(
                "connection_name"
            ),
            "ssid": wifi_result.get("ssid"),
            "ip_address": wifi_result.get(
                "ip_address"
            ),
        },
    }