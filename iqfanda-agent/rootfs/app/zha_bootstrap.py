"""Automaticky bootstrap ZHA pro TNG IQ FANDA."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from zigbee_manager import (
    HomeAssistantApiError,
    get_zha_telemetry_snapshot,
    home_assistant_request,
)


COMMUNICATION_STATE_PATH = Path(
    os.getenv(
        "IQF_COMMUNICATION_STATE_PATH",
        "/config/communication.json",
    )
)

WAIT_SECONDS = 5
FLOW_POLL_SECONDS = 1
FLOW_TIMEOUT_SECONDS = 180
RUNTIME_TIMEOUT_SECONDS = 120

logger = logging.getLogger(__name__)


def nacti_zha_config_entries() -> list[dict[str, Any]]:
    """Nacte ZHA config entries z Home Assistantu."""

    result = home_assistant_request(
        path="/config/config_entries/entry?domain=zha",
    )

    if not isinstance(result, list):
        raise HomeAssistantApiError(
            "Home Assistant nevratil platny seznam "
            "ZHA config entries."
        )

    return [
        entry
        for entry in result
        if (
            isinstance(entry, dict)
            and entry.get("domain") == "zha"
        )
    ]


def najdi_pripravene_koordinatory() -> list[dict[str, Any]]:
    """
    Najde pripravene Zigbee koordinatory.

    Pouzije pouze stabilni /dev/serial/by-id cestu.
    """

    if not COMMUNICATION_STATE_PATH.exists():
        return []

    try:
        with COMMUNICATION_STATE_PATH.open(
            "r",
            encoding="utf-8-sig",
        ) as soubor:
            stav = json.load(soubor)

    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise RuntimeError(
            "Komunikacni stav nelze nacist."
        ) from exc

    if not isinstance(stav, dict):
        raise RuntimeError(
            "Komunikacni stav nema platny format."
        )

    komunikatory = stav.get("communicators")

    if not isinstance(komunikatory, list):
        return []

    vysledky: list[dict[str, Any]] = []

    for komunikator in komunikatory:
        if not isinstance(komunikator, dict):
            continue

        if (
            komunikator.get("type")
            != "zigbee_coordinator"
        ):
            continue

        if komunikator.get("connected") is not True:
            continue

        if komunikator.get("status") != "ready":
            continue

        device_path = str(
            komunikator.get("preferred_path")
            or ""
        ).strip()

        if not device_path:
            stable_paths = komunikator.get(
                "stable_paths"
            )

            if isinstance(stable_paths, list):
                device_path = next(
                    (
                        str(path).strip()
                        for path in stable_paths
                        if str(path or "").strip()
                    ),
                    "",
                )

        if not device_path.startswith(
            "/dev/serial/by-id/"
        ):
            raise RuntimeError(
                "Zigbee koordinator nema stabilni "
                "/dev/serial/by-id cestu."
            )

        vysledky.append(
            {
                **komunikator,
                "preferred_path": device_path,
            }
        )

    return vysledky


def _flow_request(
    flow_id: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Provede jeden krok existujiciho config flow."""

    result = home_assistant_request(
        path=(
            "/config/config_entries/flow/"
            f"{flow_id}"
        ),
        method=method,
        payload=payload,
    )

    if not isinstance(result, dict):
        raise HomeAssistantApiError(
            "Home Assistant vratil neplatny stav "
            "ZHA config flow."
        )

    return result


def _abort_flow(
    flow_id: str,
) -> None:
    """Zrusi nedokonceny config flow."""

    try:
        home_assistant_request(
            path=(
                "/config/config_entries/flow/"
                f"{flow_id}"
            ),
            method="DELETE",
        )

    except HomeAssistantApiError:
        logger.warning(
            "Nedokonceny ZHA config flow %s "
            "se nepodarilo zrusit.",
            flow_id,
        )


