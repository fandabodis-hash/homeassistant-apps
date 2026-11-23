"""F132: 00019-only, filesystem-evidenced FTDI inventory for read-only RTU canary.

This function never opens a serial port, queues a command or alters existing
communication configuration. It only enriches the outbound heartbeat.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

DEVICE_UUID = "648d4229-7c17-400f-bbed-ffe5ab04b844"
FTDI_SERIAL = "BG02YF7T"
FTDI_PATH = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02YF7T-if00-port0"
USB_INVENTORY_PATH = Path("/config/usb_inventory.json")
MAX_INVENTORY_BYTES = 2 * 1024 * 1024


def enrich_if_verified(
    communication_state: dict[str, Any],
    *,
    device_uuid: str,
    inventory_path: Path = USB_INVENTORY_PATH,
    is_serial_device: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Advertise exactly one physically evidenced serial device, only for 00019.

    The existing heartbeat/communication manager already provides its own
    communicator inventory. Only a *missing* exact FTDI entry is appended.
    An absent, ambiguous or mismatched physical USB record means no change.
    """
    if str(device_uuid).strip().lower() != DEVICE_UUID:
        return communication_state
    if not isinstance(communication_state, dict):
        return communication_state
    if is_serial_device is None:
        is_serial_device = lambda p: Path(p).is_char_device()
    try:
        if not is_serial_device(FTDI_PATH):
            return communication_state
        with Path(inventory_path).open("rb") as stream:
            payload = stream.read(MAX_INVENTORY_BYTES + 1)
        if len(payload) > MAX_INVENTORY_BYTES:
            return communication_state
        inventory = json.loads(payload.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return communication_state
    devices = inventory.get("devices") if isinstance(inventory, dict) else None
    if not isinstance(devices, list):
        return communication_state
    matches = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        if str(device.get("vid") or "").upper() != "0403":
            continue
        if str(device.get("pid") or "").upper() != "6001":
            continue
        if str(device.get("serial_number") or "").strip() != FTDI_SERIAL:
            continue
        if device.get("connected") is not True:
            continue
        ports = device.get("serial_ports")
        if not isinstance(ports, list):
            continue
        if sum(
            1 for p in ports
            if isinstance(p, dict) and p.get("stable_path") == FTDI_PATH
        ) != 1:
            continue
        matches.append(device)
    if len(matches) != 1:
        return communication_state

    current = communication_state.get("communicators")
    if current is not None and not isinstance(current, list):
        return communication_state
    current = list(current or [])
    if any(not isinstance(c, dict) for c in current):
        return communication_state
    existing = [c for c in current if c.get("preferred_path") == FTDI_PATH]
    if existing:
        return communication_state  # Never override the authoritative inventory.

    communicator = {
        "communicator_id": str(matches[0].get("inventory_id") or ""),
        "type": "usb_serial",
        "preferred_path": FTDI_PATH,
        "connected": True,
        "vid": "0403", "pid": "6001", "serial_number": FTDI_SERIAL,
        "capabilities": ["serial", "rs485_or_rs232"],
        "source": "verified_usb_inventory_00019_read_only",
    }
    updated = dict(communication_state)
    updated["communicators"] = current + [communicator]
    return updated
