"""Self-test bootstrap identity TNG IQ FANDA Installer V2."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import stat
import tempfile
from pathlib import Path

from installer_v2.bootstrap_identity import (
    ensure_bootstrap_identity,
    load_bootstrap_identity,
)


def run() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = (
            Path(temp_dir)
            / "bootstrap.json"
        )

        first = ensure_bootstrap_identity(
            hardware_id="ABCDEF0123456789",
            path=path,
        )

        assert first["created"] is True

        first_identity = first[
            "identity"
        ]

        first_id = first_identity[
            "provisioning_id"
        ]

        assert first_id

        assert (
            first_identity[
                "hardware_id"
            ]
            == "abcdef0123456789"
        )

        assert (
            first_identity[
                "serial_number"
            ]
            is None
        )

        assert (
            stat.S_IMODE(
                path.stat().st_mode
            )
            == 0o600
        )

        second = ensure_bootstrap_identity(
            hardware_id="ABCDEF0123456789",
            path=path,
        )

        assert second[
            "created"
        ] is False

        assert (
            second[
                "identity"
            ][
                "provisioning_id"
            ]
            == first_id
        )

        stored = load_bootstrap_identity(
            path=path,
            expected_hardware_id=(
                "ABCDEF0123456789"
            ),
        )

        assert stored is not None

        assert (
            stored[
                "provisioning_id"
            ]
            == first_id
        )

        file_payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        assert (
            file_payload[
                "provisioning_id"
            ]
            == first_id
        )

        try:
            ensure_bootstrap_identity(
                hardware_id=(
                    "JINY-HARDWARE"
                ),
                path=path,
            )

        except ValueError:
            pass

        else:
            raise AssertionError(
                "Jiny hardware musel byt odmitnut."
            )

    with tempfile.TemporaryDirectory() as temp_dir:
        path = (
            Path(temp_dir)
            / "bootstrap-concurrent.json"
        )

        def create_once(
            _: int,
        ) -> tuple[bool, str]:
            result = ensure_bootstrap_identity(
                hardware_id=(
                    "CONCURRENT-HARDWARE-00009"
                ),
                path=path,
            )

            return (
                bool(
                    result[
                        "created"
                    ]
                ),
                result[
                    "identity"
                ][
                    "provisioning_id"
                ],
            )

        with ThreadPoolExecutor(
            max_workers=16,
        ) as executor:
            results = list(
                executor.map(
                    create_once,
                    range(64),
                )
            )

        provisioning_ids = {
            provisioning_id
            for _, provisioning_id
            in results
        }

        assert len(
            provisioning_ids
        ) == 1

        assert sum(
            1
            for created, _
            in results
            if created
        ) == 1

        stored = load_bootstrap_identity(
            path=path,
            expected_hardware_id=(
                "CONCURRENT-HARDWARE-00009"
            ),
        )

        assert stored is not None

        assert (
            stored[
                "provisioning_id"
            ]
            == next(
                iter(
                    provisioning_ids
                )
            )
        )

    print(
        "Installer V2 bootstrap identity self-test: OK"
    )


if __name__ == "__main__":
    run()