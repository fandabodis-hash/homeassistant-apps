"""Periodicky sber a odesilani telemetrie modulu."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from communication.fve_entity_mapper import (
    vytvorit_goodwe_fve_entity,
)
from communication.goodwe_probe import (
    read_goodwe_et_snapshot,
)
from device_config import (
    load_cached_cloud_config,
    load_device_identity,
)
from host.cloud_client import cloud_client


DEFAULT_TELEMETRY_INTERVAL_SECONDS = 60
MIN_TELEMETRY_INTERVAL_SECONDS = 5
MAX_TELEMETRY_INTERVAL_SECONDS = 3600
ERROR_RETRY_INTERVAL_SECONDS = 15


def ziskej_interval_telemetrie(
    konfigurace: dict[str, Any] | None,
) -> int:
    """Vrati bezpecne omezeny interval telemetrie."""
    if not isinstance(konfigurace, dict):
        return DEFAULT_TELEMETRY_INTERVAL_SECONDS

    raw_interval = konfigurace.get(
        "telemetry_interval_seconds",
        DEFAULT_TELEMETRY_INTERVAL_SECONDS,
    )

    try:
        interval = int(raw_interval)
    except (TypeError, ValueError):
        logging.warning(
            "Neplatny telemetry_interval_seconds: %r. "
            "Pouzivam %s sekund.",
            raw_interval,
            DEFAULT_TELEMETRY_INTERVAL_SECONDS,
        )
        return DEFAULT_TELEMETRY_INTERVAL_SECONDS

    return max(
        MIN_TELEMETRY_INTERVAL_SECONDS,
        min(interval, MAX_TELEMETRY_INTERVAL_SECONDS),
    )


def najdi_goodwe_fve_runtime(
    cloud_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Najde povolenou read-only GoodWe FVE konfiguraci."""
    runtime_configurations = cloud_config.get(
        "module_runtime_configurations"
    )

    if not isinstance(runtime_configurations, list):
        return None

    for runtime_configuration in runtime_configurations:
        if not isinstance(runtime_configuration, dict):
            continue

        module_key = str(
            runtime_configuration.get("module_key") or ""
        ).strip().lower()

        manufacturer = str(
            runtime_configuration.get("manufacturer") or ""
        ).strip().lower()

        if (
            module_key == "photovoltaic"
            and manufacturer == "goodwe"
            and runtime_configuration.get(
                "telemetry_enabled"
            )
            is True
            and runtime_configuration.get("read_only") is True
        ):
            return runtime_configuration

    return None


def vytvorit_cas_snapshotu() -> str:
    """Vrati aktualni UTC cas ve formatu ISO 8601."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def odeslat_telemetrii_jednou() -> int:
    """Nacte a odesle jeden snapshot FVE telemetrie."""
    identity = load_device_identity()
    cloud_config = load_cached_cloud_config()

    if not isinstance(cloud_config, dict):
        raise RuntimeError(
            "Cloudova konfigurace zatim neni dostupna."
        )

    local_interval = ziskej_interval_telemetrie(
        cloud_config
    )

    runtime_configuration = najdi_goodwe_fve_runtime(
        cloud_config
    )

    if runtime_configuration is None:
        logging.info(
            "Aktivni GoodWe FVE telemetrie neni "
            "v cloudove konfiguraci povolena."
        )
        return local_interval

    snapshot = read_goodwe_et_snapshot(
        runtime_configuration
    )

    if not isinstance(snapshot, dict):
        raise RuntimeError(
            "GoodWe runtime reader nevratil platny snapshot."
        )

    entities = vytvorit_goodwe_fve_entity(snapshot)

    if not isinstance(entities, list) or not entities:
        raise RuntimeError(
            "Mapper GoodWe nevratil zadne FVE entity."
        )

    captured_at = vytvorit_cas_snapshotu()
    snapshot_complete = snapshot.get("complete") is True

    response = cloud_client.submit_module_telemetry(
        device_uuid=str(identity["device_uuid"]),
        device_token=str(identity["device_token"]),
        module_key="photovoltaic",
        source="goodwe_rs485",
        captured_at=captured_at,
        entities=entities,
        snapshot_complete=snapshot_complete,
    )

    if not isinstance(response, dict) or not response.get("ok"):
        status_code = (
            response.get("status_code")
            if isinstance(response, dict)
            else None
        )
        error = (
            response.get("error")
            if isinstance(response, dict)
            else "Neplatna odpoved cloudoveho klienta."
        )

        raise RuntimeError(
            "Odeslani FVE telemetrie selhalo. "
            f"HTTP: {status_code}, chyba: {error}"
        )

    response_data = response.get("data")

    if not isinstance(response_data, dict):
        response_data = {}

    next_interval = ziskej_interval_telemetrie(
        response_data
        if "telemetry_interval_seconds" in response_data
        else cloud_config
    )

    logging.info(
        "FVE telemetrie odeslana. "
        "Prijato: %s, aktualizovano: %s, "
        "uplny snapshot: %s, dalsi odeslani za %s s.",
        response_data.get(
            "entities_received",
            len(entities),
        ),
        response_data.get(
            "entities_updated",
            len(entities),
        ),
        snapshot_complete,
        next_interval,
    )

    return next_interval


def main() -> None:
    """Spusti nekonecnou sluzbu telemetrie modulu."""
    logging.info(
        "Sluzba telemetrie modulu byla spustena."
    )

    while True:
        try:
            interval = odeslat_telemetrii_jednou()
        except Exception as exc:
            logging.warning(
                "Cyklus telemetrie modulu selhal: %s",
                exc,
            )
            interval = ERROR_RETRY_INTERVAL_SECONDS

        time.sleep(interval)


if __name__ == "__main__":
    main()
