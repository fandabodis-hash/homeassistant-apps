"""Univerzalni adapter fyzickeho rizeni stridace."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Any, Callable

from communication.inverter_adapter import (
    select_inverter_profile,
)


BATTERY_CHARGE_CONTROL = (
    "battery_charge_control"
)


class UnsupportedInverterCapabilityError(
    RuntimeError
):
    """Pozadovana capability neni profilem podporovana."""


@dataclass(frozen=True)
class InverterBatteryControlResult:
    capability: str
    action: str
    requested_power_w: int
    applied_power_w: int
    verified: bool
    write_performed: bool


def _select_photovoltaic_runtime(
    cloud_config: dict[str, Any],
) -> dict[str, Any]:
    """
    Najde obecnou FVE runtime konfiguraci.

    Funkce nerozhoduje podle vyrobce ani modelu.
    """
    if not isinstance(
        cloud_config,
        dict,
    ):
        raise RuntimeError(
            "Cloudova konfigurace neni dostupna."
        )

    runtime_configurations = cloud_config.get(
        "module_runtime_configurations"
    )

    if not isinstance(
        runtime_configurations,
        list,
    ):
        raise RuntimeError(
            "Cloudova konfigurace neobsahuje "
            "runtime konfigurace modulu."
        )

    matches: list[dict[str, Any]] = []

    for runtime in runtime_configurations:

        if not isinstance(
            runtime,
            dict,
        ):
            continue

        module_key = str(
            runtime.get(
                "module_key"
            )
            or ""
        ).strip().lower()

        communication_type = str(
            runtime.get(
                "communication_type"
            )
            or ""
        ).strip().lower()

        if (
            module_key == "photovoltaic"
            and communication_type == "rs485"
        ):
            matches.append(
                runtime
            )

    if len(matches) != 1:
        raise RuntimeError(
            "FVE runtime konfigurace nebyla "
            "nalezena jednoznacne."
        )

    runtime = matches[0]

    manufacturer = str(
        runtime.get(
            "manufacturer"
        )
        or ""
    ).strip()

    model = str(
        runtime.get(
            "model"
        )
        or ""
    ).strip()

    if not manufacturer:
        raise RuntimeError(
            "FVE runtime nema vyrobce."
        )

    if not model:
        raise RuntimeError(
            "FVE runtime nema model."
        )

    return runtime


def _load_control_driver(
    *,
    profile: dict[str, Any],
    capability_name: str,
    action: str,
) -> Callable[..., Any]:
    """
    Nacte driver deklarovany lokalnim profilem.

    Profil je soucast instalacni/integracni vrstvy.
    Obecna runtime logika konkretni driver nezna.
    """
    capabilities = profile.get(
        "control_capabilities"
    )

    if not isinstance(
        capabilities,
        dict,
    ):
        raise UnsupportedInverterCapabilityError(
            "Profil stridace nema zadne "
            "control capabilities."
        )

    capability = capabilities.get(
        capability_name
    )

    if not isinstance(
        capability,
        dict,
    ):
        raise UnsupportedInverterCapabilityError(
            "Profil stridace nepodporuje capability "
            f"{capability_name}."
        )

    if capability.get(
        "schema_version"
    ) != 1:
        raise RuntimeError(
            "Control capability ma nepodporovanou "
            "verzi schematu."
        )

    supported_actions = capability.get(
        "supported_actions"
    )

    if (
        not isinstance(
            supported_actions,
            list,
        )
        or action not in supported_actions
    ):
        raise UnsupportedInverterCapabilityError(
            "Profil stridace nepodporuje pozadovanou "
            f"akci {action}."
        )

    module_name = str(
        capability.get(
            "driver_module"
        )
        or ""
    ).strip()

    function_name = str(
        capability.get(
            "driver_function"
        )
        or ""
    ).strip()

    if (
        not module_name.startswith(
            "communication."
        )
        or ".." in module_name
        or not all(
            part.isidentifier()
            for part
            in module_name.split(".")
        )
    ):
        raise RuntimeError(
            "Control profil obsahuje "
            "neplatny driver_module."
        )

    if (
        not function_name
        or not function_name.isidentifier()
    ):
        raise RuntimeError(
            "Control profil obsahuje "
            "neplatny driver_function."
        )

    module = importlib.import_module(
        module_name
    )

    driver = getattr(
        module,
        function_name,
        None,
    )

    if not callable(driver):
        raise RuntimeError(
            "Control driver nebyl nalezen."
        )

    return driver


def _result_value(
    result: Any,
    key: str,
) -> Any:
    if isinstance(
        result,
        dict,
    ):
        return result.get(
            key
        )

    return getattr(
        result,
        key,
        None,
    )


def execute_battery_control_from_cloud_config(
    *,
    cloud_config: dict[str, Any],
    action: str,
    allowed_charge_power_w: int,
    target_soc_percent: float,
) -> InverterBatteryControlResult:
    """
    Provede univerzalni battery charge control.

    Spot, command worker ani jina nadrazena logika
    nevi, jaky vyrobce nebo model je fyzicky pripojen.
    """
    normalized_action = str(
        action or ""
    ).strip()

    if not normalized_action:
        raise ValueError(
            "Battery control action nesmi byt prazdna."
        )

    runtime = _select_photovoltaic_runtime(
        cloud_config
    )

    manufacturer = str(
        runtime.get(
            "manufacturer"
        )
        or ""
    ).strip()

    model = str(
        runtime.get(
            "model"
        )
        or ""
    ).strip()

    profile = select_inverter_profile(
        manufacturer=manufacturer,
        model=model,
    )

    driver = _load_control_driver(
        profile=profile,
        capability_name=(
            BATTERY_CHARGE_CONTROL
        ),
        action=normalized_action,
    )

    driver_result = driver(
        cloud_config=cloud_config,
        action=normalized_action,
        allowed_charge_power_w=int(
            allowed_charge_power_w
        ),
        target_soc_percent=float(
            target_soc_percent
        ),
    )

    if driver_result is None:
        raise RuntimeError(
            "Battery control driver nevratil vysledek."
        )

    result_action = _result_value(
        driver_result,
        "action",
    )

    requested_power_w = _result_value(
        driver_result,
        "requested_power_w",
    )

    applied_power_w = _result_value(
        driver_result,
        "applied_power_w",
    )

    verified = _result_value(
        driver_result,
        "verified",
    )

    write_performed = _result_value(
        driver_result,
        "write_performed",
    )

    if not isinstance(
        result_action,
        str,
    ):
        raise RuntimeError(
            "Battery control driver nevratil action."
        )

    if type(requested_power_w) is not int:
        raise RuntimeError(
            "Battery control driver nevratil "
            "requested_power_w."
        )

    if type(applied_power_w) is not int:
        raise RuntimeError(
            "Battery control driver nevratil "
            "applied_power_w."
        )

    if type(verified) is not bool:
        raise RuntimeError(
            "Battery control driver nevratil verified."
        )

    if type(write_performed) is not bool:
        raise RuntimeError(
            "Battery control driver nevratil "
            "write_performed."
        )

    return InverterBatteryControlResult(
        capability=BATTERY_CHARGE_CONTROL,
        action=result_action,
        requested_power_w=requested_power_w,
        applied_power_w=applied_power_w,
        verified=verified,
        write_performed=write_performed,
    )
