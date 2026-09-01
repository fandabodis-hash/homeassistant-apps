from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from device_config import load_cached_cloud_config
from pv_surplus_target_intent import (
    DEFAULT_PATH,
    SCHEMA_VERSION,
    _parse_datetime,
    _read_store,
    save_pv_surplus_target_intent,
)
from zigbee_manager import (
    call_binary_power_output_service,
    get_binary_power_output_service_domain,
    get_entity_state,
)


def _target_id_from_resource_key(resource_key: str) -> str:
    value = str(resource_key or "").strip()
    prefix = "pv_surplus_target:"
    if not value.startswith(prefix):
        raise ValueError("Neplatny resource_key energetickeho cile.")
    target_id = value[len(prefix):].strip()
    if not target_id:
        raise ValueError("resource_key neobsahuje target_id.")
    return target_id


def _find_generic_target_binding(
    *,
    cloud_config: dict[str, Any],
    resource_key: str,
) -> dict[str, Any]:

    target_id = (
        _target_id_from_resource_key(
            resource_key
        )
    )

    runtimes = cloud_config.get(
        "module_runtime_configurations"
    )

    if not isinstance(
        runtimes,
        list,
    ):
        raise ValueError(
            "Cloud config nema "
            "module_runtime_configurations."
        )

    runtime = next(
        (
            item
            for item in runtimes
            if (
                isinstance(item, dict)
                and str(
                    item.get("module_key")
                    or ""
                ).strip()
                == "pv_surplus_control"
            )
        ),
        None,
    )

    if runtime is None:
        raise ValueError(
            "pv_surplus_control runtime chybi."
        )

    configuration = runtime.get(
        "configuration"
    )

    if not isinstance(
        configuration,
        dict,
    ):
        raise ValueError(
            "pv_surplus_control konfigurace chybi."
        )

    targets = configuration.get(
        "targets"
    )

    if not isinstance(
        targets,
        list,
    ):
        raise ValueError(
            "Seznam energetickych cilu chybi."
        )

    target = next(
        (
            item
            for item in targets
            if (
                isinstance(item, dict)
                and str(
                    item.get("id")
                    or ""
                ).strip()
                == target_id
            )
        ),
        None,
    )

    if target is None:
        raise ValueError(
            "Energeticky cil nebyl nalezen."
        )

    if (
        str(
            target.get("type")
            or ""
        ).strip()
        != "generic_load"
    ):
        raise ValueError(
            "Executor smi ovladat "
            "pouze generic_load."
        )

    if target.get(
        "enabled"
    ) is not True:
        raise ValueError(
            "Energeticky cil je zakazan."
        )

    if (
        target.get(
            "configuration_status"
        )
        != "verified"
    ):
        raise ValueError(
            "Energeticky cil neni overen."
        )

    output = target.get(
        "output"
    )

    if not isinstance(
        output,
        dict,
    ):
        raise ValueError(
            "Energeticky cil nema vystup."
        )

    if (
        output.get("status")
        != "verified"
    ):
        raise ValueError(
            "Vystup energetickeho cile "
            "neni overen."
        )

    output_reference = str(
        output.get("reference")
        or ""
    ).strip()

    service_domain = (
        get_binary_power_output_service_domain(
            output_reference
        )
    )

    return {
        "target_id":
            target_id,

        "target_name":
            str(
                target.get("name")
                or "Cil"
            ).strip(),

        "output_reference":
            output_reference,

        "capability":
            "binary_power_output",

        "service_domain":
            service_domain,

        "actuation_enabled":
            configuration.get(
                "actuation_enabled"
            )
            is True,
    }

def _set_switch_state(
    *,
    output_reference: str,
    desired_on: bool,
) -> dict[str, Any]:
    """
    Zpetne kompatibilni helper pro overeny
    binary_power_output.
    """

    reference = str(
        output_reference or ""
    ).strip()

    service_domain = (
        get_binary_power_output_service_domain(
            reference
        )
    )

    target_state = (
        "on"
        if desired_on
        else "off"
    )

    before_state = str(
        get_entity_state(
            reference
        ).get(
            "state"
        )
        or ""
    ).strip().lower()

    service_called = False
    service_name = None

    if before_state != target_state:

        service_result = (
            call_binary_power_output_service(
                entity_id=reference,
                desired_on=desired_on,
            )
        )

        service_called = True

        service_name = (
            service_result.get(
                "service"
            )
        )

    readback_state = None

    for _ in range(20):

        readback_state = str(
            get_entity_state(
                reference
            ).get(
                "state"
            )
            or ""
        ).strip().lower()

        if (
            readback_state
            == target_state
        ):
            break

        time.sleep(0.25)

    if (
        readback_state
        != target_state
    ):
        raise RuntimeError(
            "PV target binary_power_output "
            "nebyl potvrzen read-back kontrolou."
        )

    return {
        "output_reference":
            reference,

        "capability":
            "binary_power_output",

        "service_domain":
            service_domain,

        "service":
            service_name,

        "requested_state":
            target_state,

        "previous_state":
            before_state,

        "readback_state":
            readback_state,

        "service_called":
            service_called,

        "readback_verified":
            True,
    }

