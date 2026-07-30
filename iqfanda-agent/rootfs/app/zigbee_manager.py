import json
import logging
import os
from typing import Any
from urllib import error, request


SUPERVISOR_CORE_API_URL = (
    "http://supervisor/core/api"
)

REQUEST_TIMEOUT_SECONDS = 15


class HomeAssistantApiError(RuntimeError):
    """Chyba komunikace s Home Assistant Core API."""


def get_supervisor_token() -> str:
    """Vrati token prideleny add-onu Supervisorem."""

    token = os.getenv(
        "SUPERVISOR_TOKEN",
        "",
    ).strip()

    if not token:
        raise HomeAssistantApiError(
            "Promenna SUPERVISOR_TOKEN neni dostupna."
        )

    return token


def home_assistant_request(
    *,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    """Provede pozadavek na Home Assistant Core API."""

    normalized_path = "/" + path.lstrip("/")
    url = SUPERVISOR_CORE_API_URL + normalized_path

    data = None

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    http_request = request.Request(
        url=url,
        data=data,
        method=method,
        headers={
            "Authorization": (
                f"Bearer {get_supervisor_token()}"
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "TNG-IQ-FANDA-Agent",
        },
    )

    try:
        with request.urlopen(
            http_request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            response_body = response.read().decode(
                "utf-8",
                errors="strict",
            )

            if not response_body.strip():
                return None

            return json.loads(response_body)

    except error.HTTPError as exc:
        response_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise HomeAssistantApiError(
            f"Home Assistant API vratilo HTTP "
            f"{exc.code}: {response_body}"
        ) from exc

    except error.URLError as exc:
        raise HomeAssistantApiError(
            "Home Assistant API neni dostupne: "
            f"{exc.reason}"
        ) from exc


def get_home_assistant_config() -> dict[str, Any]:
    """Overi dostupnost Home Assistant Core API."""

    result = home_assistant_request(
        path="/config",
    )

    if not isinstance(result, dict):
        raise HomeAssistantApiError(
            "Home Assistant vratil neplatnou konfiguraci."
        )

    return result


def get_home_assistant_services() -> list[dict[str, Any]]:
    """Nacte seznam dostupnych domen a sluzeb."""

    result = home_assistant_request(
        path="/services",
    )

    if not isinstance(result, list):
        raise HomeAssistantApiError(
            "Home Assistant vratil neplatny seznam sluzeb."
        )

    return result


def find_service_domain(
    services: list[dict[str, Any]],
    domain_name: str,
) -> dict[str, Any] | None:
    """Vyhleda domenu sluzeb podle nazvu."""

    for domain in services:
        if not isinstance(domain, dict):
            continue

        if domain.get("domain") == domain_name:
            return domain

    return None


def get_zigbee_runtime_status() -> dict[str, Any]:
    """
    Zjisti pouze pripravenost Zigbee vrstvy.

    Tato funkce nic neinstaluje, nemeni konfiguraci
    a neotevira parovani.
    """

    ha_config = get_home_assistant_config()
    services = get_home_assistant_services()

    zha_domain = find_service_domain(
        services,
        "zha",
    )

    zha_services: list[str] = []

    if zha_domain:
        raw_services = zha_domain.get(
            "services",
            {},
        )

        if isinstance(raw_services, dict):
            zha_services = sorted(
                str(service_name)
                for service_name in raw_services
            )

    return {
        "home_assistant_available": True,
        "home_assistant_version": (
            ha_config.get("version")
        ),
        "location_name": (
            ha_config.get("location_name")
        ),
        "zha_service_domain_available": (
            zha_domain is not None
        ),
        "zha_services": zha_services,
        "zigbee_network_initialized": (
            zha_domain is not None
        ),
        "permit_join_available": (
            "permit" in zha_services
            or "permit_join" in zha_services
        ),
    }


def diagnostic_main() -> None:
    """Vypise diagnostiku bez zmeny systemu."""

    try:
        status = get_zigbee_runtime_status()

        logging.info(
            "Zigbee diagnostika: %s",
            json.dumps(
                status,
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    except Exception:
        logging.exception(
            "Zigbee diagnostika selhala."
        )
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    diagnostic_main()
