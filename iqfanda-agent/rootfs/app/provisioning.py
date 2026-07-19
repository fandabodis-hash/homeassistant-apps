import json
import logging
import os
import socket
import tempfile
from pathlib import Path
from urllib import error, request


CESTA_IDENTITY_ZARIZENI = Path(
    os.getenv(
        "IQF_DEVICE_CONFIG_PATH",
        "/data/device.json",
    )
)

VYCHOZI_API_URL = "https://api.tngiqfanda.cz"
KONCOVY_BOD_REGISTRACE = "/api/v1/provisioning/register"


def nacti_povinnou_promennou(nazev: str) -> str:
    hodnota = os.getenv(nazev, "").strip()

    if not hodnota:
        raise ValueError(
            f"Chybi povinna provisioning hodnota: {nazev}"
        )

    return hodnota


def nacti_volitelnou_promennou(
    nazev: str,
    vychozi_hodnota: str | None = None,
) -> str | None:
    hodnota = os.getenv(nazev)

    if hodnota is None:
        return vychozi_hodnota

    upravena_hodnota = hodnota.strip()

    if not upravena_hodnota:
        return vychozi_hodnota

    return upravena_hodnota


def je_provisioning_povolen() -> bool:
    hodnota = os.getenv(
        "IQF_PROVISIONING_ENABLED",
        "true",
    ).strip().lower()

    return hodnota in {
        "1",
        "true",
        "yes",
        "on",
    }


def sestav_registracni_data() -> dict:
    nazev_hostitele = nacti_volitelnou_promennou(
        "IQF_DEVICE_HOSTNAME",
        socket.gethostname(),
    )

    return {
        "provisioning_id": nacti_povinnou_promennou(
            "IQF_PROVISIONING_ID"
        ),
        "first_name": nacti_povinnou_promennou(
            "IQF_FIRST_NAME"
        ),
        "last_name": nacti_povinnou_promennou(
            "IQF_LAST_NAME"
        ),
        "email": nacti_povinnou_promennou(
            "IQF_EMAIL"
        ),
        "password": nacti_povinnou_promennou(
            "IQF_PASSWORD"
        ),
        "site_name": nacti_povinnou_promennou(
            "IQF_SITE_NAME"
        ),
        "site_type": nacti_volitelnou_promennou(
            "IQF_SITE_TYPE",
            "house",
        ),
        "device_serial_number": nacti_povinnou_promennou(
            "IQF_DEVICE_SERIAL_NUMBER"
        ),
        "device_name": nacti_volitelnou_promennou(
            "IQF_DEVICE_NAME",
            "TNG IQ FANDA",
        ),
        "device_model": nacti_volitelnou_promennou(
            "IQF_DEVICE_MODEL",
            "IQ FANDA PI5",
        ),
        "hardware_revision": nacti_volitelnou_promennou(
            "IQF_HARDWARE_REVISION",
            "Raspberry Pi 5",
        ),
        "device_hostname": nazev_hostitele,
        "software_version": nacti_volitelnou_promennou(
            "IQF_SOFTWARE_VERSION",
            "0.1.0",
        ),
    }


def sestav_url_registrace() -> str:
    api_url = os.getenv(
        "IQF_API_BASE_URL",
        VYCHOZI_API_URL,
    ).strip().rstrip("/")

    if not api_url:
        api_url = VYCHOZI_API_URL

    return f"{api_url}{KONCOVY_BOD_REGISTRACE}"


def registruj_zarizeni(registracni_data: dict) -> dict:
    telo_pozadavku = json.dumps(
        registracni_data
    ).encode("utf-8")

    http_pozadavek = request.Request(
        sestav_url_registrace(),
        data=telo_pozadavku,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "TNG-IQ-FANDA-Agent",
        },
    )

    with request.urlopen(
        http_pozadavek,
        timeout=30,
    ) as odpoved:
        telo_odpovedi = odpoved.read().decode(
            "utf-8",
            errors="strict",
        )

    registrace = json.loads(telo_odpovedi)

    if not registrace.get("success"):
        raise ValueError(
            "Cloud nevratil uspesny vysledek registrace."
        )

    for nazev_pole in (
        "device_uuid",
        "device_token",
    ):
        if not registrace.get(nazev_pole):
            raise ValueError(
                f"V odpovedi registrace chybi pole: {nazev_pole}"
            )

    return registrace


def uloz_identitu_zarizeni(registrace: dict) -> None:
    CESTA_IDENTITY_ZARIZENI.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    identita = {
        "device_uuid": registrace["device_uuid"],
        "device_token": registrace["device_token"],
        "api_base_url": os.getenv(
            "IQF_API_BASE_URL",
            VYCHOZI_API_URL,
        ).strip().rstrip("/"),
        "device_status": registrace.get(
            "device_status"
        ),
        "site_id": registrace.get("site_id"),
        "device_id": registrace.get("device_id"),
    }

    popisovac_souboru, docasny_nazev = tempfile.mkstemp(
        prefix="device-",
        suffix=".tmp",
        dir=CESTA_IDENTITY_ZARIZENI.parent,
    )

    docasna_cesta = Path(docasny_nazev)

    try:
        with os.fdopen(
            popisovac_souboru,
            "w",
            encoding="utf-8",
        ) as soubor:
            json.dump(
                identita,
                soubor,
                ensure_ascii=True,
                indent=2,
            )
            soubor.write("\n")
            soubor.flush()
            os.fsync(soubor.fileno())

        docasna_cesta.replace(
            CESTA_IDENTITY_ZARIZENI
        )

    except Exception:
        docasna_cesta.unlink(missing_ok=True)
        raise


def zajisti_registraci_zarizeni() -> bool:
    if CESTA_IDENTITY_ZARIZENI.exists():
        logging.info(
            "Identita zarizeni jiz existuje: %s",
            CESTA_IDENTITY_ZARIZENI,
        )
        return False

    if not je_provisioning_povolen():
        raise RuntimeError(
            "Zarizeni neni registrovano a provisioning "
            "je vypnuty."
        )

    logging.info(
        "Identita zarizeni neexistuje. "
        "Spoustim prvni registraci."
    )

    registracni_data = sestav_registracni_data()

    try:
        registrace = registruj_zarizeni(
            registracni_data
        )

    except error.HTTPError as chyba:
        telo_odpovedi = chyba.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Registrace byla odmitnuta, HTTP "
            f"{chyba.code}: {telo_odpovedi}"
        ) from chyba

    except error.URLError as chyba:
        raise RuntimeError(
            f"Cloud neni pri registraci dostupny: "
            f"{chyba.reason}"
        ) from chyba

    uloz_identitu_zarizeni(registrace)

    logging.info(
        "Zarizeni bylo uspesne registrovano. UUID: %s",
        registrace["device_uuid"],
    )

    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    zajisti_registraci_zarizeni()