def _write_store(
    *,
    intents: dict[str, Any],
    path: Path = DEFAULT_PATH,
) -> None:
    payload = {"schema_version": SCHEMA_VERSION, "intents": intents}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _remove_intent(
    *,
    resource_key: str,
    path: Path = DEFAULT_PATH,
) -> None:
    store = _read_store(path)
    intents = dict(store.get("intents") or {})
    intents.pop(resource_key, None)
    _write_store(intents=intents, path=path)


def apply_pv_surplus_target_intent(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(payload, dict):
        raise ValueError("PV target intent payload musi byt objekt.")

    resource_key = str(payload.get("resource_key") or "").strip()
    requested_output = str(payload.get("output_reference") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"on", "off"}:
        raise ValueError("PV target action musi byt on nebo off.")

    cloud_config = load_cached_cloud_config()
    if not isinstance(cloud_config, dict):
        raise RuntimeError("Cloud konfigurace neni lokalne dostupna.")

    binding = _find_generic_target_binding(
        cloud_config=cloud_config,
        resource_key=resource_key,
    )
    if requested_output != binding["output_reference"]:
        raise ValueError(
            "Intent output_reference neodpovida adminem overenemu vystupu cile."
        )

    if action == "on" and binding["actuation_enabled"] is not True:
        raise ValueError("Zapnuti energetickeho cile neni povoleno.")

    stored = save_pv_surplus_target_intent(payload, now=now)
    try:
        physical = _set_switch_state(
            output_reference=binding["output_reference"],
            desired_on=action == "on",
        )
    except Exception:
        _remove_intent(resource_key=resource_key)
        raise

    return {
        "target_id": binding["target_id"],
        "target_name": binding["target_name"],
        "resource_key": stored["resource_key"],
        "output_reference": stored["output_reference"],
        "action": stored["action"],
        "desired_on": stored["desired_on"],
        "reason": stored["reason"],
        "valid_until": stored["valid_until"],
        **physical,
    }


def reconcile_expired_pv_surplus_target_intents(
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if now is None:
        now = datetime.now(timezone.utc)
    if not DEFAULT_PATH.exists():
        return []

    try:
        store = _read_store(DEFAULT_PATH)
    except ValueError as exc:
        logging.error("PV target intent store nelze nacist: %s", exc)
        return []

    raw_intents = dict(store.get("intents") or {})
    if not raw_intents:
        return []

    cloud_config = load_cached_cloud_config()
    if not isinstance(cloud_config, dict):
        return []

    changed = False
    results: list[dict[str, Any]] = []

    for resource_key, intent in list(raw_intents.items()):
        if not isinstance(intent, dict):
            continue
        try:
            valid_until = _parse_datetime(intent.get("valid_until"))
        except (TypeError, ValueError):
            valid_until = now
        if valid_until > now:
            continue

        try:
            binding = _find_generic_target_binding(
                cloud_config=cloud_config,
                resource_key=resource_key,
            )
            stored_output = str(
                intent.get("output_reference") or ""
            ).strip()
            if stored_output != binding["output_reference"]:
                raise ValueError(
                    "Expirovany intent neodpovida aktualnimu admin output bindingu."
                )

            physical = _set_switch_state(
                output_reference=binding["output_reference"],
                desired_on=False,
            )
            raw_intents.pop(resource_key, None)
            changed = True
            results.append(
                {
                    "resource_key": resource_key,
                    "target_id": binding["target_id"],
                    "action": "fail_safe_off",
                    "reason": "intent_expired",
                    **physical,
                }
            )
        except Exception as exc:
            logging.error(
                "PV target expired fail-safe selhal resource=%s error=%s",
                resource_key,
                exc,
            )
            results.append(
                {
                    "resource_key": resource_key,
                    "action": "fail_safe_error",
                    "reason": str(exc),
                }
            )

    if changed:
        _write_store(intents=raw_intents)
    return results
