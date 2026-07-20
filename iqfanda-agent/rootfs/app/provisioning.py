import json
import logging
import os
import socket
import tempfile
from pathlib import Path
from urllib import error, request


DEVICE_CONFIG_PATH = Path(
    os.getenv(
        "IQF_DEVICE_CONFIG_PATH",
        "/data/device.json",
    )
)

DEFAULT_API_BASE_URL = "https://api.tngiqfanda.cz"
REGISTER_ENDPOINT = "/api/v1/provisioning/register"


def get_required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise ValueError(
            f"Chybí povinná provisioning hodnota: {name}"
        )

    return value


def get_optional_environment(
    name: str,
    default: str | None = None,
) -> str | None:
    value = os.getenv(name)

    if value is None:
        return default

    normalized_value = value.strip()
    return normalized_value or default


def provisioning_is_enabled() -> bool:
    raw_value = os.getenv(
        "IQF_PROVISIONING_ENABLED",
        "true",
    ).strip().lower()

    return raw_value in {
        "1",
        "true",
        "yes",
        "on",
    }


def build_registration_payload() -> dict:
    hostname = get_optional_environment(
        "IQF_DEVICE_HOSTNAME",
        socket.gethostname(),
    )

    return {
        "provisioning_id": get_required_environment(
            "IQF_PROVISIONING_ID"
        ),
        "first_name": get_required_environment(
            "IQF_FIRST_NAME"
        ),
        "last_name": get_required_environment(
            "IQF_LAST_NAME"
        ),
        "email": get_required_environment(
            "IQF_EMAIL"
        ),
        "password": get_required_environment(
            "IQF_PASSWORD"
        ),
        "site_name": get_required_environment(
            "IQF_SITE_NAME"
        ),
        "site_type": get_optional_environment(
            "IQF_SITE_TYPE",
            "house",
        ),
        "device_serial_number": get_required_environment(
            "IQF_DEVICE_SERIAL_NUMBER"
        ),
        "device_name": get_optional_environment(
            "IQF_DEVICE_NAME",
            "TNG IQ FANDA",
        ),
        "device_model": get_optional_environment(
            "IQF_DEVICE_MODEL",
            "IQ FANDA PI5",
        ),
        "hardware_revision": get_optional_environment(
            "IQF_HARDWARE_REVISION",
            "Raspberry Pi 5",
        ),
        "device_hostname": hostname,
        "software_version": get_optional_environment(
            "IQF_SOFTWARE_VERSION",
            "0.1.0",
        ),
    }


def get_register_url() -> str:
    api_base_url = os.getenv(
        "IQF_API_BASE_URL",
        DEFAULT_API_BASE_URL,
    ).strip().rstrip("/")

    if not api_base_url:
        api_base_url = DEFAULT_API_BASE_URL

    return f"{api_base_url}{REGISTER_ENDPOINT}"


def register_device(payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")

    http_request = request.Request(
        get_register_url(),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "TNG-IQ-FANDA-Agent",
        },
    )

    with request.urlopen(
        http_request,
        timeout=30,
    ) as response:
        response_body = response.read().decode(
            "utf-8",
            errors="strict",
        )

    registration = json.loads(response_body)

    if not registration.get("success"):
        raise ValueError(
            "Cloud nevrátil úspěšný výsledek registrace."
        )

    for field in (
        "device_uuid",
        "device_token",
    ):
        if not registration.get(field):
            raise ValueError(
                f"V odpovědi registrace chybí pole: {field}"
            )

    return registration


def save_device_identity(registration: dict) -> None:
    DEVICE_CONFIG_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    identity = {
        "device_uuid": registration["device_uuid"],
        "device_token": registration["device_token"],
        "api_base_url": os.getenv(
            "IQF_API_BASE_URL",
            DEFAULT_API_BASE_URL,
        ).strip().rstrip("/"),
        "device_status": registration.get(
            "device_status"
        ),
        "site_id": registration.get("site_id"),
        "device_id": registration.get("device_id"),
    }

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix="device-",
        suffix=".tmp",
        dir=DEVICE_CONFIG_PATH.parent,
    )

    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                identity,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        temporary_path.replace(DEVICE_CONFIG_PATH)

    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def ensure_device_is_provisioned() -> bool:
    if DEVICE_CONFIG_PATH.exists():
        logging.info(
            "Identita zařízení již existuje: %s",
            DEVICE_CONFIG_PATH,
        )
        return False

    if not provisioning_is_enabled():
        raise RuntimeError(
            "Zařízení není registrováno a provisioning "
            "je vypnutý."
        )

    logging.info(
        "Identita zařízení neexistuje. "
        "Spouštím první registraci."
    )

    payload = build_registration_payload()

    try:
        registration = register_device(payload)

    except error.HTTPError as exc:
        response_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Registrace byla odmítnuta, HTTP "
            f"{exc.code}: {response_body}"
        ) from exc

    except error.URLError as exc:
        raise RuntimeError(
            f"Cloud není při registraci dostupný: "
            f"{exc.reason}"
        ) from exc

    save_device_identity(registration)

    logging.info(
        "Zařízení bylo úspěšně registrováno. UUID: %s",
        registration["device_uuid"],
    )

    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    ensure_device_is_provisioned()