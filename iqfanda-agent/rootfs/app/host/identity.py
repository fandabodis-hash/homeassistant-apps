from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_IDENTITY_PATH = Path(
    os.getenv(
        "IQF_DEVICE_IDENTITY_PATH",
        "/data/device_identity.json",
    )
)


class DeviceIdentityService:
    """
    Central service for reading the permanent manufacturing identity
    of an IQ FANDA device.

    The manufacturing identity is separate from cloud credentials.
    """

    def __init__(
        self,
        identity_path: Path = DEFAULT_IDENTITY_PATH,
    ) -> None:
        self.identity_path = Path(identity_path)

    def exists(self) -> bool:
        return self.identity_path.is_file()

    def load(self) -> dict[str, Any]:
        if not self.exists():
            return {
                "state": "FACTORY",
                "identity_exists": False,
            }

        try:
            raw_content = self.identity_path.read_text(encoding="utf-8")
            data = json.loads(raw_content)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Unable to load device identity from "
                f"{self.identity_path}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise RuntimeError(
                f"Device identity in {self.identity_path} "
                "must be a JSON object."
            )

        serial_number = str(data.get("serial_number") or "").strip()

        if not serial_number:
            raise RuntimeError(
                f"Device identity in {self.identity_path} "
                "does not contain serial_number."
            )

        result = dict(data)
        result["identity_exists"] = True
        result.setdefault("state", "UNPROVISIONED")

        return result

    def get_serial_number(self) -> str | None:
        identity = self.load()

        if not identity.get("identity_exists"):
            return None

        return str(identity["serial_number"])

    def get_hostname(self) -> str | None:
        identity = self.load()

        if not identity.get("identity_exists"):
            return None

        hostname = str(identity.get("hostname") or "").strip()
        return hostname or None

    def get_state(self) -> str:
        return str(self.load().get("state") or "FACTORY")


identity_service = DeviceIdentityService()
