import json
import logging
import os
import time
from pathlib import Path
from urllib import error, request

from device_config import load_cached_cloud_config
from sync_signal import request_config_sync


CONFIG_PATH = Path(
    os.getenv(
        "IQF_DEVICE_CONFIG_PATH",
        "/data/device.json",
    )
)

DEFAULT_API_BASE_URL = "https://api.tngiqfanda.cz"
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 60
DEFAULT_SOFTWARE_VERSION = "0.1.0"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def load_device_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Konfigurační soubor neexistuje: {CONFIG_PATH}"
        )

    with CONFIG_PATH.open("r", encoding="utf-8-sig") as file:
        config = json.load(file)

    required_fields = (
        "device_uuid",
        "device_token",
    )

    for field in required_fields:
        if not config.get(field):
            raise ValueError(
                f"V konfiguraci chybí povinné pole: {field}"
            )

    return config


def get_api_url(config: dict) -> str:
    api_base_url = config.get(
        "api_base_url",
        DEFAULT_API_BASE_URL,
    ).rstrip("/")

    return f"{api_base_url}/api/v1/device/heartbeat"


def get_heartbeat_interval(config: dict) -> int:
    try:
        cloud_config = load_cached_cloud_config()
    except Exception:
        logging.exception(
            "Nepodařilo se načíst interval z cloudové konfigurace."
        )
        cloud_config = None

    if cloud_config:
        value = cloud_config.get(
            "heartbeat_interval_seconds",
            DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        )
    else:
        value = config.get(
            "heartbeat_interval_seconds",
            DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        )

    try:
        interval = int(value)
    except (TypeError, ValueError):
        logging.warning(
            "Neplatný interval heartbeatu %r, používám %s sekund.",
            value,
            DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        )
        return DEFAULT_HEARTBEAT_INTERVAL_SECONDS

    return max(interval, 10)


def send_heartbeat(config: dict) -> dict:
    payload = {
        "device_uuid": config["device_uuid"],
        "status": "online",
        "software_version": config.get(
            "software_version",
            DEFAULT_SOFTWARE_VERSION,
        ),
    }

    encoded_payload = json.dumps(payload).encode("utf-8")

    http_request = request.Request(
        get_api_url(config),
        data=encoded_payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {config['device_token']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "TNG-IQ-FANDA-Agent",
        },
    )

    with request.urlopen(http_request, timeout=15) as response:
        response_body = response.read().decode(
            "utf-8",
            errors="strict",
        )

        response_data = json.loads(response_body)

        logging.info(
            "Heartbeat úspěšný, HTTP %s: %s",
            response.status,
            response_body,
        )

        return response_data


def get_local_config_version() -> int | None:
    try:
        cloud_config = load_cached_cloud_config()
    except Exception:
        logging.exception(
            "Načtení lokální verze konfigurace selhalo."
        )
        return None

    if not cloud_config:
        return None

    try:
        return int(cloud_config.get("config_version"))
    except (TypeError, ValueError):
        return None


def process_config_version(heartbeat_response: dict) -> None:
    raw_server_version = heartbeat_response.get("config_version")

    try:
        server_version = int(raw_server_version)
    except (TypeError, ValueError):
        logging.warning(
            "Heartbeat neobsahuje platnou config_version: %r",
            raw_server_version,
        )
        return

    local_version = get_local_config_version()

    if local_version == server_version:
        logging.debug(
            "Konfigurace je aktuální, verze: %s.",
            local_version,
        )
        return

    logging.info(
        "Zjištěna nová konfigurace. "
        "Lokální verze: %s, serverová verze: %s.",
        local_version,
        server_version,
    )

    request_config_sync()


def main() -> None:
    logging.info(
        "IQ FANDA heartbeat agent spuštěn. Konfigurace: %s",
        CONFIG_PATH,
    )

    while True:
        interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS

        try:
            config = load_device_config()
            interval = get_heartbeat_interval(config)

            heartbeat_response = send_heartbeat(config)
            process_config_version(heartbeat_response)

            next_interval = heartbeat_response.get(
                "next_heartbeat_seconds"
            )

            if next_interval is not None:
                try:
                    interval = max(int(next_interval), 10)
                except (TypeError, ValueError):
                    logging.warning(
                        "Server vrátil neplatný heartbeat interval: %r",
                        next_interval,
                    )

        except error.HTTPError as exc:
            response_body = exc.read().decode(
                "utf-8",
                errors="replace",
            )

            logging.error(
                "Heartbeat odmítnut, HTTP %s: %s",
                exc.code,
                response_body,
            )

        except error.URLError as exc:
            logging.warning(
                "Cloud není dostupný: %s",
                exc.reason,
            )

        except FileNotFoundError as exc:
            logging.warning("%s", exc)

        except ValueError as exc:
            logging.error("%s", exc)

        except Exception:
            logging.exception(
                "Heartbeat skončil neočekávanou chybou."
            )

        time.sleep(interval)


if __name__ == "__main__":
    main()

