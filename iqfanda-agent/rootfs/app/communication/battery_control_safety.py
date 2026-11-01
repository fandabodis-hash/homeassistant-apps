"""A local battery safety condition must end the owned request, not retry it."""

from __future__ import annotations


class BatteryControlSafetyStop(RuntimeError):
    """The device's local measurements require a verified return to AUTO."""

    def __init__(self, reason: str, observed_soc_percent: float | None = None):
        self.reason = str(reason)
        self.observed_soc_percent = observed_soc_percent
        detail = "" if observed_soc_percent is None else f" (SOC={observed_soc_percent:g} %)"
        super().__init__(f"Battery safety stop: {self.reason}{detail}")
