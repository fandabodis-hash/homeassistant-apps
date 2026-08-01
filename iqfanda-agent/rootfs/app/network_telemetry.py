from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib import error, request


SUPERVISOR_BASE_URL = "http://supervisor"
DEFAULT_TIMEOUT_SECONDS = 5


class SupervisorApiError(RuntimeError):
    pass


def _get_supervisor_token() -> str:
    token = str(
        os.getenv("SUPERVISOR_TOKEN") or ""
    ).strip()

    if not token:
        raise SupervisorApiError(
            "SUPERVISOR_TOKEN není dostupný."
        )

    return token


def _supervisor_get(
    endpoint: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    url = (
        SUPERVISOR_BASE_URL.rstrip("/")
        + "/"
        + endpoint.lstrip("/")
    )

    http_request = request.Request(
        url,
        method="GET",
        headers={
            "Authorization": (
                f"Bearer {_get_supervisor_token()}"
            ),
            "Accept": "application/json",
        },
    )

    try:
        with request.urlopen(
            http_request,
            timeout=timeout_seconds,
        ) as response:
            payload = json.loads(
                response.read().decode("utf-8")
            )

    except (
        error.HTTPError,
        error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise SupervisorApiError(
            f"Supervisor API {endpoint} selhalo: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise SupervisorApiError(
            f"Supervisor API {endpoint} vrátilo "
            "neplatný formát."
        )

    if payload.get("result") != "ok":
        raise SupervisorApiError(
            f"Supervisor API {endpoint} vrátilo chybu."
        )

    data = payload.get("data")

    if not isinstance(data, dict):
        raise SupervisorApiError(
            f"Supervisor API {endpoint} "
            "neobsahuje objekt data."
        )

    return data


def _first_ipv4_address(
    interface: dict[str, Any],
) -> str | None:
    ipv4 = interface.get("ipv4")

    if not isinstance(ipv4, dict):
        return None

    addresses = ipv4.get("address")

    if not isinstance(addresses, list):
        return None

    for raw_address in addresses:
        address = str(
            raw_address or ""
        ).strip()

        if not address:
            continue

        return address.split("/", 1)[0]

    return None


def collect_network_telemetry() -> dict[str, Any]:
    try:
        network_info = _supervisor_get(
            "/network/info"
        )

        core_info = _supervisor_get(
            "/core/info"
        )

    except SupervisorApiError as exc:
        logging.warning(
            "Síťovou telemetrii se nepodařilo načíst: %s",
            exc,
        )

        return {
            "home_assistant_running": False,
        }

    interfaces = network_info.get(
        "interfaces"
    )

    if not isinstance(interfaces, list):
        interfaces = []

    primary_interface = None
    wifi_interface = None

    for item in interfaces:
        if not isinstance(item, dict):
            continue

        if (
            item.get("connected") is True
            and item.get("primary") is True
        ):
            primary_interface = item

        if (
            item.get("type") == "wireless"
            and item.get("connected") is True
        ):
            wifi_interface = item

    telemetry: dict[str, Any] = {
        "home_assistant_running": True,
        "home_assistant_version": (
            core_info.get("version")
        ),
        "host_internet_connected": bool(
            network_info.get("host_internet")
        ),
        "supervisor_internet_connected": bool(
            network_info.get(
                "supervisor_internet"
            )
        ),
    }

    network_state: dict[str, Any] = {
        "interfaces": interfaces,
    }

    if isinstance(primary_interface, dict):
        primary_type = str(
            primary_interface.get("type") or ""
        ).strip()

        connection_type = (
            "wifi"
            if primary_type == "wireless"
            else primary_type
        )

        telemetry["ip_address"] = (
            _first_ipv4_address(
                primary_interface
            )
        )

        telemetry["mac_address"] = (
            primary_interface.get("mac")
        )

        network_state["connection_type"] = (
            connection_type
        )

        network_state["interface"] = (
            primary_interface.get("interface")
        )

        network_state["ip_address"] = (
            telemetry.get("ip_address")
        )

        network_state["mac_address"] = (
            telemetry.get("mac_address")
        )

        network_state["primary"] = True

    if isinstance(wifi_interface, dict):
        wifi = wifi_interface.get("wifi")

        if not isinstance(wifi, dict):
            wifi = {}

        network_state["wifi"] = {
            "connected": True,
            "interface": (
                wifi_interface.get("interface")
            ),
            "ssid": wifi.get("ssid"),
            "signal_percent": wifi.get("signal"),
            "ip_address": (
                _first_ipv4_address(
                    wifi_interface
                )
            ),
            "mac_address": (
                wifi_interface.get("mac")
            ),
        }

    else:
        network_state["wifi"] = {
            "connected": False,
        }

    telemetry["network_state"] = network_state

    return telemetry
