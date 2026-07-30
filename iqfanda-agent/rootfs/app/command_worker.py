"""Cloudovy vykonavatel prikazu TNG IQ FANDA Agentu."""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from host.cloud_client import cloud_client


DEVICE_CONFIG_PATH = Path(
    os.getenv(
        "IQF_DEVICE_CONFIG_PATH",
        "/config/device.json",
    )
)

CLAIM_INTERVAL_SECONDS = 2
ERROR_RETRY_SECONDS = 15


def load_device_identity() -> dict[str, Any]:
    """Nacte cloudovou identitu a token zarizeni."""

    if not DEVICE_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Konfiguracni soubor zarizeni neexistuje: "
            f"{DEVICE_CONFIG_PATH}"
        )

    with DEVICE_CONFIG_PATH.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        identity = json.load(file)

    for field_name in (
        "device_uuid",
        "device_token",
    ):
        if not identity.get(field_name):
            raise ValueError(
                f"V konfiguraci zarizeni chybi pole: "
                f"{field_name}"
            )

    return identity


def require_cloud_success(
    response: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    """Overi sjednocenou odpoved cloudoveho klienta."""

    if not isinstance(response, dict):
        raise RuntimeError(
            f"{operation}: cloudovy klient vratil "
            "neplatny format."
        )

    if not response.get("ok"):
        raise RuntimeError(
            f"{operation}: "
            f"{response.get('error') or 'neznamy problem'}"
        )

    data = response.get("data")

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise RuntimeError(
            f"{operation}: cloud vratil neplatna data."
        )

    return data


def claim_command(
    identity: dict[str, Any],
) -> dict[str, Any] | None:
    """Vyzvedne nejstarsi dostupny prikaz zarizeni."""

    response = cloud_client.claim_device_command(
        device_token=identity["device_token"],
    )

    data = require_cloud_success(
        response,
        "Vyzvednuti prikazu",
    )

    command = data.get("command")

    if command is None:
        return None

    if not isinstance(command, dict):
        raise ValueError(
            "Cloud vratil neplatny format prikazu."
        )

    for field_name in (
        "id",
        "command_type",
        "payload",
    ):
        if field_name not in command:
            raise ValueError(
                f"V prijatem prikazu chybi pole: "
                f"{field_name}"
            )

    return command


def submit_command_result(
    *,
    identity: dict[str, Any],
    command_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Odesle prubezny nebo konecny stav prikazu."""

    response = cloud_client.submit_device_command_result(
        device_token=identity["device_token"],
        command_id=command_id,
        status=status,
        result=result,
        error_message=error_message,
    )

    return require_cloud_success(
        response,
        f"Ulozeni stavu {status}",
    )


def execute_command(
    *,
    identity: dict[str, Any],
    command: dict[str, Any],
) -> None:
    """Provede bezpecny test celeho Command Engine."""

    command_id = str(command["id"])
    command_type = str(command["command_type"])
    command_payload = command.get("payload") or {}

    logging.info(
        "Prikaz prevzat. ID: %s, typ: %s, payload: %s",
        command_id,
        command_type,
        json.dumps(
            command_payload,
            ensure_ascii=False,
            sort_keys=True,
        ),
    )

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status="running",
        result={
            "worker": "command_worker",
            "phase": "validation",
        },
    )

    if command_type == "zigbee_permit_join":
        submit_command_result(
            identity=identity,
            command_id=command_id,
            status="failed",
            result={
                "worker": "command_worker",
                "executor": "zigbee",
                "phase": "validation_complete",
                "payload_received": command_payload,
            },
            error_message=(
                "Command Worker prikaz uspesne prevzal. "
                "Skutecny Zigbee permit join zatim neni "
                "v teto testovaci verzi aktivovan."
            ),
        )
        return

    submit_command_result(
        identity=identity,
        command_id=command_id,
        status="failed",
        result={
            "worker": "command_worker",
            "phase": "unsupported_command",
        },
        error_message=(
            f"Nepodporovany typ prikazu: {command_type}"
        ),
    )


def process_once() -> bool:
    """Provede jeden pokus o vyzvednuti prikazu."""

    identity = load_device_identity()
    command = claim_command(identity)

    if command is None:
        return False

    execute_command(
        identity=identity,
        command=command,
    )
    return True


def main() -> None:
    """Spusti nepretrzite zpracovani cloudovych prikazu."""

    logging.info(
        "Command Worker TNG IQ FANDA byl spusten."
    )

    while True:
        sleep_seconds = CLAIM_INTERVAL_SECONDS

        try:
            process_once()

        except (
            FileNotFoundError,
            ValueError,
            RuntimeError,
        ) as exc:
            logging.error("%s", exc)
            sleep_seconds = ERROR_RETRY_SECONDS

        except Exception:
            logging.exception(
                "Command Worker skoncil neocekavanou chybou."
            )
            sleep_seconds = ERROR_RETRY_SECONDS

        time.sleep(sleep_seconds)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    main()
