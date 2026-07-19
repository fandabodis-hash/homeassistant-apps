import json
import logging
import os
import tempfile
import time
from pathlib import Path
from urllib import error, request

from sync_signal import wait_for_config_sync


DEVICE_CONFIG_PATH = Path(
    os.getenv(
        "IQF_DEVICE_CONFIG_PATH",
        "/data/device.json",
    )
)

CLOUD_CONFIG_PATH = Path(
    os.getenv(
        "IQF_CLOUD_CONFIG_PATH",
        "/data/cloud-config.json",
    )
)

DEFAULT_API_BASE_URL = "https://api.tngiqfanda.cz"
DEFAULT_SYNC_INTERVAL_SECONDS = 300
ERROR_RETRY_INTERVAL_SECONDS = 60
MIN_SYNC_INTERVAL_SECONDS = 30


def load_device_identity() -> dict:
    if not DEVICE_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Konfigurační soubor zařízení neexistuje: "
            f"{DEVICE_CONFIG_PATH}"
        )

    with DEVICE_CONFIG_PATH.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        config = json.load(file)

    for field in ("device_uuid", "device_token"):
        if not config.get(field):
            raise ValueError(
                f"V konfiguraci zařízení chybí pole: {field}"
            )

    return config


def get_config_url(device_identity: dict) -> str:
    api_base_url = device_identity.get(
        "api_base_url",
        DEFAULT_API_BASE_URL,
    ).rstrip("/")

    return f"{api_base_url}/api/v1/device/config"


def fetch_cloud_config(device_identity: dict) -> dict:
    http_request = request.Request(
        get_config_url(device_identity),
        method="GET",
        headers={
            "Authorization": (
                f"Bearer {device_identity['device_token']}"
            ),
            "Accept": "application/json",
            "User-Agent": "TNG-IQ-FANDA-Agent",
        },
    )

    with request.urlopen(
        http_request,
        timeout=15,
    ) as response:
        response_body = response.read().decode(
            "utf-8",
            errors="strict",
        )

    cloud_config = json.loads(response_body)

    expected_uuid = device_identity["device_uuid"]
    received_uuid = cloud_config.get("device_uuid")

    if received_uuid != expected_uuid:
        raise ValueError(
            "Device UUID v cloudové konfiguraci neodpovídá "
            "lokálnímu zařízení."
        )

    return cloud_config


def save_cloud_config(cloud_config: dict) -> None:
    CLOUD_CONFIG_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix="cloud-config-",
        suffix=".tmp",
        dir=CLOUD_CONFIG_PATH.parent,
    )

    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                cloud_config,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        temporary_path.replace(CLOUD_CONFIG_PATH)

    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def load_cached_cloud_config() -> dict | None:
    if not CLOUD_CONFIG_PATH.exists():
        return None

    with CLOUD_CONFIG_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def get_sync_interval(cloud_config: dict | None) -> int:
    if not cloud_config:
        return DEFAULT_SYNC_INTERVAL_SECONDS

    raw_interval = cloud_config.get(
        "sync_interval_seconds",
        DEFAULT_SYNC_INTERVAL_SECONDS,
    )

    try:
        interval = int(raw_interval)
    except (TypeError, ValueError):
        logging.warning(
            "Neplatný sync_interval_seconds: %r. "
            "Používám výchozí hodnotu %s sekund.",
            raw_interval,
            DEFAULT_SYNC_INTERVAL_SECONDS,
        )
        return DEFAULT_SYNC_INTERVAL_SECONDS

    return max(interval, MIN_SYNC_INTERVAL_SECONDS)


def sync_device_config() -> dict:
    device_identity = load_device_identity()
    cloud_config = fetch_cloud_config(device_identity)
    save_cloud_config(cloud_config)

    logging.info(
        "Cloudová konfigurace stažena. Verze: %s, moduly: %s",
        cloud_config.get("config_version"),
        cloud_config.get("active_modules", []),
    )

    return cloud_config


def sync_once() -> tuple[dict | None, bool]:
    try:
        cloud_config = sync_device_config()
        return cloud_config, True

    except error.HTTPError as exc:
        response_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        logging.error(
            "Stažení konfigurace odmítnuto, HTTP %s: %s",
            exc.code,
            response_body,
        )

    except error.URLError as exc:
        logging.warning(
            "Cloud není při synchronizaci konfigurace dostupný: %s",
            exc.reason,
        )

    except Exception:
        logging.exception(
            "Synchronizace cloudové konfigurace selhala."
        )

    try:
        cached_config = load_cached_cloud_config()

        if cached_config:
            logging.info(
                "Používám poslední uloženou cloudovou konfiguraci, "
                "verze: %s.",
                cached_config.get("config_version"),
            )

        return cached_config, False

    except Exception:
        logging.exception(
            "Načtení uložené cloudové konfigurace selhalo."
        )
        return None, False


def main() -> None:
    logging.info(
        "Služba synchronizace cloudové konfigurace spuštěna."
    )

    while True:
        cloud_config, success = sync_once()

        if success:
            interval = get_sync_interval(cloud_config)
        else:
            interval = ERROR_RETRY_INTERVAL_SECONDS

        logging.info(
            "Další synchronizace cloudové konfigurace za %s sekund.",
            interval,
        )

        sync_requested = wait_for_config_sync(interval)

        if sync_requested:
            logging.info(
                "Synchronizace konfigurace vyžádána heartbeatem."
            )


if __name__ == "__main__":
    main()


