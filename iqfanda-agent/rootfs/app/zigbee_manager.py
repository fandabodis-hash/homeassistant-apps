import json
import logging
import os
import time
from typing import Any
from urllib import error, request


SUPERVISOR_CORE_API_URL = (
    "http://supervisor/core/api"
)

REQUEST_TIMEOUT_SECONDS = 15
ZIGBEE_DISCOVERY_POLL_SECONDS = 2


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


def render_home_assistant_template(
    template: str,
) -> Any:
    """Vyrenderuje Home Assistant Jinja sablonu."""

    normalized_template = str(
        template or ""
    ).strip()

    if not normalized_template:
        raise ValueError(
            "Home Assistant sablona nesmi byt prazdna."
        )

    return home_assistant_request(
        path="/template",
        method="POST",
        payload={
            "template": normalized_template,
        },
    )


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


def get_home_assistant_states() -> list[dict[str, Any]]:
    """Nacte aktualni stavy vsech entit."""

    result = home_assistant_request(
        path="/states",
    )

    if not isinstance(result, list):
        raise HomeAssistantApiError(
            "Home Assistant vratil neplatny seznam entit."
        )

    return [
        item
        for item in result
        if isinstance(item, dict)
        and item.get("entity_id")
    ]


def get_home_assistant_entity_ids() -> set[str]:
    """Vrati mnozinu aktualne existujicich entity_id."""

    return {
        str(item["entity_id"])
        for item in get_home_assistant_states()
    }


def get_entity_state(
    entity_id: str,
) -> dict[str, Any]:
    """Nacte stav jedne konkretni entity."""

    normalized_entity_id = str(
        entity_id or ""
    ).strip()

    if not normalized_entity_id:
        raise ValueError(
            "Entity ID nesmi byt prazdne."
        )

    result = home_assistant_request(
        path=f"/states/{normalized_entity_id}",
    )

    if not isinstance(result, dict):
        raise HomeAssistantApiError(
            "Home Assistant vratil neplatny stav entity "
            f"{normalized_entity_id}."
        )

    return result


def get_entity_device_id(
    entity_id: str,
) -> str | None:
    """Zjisti HA device_id, ke kteremu entita patri."""

    normalized_entity_id = str(
        entity_id or ""
    ).strip()

    if not normalized_entity_id:
        raise ValueError(
            "Entity ID nesmi byt prazdne."
        )

    template = (
        "{{ device_id("
        + json.dumps(normalized_entity_id)
        + ") | default('', true) }}"
    )

    result = render_home_assistant_template(
        template
    )

    if result is None:
        return None

    normalized_device_id = str(result).strip()

    return normalized_device_id or None


def get_device_entity_ids(
    device_id: str,
) -> list[str]:
    """Nacte vsechny entity prirazene k HA zarizeni."""

    normalized_device_id = str(
        device_id or ""
    ).strip()

    if not normalized_device_id:
        raise ValueError(
            "Device ID nesmi byt prazdne."
        )

    template = (
        "{{ device_entities("
        + json.dumps(normalized_device_id)
        + ") | tojson }}"
    )

    result = render_home_assistant_template(
        template
    )

    if isinstance(result, list):
        entity_ids = result

    elif isinstance(result, str):
        try:
            entity_ids = json.loads(result)
        except json.JSONDecodeError as exc:
            raise HomeAssistantApiError(
                "Home Assistant vratil neplatny seznam "
                "entit zarizeni."
            ) from exc

    else:
        raise HomeAssistantApiError(
            "Home Assistant vratil neocekavany "
            "format entit zarizeni."
        )

    if not isinstance(entity_ids, list):
        raise HomeAssistantApiError(
            "Seznam entit zarizeni nema platny format."
        )

    return sorted(
        {
            str(entity_id).strip()
            for entity_id in entity_ids
            if str(entity_id or "").strip()
        }
    )


def get_device_metadata(
    device_id: str,
) -> dict[str, Any]:
    """Nacte zakladni metadata HA zarizeni."""

    normalized_device_id = str(
        device_id or ""
    ).strip()

    if not normalized_device_id:
        raise ValueError(
            "Device ID nesmi byt prazdne."
        )

    template = """
{% set device_id_value = DEVICE_ID %}
{{
  {
    "name": (
      device_attr(device_id_value, "name_by_user")
      or device_attr(device_id_value, "name")
    ),
    "manufacturer": device_attr(
      device_id_value,
      "manufacturer"
    ),
    "model": device_attr(
      device_id_value,
      "model"
    ),
    "sw_version": device_attr(
      device_id_value,
      "sw_version"
    ),
    "hw_version": device_attr(
      device_id_value,
      "hw_version"
    )
  } | tojson
}}
""".replace(
        "DEVICE_ID",
        json.dumps(normalized_device_id),
    )

    result = render_home_assistant_template(
        template
    )

    if isinstance(result, dict):
        metadata = result

    elif isinstance(result, str):
        try:
            metadata = json.loads(result)
        except json.JSONDecodeError as exc:
            raise HomeAssistantApiError(
                "Home Assistant vratil neplatna "
                "metadata zarizeni."
            ) from exc

    else:
        raise HomeAssistantApiError(
            "Home Assistant vratil neocekavany "
            "format metadat zarizeni."
        )

    if not isinstance(metadata, dict):
        raise HomeAssistantApiError(
            "Metadata zarizeni nemaji platny format."
        )

    return {
        "device_id": normalized_device_id,
        "name": metadata.get("name"),
        "manufacturer": metadata.get("manufacturer"),
        "model": metadata.get("model"),
        "sw_version": metadata.get("sw_version"),
        "hw_version": metadata.get("hw_version"),
    }


