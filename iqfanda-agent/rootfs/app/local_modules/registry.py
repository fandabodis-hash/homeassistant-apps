"""F59 local control registry.

Each new automation lives in its own file. Import a reviewed local entrypoint
here, never an import path supplied by the cloud. Existing local owners are NOT
registered until an explicitly verified one-owner migration is completed.
"""
from __future__ import annotations

from typing import Callable, Mapping, Any

LocalTick = Callable[[dict[str, Any]], None]

# F59 initial release deliberately has NO migrated controllers. Register a
# function here only after verifying it does not duplicate a legacy owner.
REGISTERED_LOCAL_TICKS: Mapping[str, LocalTick] = {}