def vytvor_zha_config_entry(
    device_path: str,
) -> dict[str, Any]:
    """
    Vytvori ZHA pres oficialni Home Assistant config flow.

    Typ radia se neurcuje rucne.
    """

    normalized_device_path = str(
        device_path or ""
    ).strip()

    if not normalized_device_path.startswith(
        "/dev/serial/by-id/"
    ):
        raise ValueError(
            "ZHA bootstrap vyzaduje stabilni "
            "/dev/serial/by-id cestu."
        )

    result = home_assistant_request(
        path="/config/config_entries/flow",
        method="POST",
        payload={
            "handler": "zha",
        },
    )

    if not isinstance(result, dict):
        raise HomeAssistantApiError(
            "Home Assistant nevytvoril platny "
            "ZHA config flow."
        )

    flow_id = str(
        result.get("flow_id")
        or ""
    ).strip()

    if not flow_id:
        raise HomeAssistantApiError(
            "ZHA config flow nema flow_id."
        )

    started_at = time.monotonic()

    try:
        while True:
            result_type = str(
                result.get("type")
                or ""
            ).strip()

            step_id = str(
                result.get("step_id")
                or ""
            ).strip()

            if result_type == "create_entry":
                return result

            if result_type == "abort":
                raise HomeAssistantApiError(
                    "ZHA config flow byl ukoncen: "
                    f"{result.get('reason') or 'neznamy duvod'}"
                )

            if result_type == "form":
                if step_id == "choose_serial_port":
                    result = _flow_request(
                        flow_id,
                        method="POST",
                        payload={
                            "path": (
                                normalized_device_path
                            ),
                        },
                    )
                    continue

                if step_id == "verify_radio":
                    result = _flow_request(
                        flow_id,
                        method="POST",
                        payload={},
                    )
                    continue

                if (
                    step_id
                    == "manual_pick_radio_type"
                ):
                    raise HomeAssistantApiError(
                        "Autodetekce typu Zigbee radia "
                        "selhala. Rucni odhad radia "
                        "nebude proveden."
                    )

                raise HomeAssistantApiError(
                    "Neocekavany formular ZHA flow: "
                    f"{step_id or 'bez step_id'}."
                )

            if result_type == "menu":
                menu_options = list(
                    result.get("menu_options")
                    or []
                )

                if (
                    step_id
                    == "choose_setup_strategy"
                ):
                    recommended = (
                        "setup_strategy_recommended"
                    )

                    if (
                        recommended
                        not in menu_options
                    ):
                        raise HomeAssistantApiError(
                            "ZHA flow nenabizi "
                            "doporucenou strategii."
                        )

                    result = _flow_request(
                        flow_id,
                        method="POST",
                        payload={
                            "next_step_id": (
                                recommended
                            ),
                        },
                    )
                    continue

                if (
                    step_id
                    == "choose_migration_strategy"
                ):
                    raise HomeAssistantApiError(
                        "Behem bootstrapu se objevila "
                        "existujici ZHA konfigurace. "
                        "Automaticka migrace nebyla "
                        "provedena."
                    )

                raise HomeAssistantApiError(
                    "Neocekavane menu ZHA flow: "
                    f"{step_id or 'bez step_id'}."
                )

            if result_type == "progress":
                if (
                    time.monotonic()
                    - started_at
                    > FLOW_TIMEOUT_SECONDS
                ):
                    raise HomeAssistantApiError(
                        "ZHA config flow prekrocil "
                        "casovy limit."
                    )

                time.sleep(
                    FLOW_POLL_SECONDS
                )

                result = _flow_request(
                    flow_id
                )
                continue

            raise HomeAssistantApiError(
                "Neocekavany typ ZHA flow: "
                f"{result_type or 'prazdny'}."
            )

    except Exception:
        _abort_flow(
            flow_id
        )
        raise


def cekej_na_zha_runtime() -> dict[str, Any]:
    """
    Pocka na aktivni ZHA runtime.

    Config entry musi byt prave jedna a
    ZHA WebSocket musi vratit coordinator.
    """

    deadline = (
        time.monotonic()
        + RUNTIME_TIMEOUT_SECONDS
    )

    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            entries = (
                nacti_zha_config_entries()
            )

            if len(entries) != 1:
                raise RuntimeError(
                    "Po bootstrapu neni prave "
                    "jedna ZHA config entry."
                )

            snapshot = (
                get_zha_telemetry_snapshot()
            )

            if (
                snapshot.get(
                    "network_initialized"
                )
                is True
            ):
                return snapshot

        except (
            HomeAssistantApiError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            last_error = exc

        time.sleep(
            FLOW_POLL_SECONDS
        )

    raise HomeAssistantApiError(
        "ZHA config entry vznikla, "
        "ale runtime se neinicializoval. "
        f"Posledni chyba: {last_error}"
    )


def _park() -> None:
    """
    Udrzi bootstrap vlakno aktivni.

    Po uspechu nebo konfliktu uz nic dalsiho
    automaticky nekonfiguruje.
    """

    while True:
        time.sleep(3600)


def main() -> None:
    """
    Jednorazove zajisti ZHA.

    Pokud ZHA jiz existuje, nic nemeni.
    """

    logger.info(
        "ZHA Bootstrap spusten."
    )

    while True:
        try:
            entries = (
                nacti_zha_config_entries()
            )

        except HomeAssistantApiError as exc:
            logger.info(
                "ZHA Bootstrap ceka "
                "na Home Assistant: %s",
                exc,
            )

            time.sleep(
                WAIT_SECONDS
            )
            continue

        if len(entries) > 1:
            logger.error(
                "ZHA Bootstrap zastaven: "
                "nalezeno %s ZHA config entries.",
                len(entries),
            )
            _park()

        if len(entries) == 1:
            logger.info(
                "ZHA Bootstrap: ZHA jiz existuje. "
                "Nic se nevytvari ani neresetuje."
            )
            _park()

        try:
            koordinatory = (
                najdi_pripravene_koordinatory()
            )

        except RuntimeError as exc:
            logger.error(
                "ZHA Bootstrap zastaven: %s",
                exc,
            )
            _park()

        if not koordinatory:
            logger.info(
                "ZHA Bootstrap ceka "
                "na pripraveny Zigbee koordinator."
            )

            time.sleep(
                WAIT_SECONDS
            )
            continue

        if len(koordinatory) > 1:
            logger.error(
                "ZHA Bootstrap zastaven: "
                "nalezeno %s pripravenych "
                "Zigbee koordinatoru.",
                len(koordinatory),
            )
            _park()

        koordinator = koordinatory[0]

        device_path = str(
            koordinator[
                "preferred_path"
            ]
        )

        serial_number = str(
            koordinator.get(
                "serial_number"
            )
            or "neuveden"
        )

        logger.info(
            "ZHA Bootstrap vytvari novou "
            "Zigbee sit. Koordinator "
            "serial=%s, port=%s.",
            serial_number,
            device_path,
        )

        try:
            vytvor_zha_config_entry(
                device_path
            )

            snapshot = (
                cekej_na_zha_runtime()
            )

        except Exception:
            logger.exception(
                "ZHA Bootstrap selhal. "
                "Dalsi automaticky pokus "
                "nebude proveden do restartu "
                "Agenta."
            )
            _park()

        logger.info(
            "ZHA Bootstrap dokoncen. "
            "Zigbee sit je inicializovana, "
            "zarizeni=%s.",
            snapshot.get(
                "device_count",
                0,
            ),
        )

        _park()


if __name__ == "__main__":
    main()
