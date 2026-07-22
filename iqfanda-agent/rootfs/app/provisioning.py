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
        "/config/device.json",
    )
)

FACTORY_CONFIG_PATH = Path(
    os.getenv(
        "IQF_FACTORY_CONFIG_PATH",
        "/config/factory.json",
    )
)

INSTALL_CONFIG_PATH = Path(
    os.getenv(
        "IQF_INSTALL_CONFIG_PATH",
        "/config/install.json",
    )
)

DEFAULT_API_BASE_URL = "https://api.tngiqfanda.cz"
REGISTER_ENDPOINT = "/api/v1/provisioning/register"


def get_required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise ValueError(
            f"Chybi povinna provisioning hodnota: {name}"
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


def load_factory_config() -> dict:
    if not FACTORY_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Vyrobni konfigurace neexistuje: "
            f"{FACTORY_CONFIG_PATH}"
        )

    with FACTORY_CONFIG_PATH.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        factory_config = json.load(file)

    required_fields = (
        "provisioning_id",
        "serial_number",
        "device_model",
        "hardware_revision",
        "software_version",
    )

    for field in required_fields:
        value = factory_config.get(field)

        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Ve vyrobni konfiguraci chybi pole: {field}"
            )

    return factory_config


def load_install_config() -> dict:
    if not INSTALL_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Instalacni konfigurace neexistuje: "
            f"{INSTALL_CONFIG_PATH}"
        )

    with INSTALL_CONFIG_PATH.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        install_config = json.load(file)

    required_fields = (
        "first_name",
        "last_name",
        "email",
        "password",
        "site_name",
    )

    for field in required_fields:
        value = install_config.get(field)

        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"V instalacni konfiguraci chybi pole: {field}"
            )

    return install_config


def build_registration_payload() -> dict:
    factory_config = load_factory_config()
    install_config = load_install_config()

    hostname = get_optional_environment(
        "IQF_DEVICE_HOSTNAME",
        socket.gethostname(),
    )

    return {
        "provisioning_id": factory_config["provisioning_id"],
        "first_name": install_config["first_name"],
        "last_name": install_config["last_name"],
        "email": install_config["email"],
        "password": install_config["password"],
        "site_name": install_config["site_name"],
        "site_type": install_config.get(
            "site_type",
            "house",
        ),
        "device_serial_number": factory_config["serial_number"],
        "device_name": get_optional_environment(
            "IQF_DEVICE_NAME",
            "TNG IQ FANDA",
        ),
        "device_model": factory_config["device_model"],
        "hardware_revision": factory_config[
            "hardware_revision"
        ],
        "device_hostname": hostname,
        "software_version": factory_config[
            "software_version"
        ],
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
            "Cloud nevratil uspesny vysledek registrace."
        )

    for field in (
        "device_uuid",
        "device_token",
    ):
        if not registration.get(field):
            raise ValueError(
                f"V odpovedi registrace chybi pole: {field}"
            )

    return {
        "ok": True,
        "data": registration,
    }


def save_device_identity(registration: dict) -> dict:
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

        if INSTALL_CONFIG_PATH.exists():
            INSTALL_CONFIG_PATH.unlink()
            logging.info(
                "Instalacni konfigurace byla po uspesne registraci odstranena."
            )

    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return {
        "ok": True,
    }


def ensure_device_is_provisioned() -> bool:
    if DEVICE_CONFIG_PATH.exists():
        logging.info(
            "Identita zarizeni jiz existuje: %s",
            DEVICE_CONFIG_PATH,
        )
        return False

    if not provisioning_is_enabled():
        raise RuntimeError(
            "Zarizeni neni registrovano a provisioning "
            "je vypnuty."
        )

    logging.info(
        "Identita zarizeni neexistuje. "
        "Spoustim prvni registraci."
    )

    payload = build_registration_payload()

    try:
        registration_result = register_device(payload)
        registration = registration_result["data"]

    except error.HTTPError as exc:
        response_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Registrace byla odmitnuta, HTTP "
            f"{exc.code}: {response_body}"
        ) from exc

    except error.URLError as exc:
        raise RuntimeError(
            f"Cloud neni pri registraci dostupny: "
            f"{exc.reason}"
        ) from exc

    save_device_identity(registration)

    logging.info(
        "Zarizeni bylo uspesne registrovano. UUID: %s",
        registration["device_uuid"],
    )

    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    ensure_device_is_provisioned()