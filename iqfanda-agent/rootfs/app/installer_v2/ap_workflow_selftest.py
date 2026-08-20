"""Self-test zavazneho AP workflow Installer V2."""

from installer_v2.ap_workflow import (
    SERVICE_WINDOW_SECONDS,
    TypPripojeni,
    konfigurace_ap_pro_rezim,
    muze_vytvorit_nove_seriove_cislo,
    pole_prvni_instalace,
    validuj_prvni_instalaci,
)
from installer_v2.models import InstallerMode


def run() -> None:
    first = konfigurace_ap_pro_rezim(
        InstallerMode.FIRST_INSTALL
    )

    assert first["access_point_enabled"] is True
    assert first["timeout_seconds"] is None
    assert first["countdown_visible"] is False
    assert first["serial_number_editable"] is False
    assert first["cloud_password_visible"] is False

    assert pole_prvni_instalace(
        TypPripojeni.ETHERNET
    ) == [
        "customer_name",
        "email",
        "connection_type",
    ]

    assert pole_prvni_instalace(
        TypPripojeni.WIFI
    ) == [
        "customer_name",
        "email",
        "connection_type",
        "ssid",
        "wifi_password",
    ]

    ethernet = validuj_prvni_instalaci({
        "customer_name": "Test Zakaznik",
        "email": "test@example.cz",
        "connection_type": "ethernet",
    })

    assert ethernet["ok"] is True

    wifi = validuj_prvni_instalaci({
        "customer_name": "Test Zakaznik",
        "email": "test@example.cz",
        "connection_type": "wifi",
        "ssid": "TEST_WIFI",
        "wifi_password": "12345678",
    })

    assert wifi["ok"] is True

    forbidden = validuj_prvni_instalaci({
        "customer_name": "Test Zakaznik",
        "email": "test@example.cz",
        "connection_type": "ethernet",
        "device_serial_number": "F800711-TNG-99999",
    })

    assert forbidden["ok"] is False
    assert (
        "device_serial_number"
        in forbidden["fields"]
    )

    installed = konfigurace_ap_pro_rezim(
        InstallerMode.INSTALLED_BOOT_WINDOW
    )

    assert (
        installed["timeout_seconds"]
        == SERVICE_WINDOW_SECONDS
        == 60
    )

    assert installed["countdown_visible"] is True

    assert installed["allowed_actions"] == [
        "change_wifi",
    ]

    maintenance = konfigurace_ap_pro_rezim(
        InstallerMode.WIFI_MAINTENANCE
    )

    assert maintenance["timeout_seconds"] is None
    assert maintenance["countdown_visible"] is False
    assert maintenance["fields"] == [
        "ssid",
        "wifi_password",
    ]

    assert muze_vytvorit_nove_seriove_cislo(
        InstallerMode.FIRST_INSTALL
    ) is True

    assert muze_vytvorit_nove_seriove_cislo(
        InstallerMode.RECOVERY
    ) is False

    assert muze_vytvorit_nove_seriove_cislo(
        InstallerMode.INSTALLED_BOOT_WINDOW
    ) is False

    assert muze_vytvorit_nove_seriove_cislo(
        InstallerMode.WIFI_MAINTENANCE
    ) is False

    print(
        "Installer V2 AP workflow self-test: OK"
    )


if __name__ == "__main__":
    run()