"""Samostatny self-test Installer V2.

Test nema zadne vedlejsi ucinky.
Nesaha na Wi-Fi, AP, soubory identity ani cloud.
"""

from installer_v2.identity_state import (
    evaluate_cloud_identity,
)
from installer_v2.models import (
    InstallerMode,
)
from installer_v2.network_state import (
    NetworkInterfaceState,
    select_wired_uplink,
)
from installer_v2.state_machine import (
    determine_installer_state,
)


def run() -> None:
    missing_identity = evaluate_cloud_identity(
        identity_exists=False,
        identity_payload=None,
    )

    assert missing_identity["missing"] is True
    assert missing_identity["recovery"] is False

    first_install = determine_installer_state(
        cloud_identity_valid=False,
        cloud_identity_missing=True,
        cloud_identity_recovery=False,
        service_window_active=False,
    )

    assert (
        first_install.mode
        == InstallerMode.FIRST_INSTALL
    )
    assert first_install.access_point_required is True
    assert first_install.service_window_seconds is None

    installed_boot = determine_installer_state(
        cloud_identity_valid=True,
        cloud_identity_missing=False,
        cloud_identity_recovery=False,
        service_window_active=True,
    )

    assert (
        installed_boot.mode
        == InstallerMode.INSTALLED_BOOT_WINDOW
    )
    assert installed_boot.access_point_required is True
    assert installed_boot.service_window_seconds == 60

    wifi_change = determine_installer_state(
        cloud_identity_valid=True,
        cloud_identity_missing=False,
        cloud_identity_recovery=False,
        service_window_active=True,
        wifi_maintenance_requested=True,
    )

    assert (
        wifi_change.mode
        == InstallerMode.WIFI_MAINTENANCE
    )
    assert wifi_change.service_window_seconds is None

    normal_run = determine_installer_state(
        cloud_identity_valid=True,
        cloud_identity_missing=False,
        cloud_identity_recovery=False,
        service_window_active=False,
    )

    assert (
        normal_run.mode
        == InstallerMode.INSTALLED_RUN
    )
    assert normal_run.access_point_required is False

    broken_identity = evaluate_cloud_identity(
        identity_exists=True,
        identity_payload={
            "device_uuid": "device-test",
        },
    )

    assert broken_identity["recovery"] is True
    assert "device_token" in broken_identity[
        "missing_fields"
    ]

    recovery = determine_installer_state(
        cloud_identity_valid=False,
        cloud_identity_missing=False,
        cloud_identity_recovery=True,
        service_window_active=False,
    )

    assert recovery.mode == InstallerMode.RECOVERY
    assert recovery.access_point_required is True

    wired = select_wired_uplink([
        NetworkInterfaceState(
            name="wlan0",
            kind="wifi",
            connected=True,
            ip_address="192.168.4.1",
            default_route=False,
        ),
        NetworkInterfaceState(
            name="end0",
            kind="ethernet",
            connected=True,
            ip_address="192.168.0.5",
            default_route=True,
        ),
    ])

    assert wired is not None
    assert wired.name == "end0"

    no_wired = select_wired_uplink([
        NetworkInterfaceState(
            name="wlan0",
            kind="wifi",
            connected=True,
            ip_address="192.168.4.1",
        ),
    ])

    assert no_wired is None

    print("Installer V2 self-test: OK")


if __name__ == "__main__":
    run()