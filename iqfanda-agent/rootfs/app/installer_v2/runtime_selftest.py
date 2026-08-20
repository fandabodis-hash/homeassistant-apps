"""Self-test read-only runtime adapteru Installer V2."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from installer_v2.runtime_adapter import (
    build_runtime_snapshot,
)


def fake_network_reader() -> dict:
    return {
        "ok": True,
        "interfaces": [
            {
                "name": "wlan0",
                "type": "wifi",
                "state": "connected",
                "connected": True,
                "connection": "IQF Installer AP",
                "ip_address": "192.168.4.1/24",
            },
            {
                "name": "end0",
                "type": "ethernet",
                "state": "connected",
                "connected": True,
                "connection": "Supervisor end0",
                "ip_address": "192.168.0.5/24",
            },
        ],
    }


def fake_wifi_reader() -> dict:
    return {
        "ok": True,
        "connected": True,
        "interface": "wlan0",
        "connection_name": "IQF Installer AP",
        "ssid": "TNG_IQ_FANDA_TEST",
        "ip_address": "192.168.4.1/24",
    }


def run() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        device_path = (
            Path(temp_dir)
            / "device.json"
        )

        device_path.write_text(
            json.dumps({
                "device_uuid": "uuid-test",
                "device_token": "SECRET-NEVER-RETURN",
                "device_status": "installed",
                "site_id": "site-test",
                "device_id": "device-test",
            }),
            encoding="utf-8",
        )

        snapshot = build_runtime_snapshot(
            device_config_path=device_path,
            network_reader=fake_network_reader,
            wifi_reader=fake_wifi_reader,
            service_window_active=True,
        )

        assert snapshot[
            "installer"
        ]["mode"] == "installed_boot_window"

        assert snapshot[
            "identity"
        ]["evaluation"]["valid"] is True

        assert snapshot[
            "identity"
        ]["summary"][
            "device_token_present"
        ] is True

        snapshot_text = json.dumps(
            snapshot,
            ensure_ascii=False,
        )

        assert "SECRET-NEVER-RETURN" not in snapshot_text

        assert snapshot[
            "network"
        ]["wired_uplink"]["name"] == "end0"

        assert snapshot[
            "network"
        ]["wired_uplink"][
            "ip_address"
        ] == "192.168.0.5/24"

        assert snapshot[
            "wifi"
        ]["interface"] == "wlan0"

    print(
        "Installer V2 runtime adapter self-test: OK"
    )


if __name__ == "__main__":
    run()