def build_entity_inventory(
    device_id: str,
) -> list[dict[str, Any]]:
    """Sestavi inventar vsech entit jednoho HA zarizeni."""

    inventory: list[dict[str, Any]] = []

    for entity_id in get_device_entity_ids(
        device_id
    ):
        try:
            state = get_entity_state(
                entity_id
            )

            attributes = state.get(
                "attributes",
                {},
            )

            if not isinstance(attributes, dict):
                attributes = {}

            inventory.append(
                {
                    "entity_id": entity_id,
                    "state": state.get("state"),
                    "friendly_name": attributes.get(
                        "friendly_name"
                    ),
                    "device_class": attributes.get(
                        "device_class"
                    ),
                    "state_class": attributes.get(
                        "state_class"
                    ),
                    "unit_of_measurement": attributes.get(
                        "unit_of_measurement"
                    ),
                    "attributes": attributes,
                    "last_changed": state.get(
                        "last_changed"
                    ),
                    "last_updated": state.get(
                        "last_updated"
                    ),
                }
            )

        except HomeAssistantApiError as exc:
            inventory.append(
                {
                    "entity_id": entity_id,
                    "state": None,
                    "error": str(exc),
                }
            )

    return inventory


def find_entity_by_device_class(
    entities: list[dict[str, Any]],
    device_class: str,
) -> dict[str, Any] | None:
    """Najde prvni platnou entitu podle device_class."""

    normalized_device_class = str(
        device_class or ""
    ).strip().lower()

    for entity in entities:
        if not isinstance(entity, dict):
            continue

        entity_device_class = str(
            entity.get("device_class") or ""
        ).strip().lower()

        if entity_device_class != normalized_device_class:
            continue

        state = str(
            entity.get("state") or ""
        ).strip().lower()

        if state in {
            "",
            "unknown",
            "unavailable",
            "none",
        }:
            continue

        return entity

    return None


def wait_for_new_device(
    *,
    entity_ids_before: set[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    """
    Ceka na novou entitu a vrati kompletni inventar
    noveho HA zarizeni.
    """

    try:
        timeout = int(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Cas cekani musi byt cele cislo."
        ) from exc

    if timeout < 1:
        raise ValueError(
            "Cas cekani musi byt vetsi nez nula."
        )

    deadline = time.monotonic() + timeout

    detected_device_id: str | None = None

    while time.monotonic() < deadline:
        current_entity_ids = (
            get_home_assistant_entity_ids()
        )

        new_entity_ids = sorted(
            current_entity_ids
            - set(entity_ids_before)
        )

        for entity_id in new_entity_ids:
            device_id = get_entity_device_id(
                entity_id
            )

            if device_id:
                detected_device_id = device_id
                break

        if detected_device_id:
            # ZHA muze entity pridavat postupne.
            time.sleep(3)

            metadata = get_device_metadata(
                detected_device_id
            )

            entities = build_entity_inventory(
                detected_device_id
            )

            temperature_entity = (
                find_entity_by_device_class(
                    entities,
                    "temperature",
                )
            )

            battery_entity = (
                find_entity_by_device_class(
                    entities,
                    "battery",
                )
            )

            return {
                "device": metadata,
                "entities": entities,
                "temperature_entity": (
                    temperature_entity
                ),
                "battery_entity": battery_entity,
            }

        time.sleep(
            ZIGBEE_DISCOVERY_POLL_SECONDS
        )

    raise HomeAssistantApiError(
        "V povolenem casovem limitu nebylo "
        "nalezeno nove Home Assistant zarizeni."
    )


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


def call_home_assistant_service(
    *,
    domain: str,
    service: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    """Zavola sluzbu Home Assistant Core API."""

    normalized_domain = str(domain or "").strip()
    normalized_service = str(service or "").strip()

    if not normalized_domain:
        raise ValueError(
            "Domena sluzby nesmi byt prazdna."
        )

    if not normalized_service:
        raise ValueError(
            "Nazev sluzby nesmi byt prazdny."
        )

    return home_assistant_request(
        path=(
            "/services/"
            f"{normalized_domain}/"
            f"{normalized_service}"
        ),
        method="POST",
        payload=payload or {},
    )


def open_zigbee_permit(
    duration_seconds: int = 180,
) -> dict[str, Any]:
    """Otevre ZHA parovaci rezim na zadany cas."""

    try:
        duration = int(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Delka parovani musi byt cele cislo."
        ) from exc

    if duration < 1 or duration > 254:
        raise ValueError(
            "Delka parovani musi byt v rozsahu 1 az 254 sekund."
        )

    runtime_status = get_zigbee_runtime_status()

    if not runtime_status.get(
        "zha_service_domain_available"
    ):
        raise HomeAssistantApiError(
            "Domena sluzeb ZHA neni dostupna."
        )

    zha_services = runtime_status.get(
        "zha_services"
    ) or []

    if "permit" not in zha_services:
        raise HomeAssistantApiError(
            "Sluzba zha.permit neni dostupna."
        )

    response = call_home_assistant_service(
        domain="zha",
        service="permit",
        payload={
            "duration": duration,
        },
    )

    return {
        "ok": True,
        "service": "zha.permit",
        "duration_seconds": duration,
        "home_assistant_response": response,
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
