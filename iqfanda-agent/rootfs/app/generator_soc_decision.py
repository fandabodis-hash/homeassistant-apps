"""R10 generator: cista rozhodovaci funkce SOC, zadne fyzicke povely.

Nepouzivat tuto funkci jako samostatny runtime. Odesilani Zigbee je zamerne
oddeleno a vyzaduje dalsi provereni RMT, senzoru, bezpecneho vypnuti a
vyhradniho vlastnika ovladani v Agentovi.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

Action = Literal["NONE", "REQUEST_START", "REQUEST_STOP"]


@dataclass(frozen=True)
class SocPolicy:
    start_soc_percent: float
    stop_soc_percent: float
    confirmation_seconds: int = 120
    minimum_run_seconds: int = 900
    minimum_stop_seconds: int = 300

    def validate(self) -> None:
        if not (isfinite(self.start_soc_percent) and isfinite(self.stop_soc_percent)):
            raise ValueError("SOC prahy musi byt konecna cisla.")
        if not (5 <= self.start_soc_percent <= 90 and 10 <= self.stop_soc_percent <= 100):
            raise ValueError("SOC prahy mimo povolene meze.")
        if self.stop_soc_percent - self.start_soc_percent < 5:
            raise ValueError("SOC hystereze musi byt alespon pet procent.")
        if not 30 <= self.confirmation_seconds <= 600:
            raise ValueError("Neplatna doba potvrzovani.")
        if not 600 <= self.minimum_run_seconds <= 14400:
            raise ValueError("Neplatna minimalni doba chodu.")
        if not 300 <= self.minimum_stop_seconds <= 14400:
            raise ValueError("Neplatna minimalni doba odstavky.")


@dataclass(frozen=True)
class LocalState:
    soc_percent: float | None
    soc_age_seconds: float | None
    soc_quality_good: bool
    generator_running: bool | None
    generator_feedback_age_seconds: float | None
    contact_physically_verified: bool
    contact_failsafe_verified: bool
    remote_mode_verified: bool
    automatic_runtime_armed: bool
    policy_requested_enabled: bool
    elapsed_since_last_transition_seconds: float | None
    condition_held_seconds: float | None


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


def evaluate_soc_automation(policy: SocPolicy, state: LocalState) -> Decision:
    """Vypocte zamer, nikdy sam nezapne a nevypne Zigbee rele."""
    policy.validate()
    if not state.policy_requested_enabled or not state.automatic_runtime_armed:
        return Decision("NONE", "not_armed")
    if not (state.contact_physically_verified and state.contact_failsafe_verified
            and state.remote_mode_verified):
        return Decision("NONE", "contact_or_remote_not_verified")
    soc = state.soc_percent
    if (not state.soc_quality_good or soc is None or not isfinite(soc)
            or not 0 <= soc <= 100 or state.soc_age_seconds is None
            or not isfinite(state.soc_age_seconds)
            or not 0 <= state.soc_age_seconds <= 120):
        return Decision("NONE", "invalid_or_stale_soc")
    if (state.generator_running is None or state.generator_feedback_age_seconds is None
            or not isfinite(state.generator_feedback_age_seconds)
            or not 0 <= state.generator_feedback_age_seconds <= 120):
        return Decision("NONE", "generator_state_unknown")
    if (state.elapsed_since_last_transition_seconds is None
            or not isfinite(state.elapsed_since_last_transition_seconds)
            or state.elapsed_since_last_transition_seconds < 0):
        return Decision("NONE", "transition_clock_unknown")
    held = state.condition_held_seconds
    if held is None or not isfinite(held) or held < policy.confirmation_seconds:
        return Decision("NONE", "awaiting_soc_confirmation")
    if state.generator_running:
        if soc < policy.stop_soc_percent:
            return Decision("NONE", "soc_below_stop")
        if state.elapsed_since_last_transition_seconds < policy.minimum_run_seconds:
            return Decision("NONE", "minimum_run_time")
        return Decision("REQUEST_STOP", "soc_reached_stop_threshold")
    if soc > policy.start_soc_percent:
        return Decision("NONE", "soc_above_start")
    if state.elapsed_since_last_transition_seconds < policy.minimum_stop_seconds:
        return Decision("NONE", "minimum_stop_time")
    return Decision("REQUEST_START", "soc_reached_start_threshold")
