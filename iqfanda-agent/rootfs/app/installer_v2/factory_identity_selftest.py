"""Self-test finalizace vyrobni identity Installer V2."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import stat
import tempfile
import uuid
from pathlib import Path

from installer_v2.bootstrap_identity import (
    build_bootstrap_identity,
)
from installer_v2.factory_identity import (
    finalize_manufacturing_identity,
)
from manufacturing import (
    read_raspberry_pi_serial,
)


def run() -> None:
    hardware_id = (
        read_raspberry_pi_serial()
        or "test-hardware-id"
    )

    provisioning_id = str(
        uuid.UUID(
            "11111111-2222-3333-4444-555555555555"
        )
    )

    bootstrap = (
        build_bootstrap_identity(
            hardware_id=hardware_id,
            provisioning_id=(
                provisioning_id
            ),
        )
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        path = (
            Path(temp_dir)
            / "device_identity.json"
        )

        first = (
            finalize_manufacturing_identity(
                bootstrap_identity=(
                    bootstrap
                ),
                serial_number=(
                    "F800711-TNG-00009"
                ),
                identity_path=path,
            )
        )

        assert first[
            "created"
        ] is True

        identity = first[
            "identity"
        ]

        assert (
            identity[
                "provisioning_id"
            ]
            == provisioning_id
        )

        assert (
            identity[
                "serial_number"
            ]
            == "F800711-TNG-00009"
        )

        assert (
            identity[
                "state"
            ]
            == "READY_FOR_INSTALL"
        )

        assert (
            str(
                identity[
                    "hardware_id"
                ]
            ).lower()
            == str(
                hardware_id
            ).lower()
        )

        assert (
            stat.S_IMODE(
                path.stat().st_mode
            )
            == 0o600
        )

        second = (
            finalize_manufacturing_identity(
                bootstrap_identity=(
                    bootstrap
                ),
                serial_number=(
                    "F800711-TNG-00009"
                ),
                identity_path=path,
            )
        )

        assert second[
            "created"
        ] is False

        try:
            finalize_manufacturing_identity(
                bootstrap_identity=(
                    bootstrap
                ),
                serial_number=(
                    "F800711-TNG-00010"
                ),
                identity_path=path,
            )

        except FileExistsError:
            pass

        else:
            raise AssertionError(
                "Jine seriove cislo muselo byt odmitnuto."
            )

    with tempfile.TemporaryDirectory() as temp_dir:
        path = (
            Path(temp_dir)
            / "device_identity_concurrent.json"
        )

        def finalize_once(
            _: int,
        ) -> tuple[bool, str, str]:
            result = (
                finalize_manufacturing_identity(
                    bootstrap_identity=(
                        bootstrap
                    ),
                    serial_number=(
                        "F800711-TNG-00009"
                    ),
                    identity_path=path,
                )
            )

            identity = result[
                "identity"
            ]

            return (
                bool(
                    result[
                        "created"
                    ]
                ),
                str(
                    identity[
                        "provisioning_id"
                    ]
                ),
                str(
                    identity[
                        "serial_number"
                    ]
                ),
            )

        with ThreadPoolExecutor(
            max_workers=16,
        ) as executor:
            results = list(
                executor.map(
                    finalize_once,
                    range(64),
                )
            )

        assert sum(
            1
            for created, _, _
            in results
            if created
        ) == 1

        provisioning_ids = {
            provisioning_id
            for _, provisioning_id, _
            in results
        }

        serial_numbers = {
            serial_number
            for _, _, serial_number
            in results
        }

        assert provisioning_ids == {
            provisioning_id
        }

        assert serial_numbers == {
            "F800711-TNG-00009"
        }

        assert (
            stat.S_IMODE(
                path.stat().st_mode
            )
            == 0o600
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        path = (
            Path(temp_dir)
            / "device_identity_conflict.json"
        )

        other_bootstrap = (
            build_bootstrap_identity(
                hardware_id=(
                    hardware_id
                ),
                provisioning_id=(
                    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
                ),
            )
        )

        finalize_manufacturing_identity(
            bootstrap_identity=(
                bootstrap
            ),
            serial_number=(
                "F800711-TNG-00009"
            ),
            identity_path=path,
        )

        try:
            finalize_manufacturing_identity(
                bootstrap_identity=(
                    other_bootstrap
                ),
                serial_number=(
                    "F800711-TNG-00009"
                ),
                identity_path=path,
            )

        except FileExistsError:
            pass

        else:
            raise AssertionError(
                "Jine provisioning_id muselo byt odmitnuto."
            )

    print(
        "Installer V2 factory identity self-test: OK"
    )


if __name__ == "__main__":
    run()