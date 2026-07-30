"""Datovy model jednoho Modbus RTU serioveho portu."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ModbusPort:
    """Stav jednoho serioveho portu pro Modbus RTU."""

    path: str
    serial_port: Any | None = None
    status: str = "registered"
    error_count: int = 0
    last_error: str | None = None

    def __post_init__(self) -> None:
        """Normalizuje a overi cestu serioveho portu."""

        self.path = self.path.strip()

        if not self.path:
            raise ValueError(
                "Cesta serioveho portu nesmi byt prazdna."
            )

    @property
    def is_open(self) -> bool:
        """Vrati, zda je seriovy port evidovan jako otevreny."""

        return (
            self.serial_port is not None
            and bool(
                getattr(
                    self.serial_port,
                    "is_open",
                    False,
                )
            )
        )
