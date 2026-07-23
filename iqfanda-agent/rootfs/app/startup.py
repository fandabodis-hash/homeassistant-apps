"""Koordinator startu sluzeb TNG IQ FANDA Agentu."""

import logging
import threading
import time

from device_config import main as device_config_main
from heartbeat import main as heartbeat_main
from installer.access_point_service import request_access_point
from installer.installer_api import spustit_api
from provisioning import ensure_device_is_provisioned
from host.iqf_host_api import main as host_api_main


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


PROVISIONING_RETRY_SECONDS = 30
SERVICE_CHECK_SECONDS = 5


def spustit_heartbeat() -> None:
    """Spusti heartbeat sluzbu."""

    heartbeat_main()


def spustit_synchronizaci_konfigurace() -> None:
    """Spusti synchronizaci cloudove konfigurace."""

    device_config_main()


def spustit_installer_api() -> None:
    """Spusti lokalni HTTP API Installeru."""

    spustit_api()


def spustit_host_api() -> None:
    """Spusti lokalni Host API."""

    host_api_main()


def cekat_na_registraci_zarizeni(
    installer_api_thread: threading.Thread,
) -> None:
    """Ceka na vytvoreni identity zarizeni."""

    logging.info(
        "Kontroluji, zda je zařízení připraveno k registraci."
    )

    access_point_requested = False

    while True:
        if not installer_api_thread.is_alive():
            raise RuntimeError(
                "Installer API se během čekání na registraci ukončilo."
            )

        try:
            ensure_device_is_provisioned()
            return

        except FileNotFoundError as chyba:
            logging.warning("%s", chyba)

            if not access_point_requested:
                try:
                    vysledek_ap = request_access_point(
                        reason="device_not_provisioned",
                    )
                    logging.info(
                        "Host Agent byl po\u017e\u00e1d\u00e1n "
                        "o spu\u0161t\u011bn\u00ed instala\u010dn\u00edho "
                        "Access Pointu: %s",
                        vysledek_ap.get("path"),
                    )
                    access_point_requested = True

                except Exception:
                    logging.exception(
                        "Po\u017eadavek na spu\u0161t\u011bn\u00ed "
                        "instala\u010dn\u00edho Access Pointu "
                        "se nepoda\u0159ilo ulo\u017eit."
                    )
            logging.info(
                "Zařízení čeká na dokončení instalace. "
                "Další kontrola za %s sekund.",
                PROVISIONING_RETRY_SECONDS,
            )

        except Exception:
            logging.exception(
                "Provisioning zařízení selhal. "
                "Další pokus proběhne za %s sekund.",
                PROVISIONING_RETRY_SECONDS,
            )

        time.sleep(PROVISIONING_RETRY_SECONDS)


def vytvorit_vlakno(
    cil,
    nazev: str,
) -> threading.Thread:
    """Vytvori daemon vlakno pro dlouhodobou sluzbu."""

    return threading.Thread(
        target=cil,
        name=nazev,
        daemon=True,
    )


def kontrolovat_sluzby(
    vlakna: dict[str, threading.Thread],
) -> None:
    """Prubezne kontroluje beh hlavních sluzeb."""

    while True:
        for nazev, vlakno in vlakna.items():
            if not vlakno.is_alive():
                raise RuntimeError(
                    f"Služba '{nazev}' se neočekávaně ukončila."
                )

        time.sleep(SERVICE_CHECK_SECONDS)


def main() -> None:
    """Spusti Installer a po registraci cloudove sluzby."""

    logging.info("IQ FANDA Agent Core spuštěn.")

    installer_api_thread = vytvorit_vlakno(
        cil=spustit_installer_api,
        nazev="installer-api",
    )
    installer_api_thread.start()

    logging.info("Installer API bylo spuštěno.")

    cekat_na_registraci_zarizeni(
        installer_api_thread=installer_api_thread,
    )

    logging.info(
        "Identita zařízení je připravena. "
        "Spouštím cloudové služby."
    )

    host_api_thread = vytvorit_vlakno(
        cil=spustit_host_api,
        nazev="host-api",
    )

    device_config_thread = vytvorit_vlakno(
        cil=spustit_synchronizaci_konfigurace,
        nazev="device-config-sync",
    )

    heartbeat_thread = vytvorit_vlakno(
        cil=spustit_heartbeat,
        nazev="heartbeat",
    )

    host_api_thread.start()
    device_config_thread.start()
    heartbeat_thread.start()

    kontrolovat_sluzby(
        {
            "installer-api": installer_api_thread,
            "host-api": host_api_thread,
            "device-config-sync": device_config_thread,
            "heartbeat": heartbeat_thread,
        }
    )