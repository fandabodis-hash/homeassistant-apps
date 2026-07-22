from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_CREDENTIAL_PATH = Path(
    os.getenv(
        "IQF_DEVICE_CREDENTIAL_PATH",
        "/data/iqf_device_credentials.json",
    )
)


class DeviceCredentialStore:
    """
    Bezpečné lokální úložiště identity zařízení.

    Soubor není součástí veřejného setup statusu.
    Zápis probíhá atomicky a výsledný soubor má režim 0600.
    """

    def __init__(
        self,
        path: Path = DEFAULT_CREDENTIAL_PATH,
    ) -> None:
        self.path = Path(path)

    def save_registration(
        self,
        registration_data: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(registration_data, dict):
            return {
                "ok": False,
                "error": "Registrační odpověď nemá platný formát.",
            }

        required_fields = (
            "device_id",
            "device_uuid",
            "device_token",
        )

        missing_fields = [
            field
            for field in required_fields
            if not registration_data.get(field)
        ]

        if missing_fields:
            return {
                "ok": False,
                "error": (
                    "V registrační odpovědi chybí: "
                    + ", ".join(missing_fields)
                ),
            }

        credentials = {
            "user_id": registration_data.get("user_id"),
            "site_id": registration_data.get("site_id"),
            "device_id": registration_data.get("device_id"),
            "device_uuid": registration_data.get("device_uuid"),
            "device_token": registration_data.get("device_token"),
            "device_status": registration_data.get("device_status"),
            "created_at": registration_data.get("created_at"),
        }

        try:
            self.path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                dir=str(self.path.parent),
            )

            temporary_path = Path(temporary_name)

            try:
                os.fchmod(file_descriptor, 0o600)

                with os.fdopen(
                    file_descriptor,
                    "w",
                    encoding="utf-8",
                ) as file:
                    json.dump(
                        credentials,
                        file,
                        ensure_ascii=False,
                        indent=2,
                    )
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())

                os.replace(
                    temporary_path,
                    self.path,
                )
                os.chmod(self.path, 0o600)

            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise

            return {
                "ok": True,
                "path": str(self.path),
                "device_uuid": credentials["device_uuid"],
            }

        except Exception as exc:
            return {
                "ok": False,
                "error": (
                    "Nepodařilo se uložit identitu zařízení: "
                    f"{exc}"
                ),
            }

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "ok": False,
                "error": "Identita zařízení zatím není uložena.",
            }

        try:
            data = json.loads(
                self.path.read_text(encoding="utf-8")
            )

            return {
                "ok": True,
                "data": data,
            }

        except Exception as exc:
            return {
                "ok": False,
                "error": (
                    "Nepodařilo se načíst identitu zařízení: "
                    f"{exc}"
                ),
            }


credential_store = DeviceCredentialStore()
