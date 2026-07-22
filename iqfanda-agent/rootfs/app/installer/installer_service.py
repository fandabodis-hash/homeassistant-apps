"""Koordinacni sluzba prvotni instalace TNG IQ FANDA."""

from typing import Any

from installer.network_manager import (
    check_internet_access,
    connect_wifi,
    get_wifi_status,
    scan_wifi_networks,
)
from installer.provisioning_adapter import (
    registrovat_zarizeni,
    ulozit_registraci,
)
from installer.setup_manager import setup_manager


def ziskat_stav_wifi() -> dict[str, Any]:
    """Vrati aktualni stav Wi-Fi rozhrani."""

    return get_wifi_status()


def vyhledat_wifi_site() -> dict[str, Any]:
    """Vyhleda dostupne Wi-Fi site."""

    return scan_wifi_networks()


def pripojit_wifi(
    ssid: str,
    password: str,
    interface: str = "wlan0",
) -> dict[str, Any]:
    """Pripoji zarizeni k vybrane Wi-Fi siti."""

    return connect_wifi(
        ssid=ssid,
        password=password,
        interface=interface,
    )


def spustit_instalaci(
    ssid: str,
    password: str,
    provisioning_id: str,
    first_name: str,
    last_name: str,
    email: str,
    account_password: str,
    site_name: str,
    site_type: str,
    device_serial_number: str | None = None,
    device_name: str = "TNG IQ FANDA",
    software_version: str | None = None,
    interface: str = "wlan0",
) -> dict[str, Any]:
    """Spusti asynchronni prvotni instalaci zarizeni."""

    return setup_manager.start(
        ssid=ssid,
        password=password,
        provisioning_id=provisioning_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        account_password=account_password,
        site_name=site_name,
        site_type=site_type,
        device_serial_number=device_serial_number,
        device_name=device_name,
        software_version=software_version,
        interface=interface,
        connector=connect_wifi,
        network_checker=check_internet_access,
        registrar=registrovat_zarizeni,
        credential_saver=ulozit_registraci,
    )


def ziskat_stav_instalace() -> dict[str, Any]:
    """Vrati aktualni stav prvotni instalace."""

    return setup_manager.get_status()


def resetovat_instalaci() -> dict[str, Any]:
    """Vrati instalacni proces do vychoziho stavu."""

    return setup_manager.reset()

