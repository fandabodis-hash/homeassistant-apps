"""Datove modely komunikacniho centra TNG IQ FANDA."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1


def aktualni_cas_iso() -> str:
    """Vrati aktualni UTC cas ve formatu ISO 8601."""

    return datetime.now(timezone.utc).isoformat()


def vytvor_prazdny_stav() -> dict[str, Any]:
    """Vytvori prazdny komunikacni stav."""

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": aktualni_cas_iso(),
        "source_status": "unknown",
        "source_status_name": "Stav zdroje není znám",
        "source_inventory_generated_at": None,
        "source_inventory_hash": None,
        "communicators": [],
        "services": [],
        "adapters": [],
        "installation_jobs": [],
        "summary": {
            "communicators_total": 0,
            "communicators_connected": 0,
            "services_total": 0,
            "services_running": 0,
            "adapters_total": 0,
            "installation_jobs_active": 0,
        },
    }


def urci_schopnosti(
    typ_komunikatoru: str | None,
) -> list[str]:
    """Vrati schopnosti podle typu komunikatoru."""

    mapa = {
        "zigbee_coordinator": [
            "zigbee",
            "serial",
        ],
        "usb_rs485": [
            "rs485",
            "modbus_rtu",
            "serial",
        ],
        "usb_rs232": [
            "rs232",
            "serial",
        ],
        "usb_serial": [
            "serial",
            "rs485_or_rs232",
        ],
    }

    return mapa.get(
        typ_komunikatoru or "",
        [],
    )


def vytvor_komunikator(
    usb_zarizeni: dict[str, Any],
) -> dict[str, Any]:
    """Prevede USB zaznam na komunikator."""

    serial_ports = usb_zarizeni.get("serial_ports")

    if not isinstance(serial_ports, list):
        serial_ports = []

    stabilni_cesty = [
        port.get("stable_path")
        for port in serial_ports
        if isinstance(port, dict)
        and port.get("stable_path")
    ]

    bezne_cesty = [
        port.get("port")
        for port in serial_ports
        if isinstance(port, dict)
        and port.get("port")
    ]

    preferovana_cesta = None

    if stabilni_cesty:
        preferovana_cesta = stabilni_cesty[0]
    elif bezne_cesty:
        preferovana_cesta = bezne_cesty[0]

    connected = bool(
        usb_zarizeni.get("connected", True)
    )

    return {
        "communicator_id": usb_zarizeni.get(
            "inventory_id"
        ),
        "type": usb_zarizeni.get(
            "type",
            "usb_device",
        ),
        "type_name": usb_zarizeni.get(
            "type_name",
            "USB zařízení",
        ),
        "manufacturer": usb_zarizeni.get(
            "manufacturer"
        ),
        "product": usb_zarizeni.get("product"),
        "vid": usb_zarizeni.get("vid"),
        "pid": usb_zarizeni.get("pid"),
        "serial_number": usb_zarizeni.get(
            "serial_number"
        ),
        "connected": connected,
        "status": (
            "ready"
            if connected
            else "disconnected"
        ),
        "status_name": (
            "Připraveno k použití"
            if connected
            else "Odpojeno"
        ),
        "preferred_path": preferovana_cesta,
        "stable_paths": stabilni_cesty,
        "device_paths": bezne_cesty,
        "usb_version": usb_zarizeni.get(
            "usb_version"
        ),
        "speed_mbps": usb_zarizeni.get(
            "speed_mbps"
        ),
        "sysfs_name": usb_zarizeni.get(
            "sysfs_name"
        ),
        "capabilities": urci_schopnosti(
            usb_zarizeni.get("type")
        ),
    }
