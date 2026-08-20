"""Sitovy model TNG IQ FANDA Installer V2.

Tento modul zatim NIC NEMENI v NetworkManageru.
Pouze vyhodnocuje data, ktera pozdeji doda sitovy adapter.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NetworkInterfaceState:
    """Normalizovany stav jednoho sitoveho rozhrani."""

    name: str
    kind: str
    connected: bool
    ip_address: str | None = None
    default_route: bool = False


def select_wired_uplink(
    interfaces: list[NetworkInterfaceState],
) -> NetworkInterfaceState | None:
    """
    Najde aktivni kabelovy uplink bez predpokladu nazvu eth0.

    Prioritu ma ethernetove rozhrani s default route.
    Pokud takove neni, pouzije prvni aktivni ethernet s IP.
    """

    connected_wired = [
        interface
        for interface in interfaces
        if (
            interface.kind.strip().lower() == "ethernet"
            and interface.connected
            and bool(
                str(interface.ip_address or "").strip()
            )
        )
    ]

    for interface in connected_wired:
        if interface.default_route:
            return interface

    if connected_wired:
        return connected_wired[0]

    return None