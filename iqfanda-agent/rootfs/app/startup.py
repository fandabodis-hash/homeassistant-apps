"""Koordinator startu sluzeb TNG IQ FANDA Agentu."""

import logging
import threading
import time

from communication.communication_manager import main as communication_manager_main
from device_config import main as device_config_main
from heartbeat import main as heartbeat_main
from host.access_point_manager import access_point_manager
from host.iqf_host_api import main as host_api_main
from installer.access_point_service import request_access_point
from installer.installer_api import spustit_api
from provisioning import DEVICE_CONFIG_PATH
from usb_inventory import main as usb_inventory_main


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


def spustit_access_point_manager() -> None:
    """Spusti spravce instalacniho Access Pointu."""

    access_point_manager.run_forever()


def spustit_usb_inventory() -> None:
    """Spusti automatickou inventarizaci USB zarizeni."""

    usb_inventory_main()


def spustit_communication_manager() -> None:
    """Spusti spravu komunikacniho centra."""

    communication_manager_main()


def cekat_na_registraci_zarizeni(
    installer_api_thread: threading.Thread,
    host_api_thread: threading.Thread,
    access_point_thread: threading.Thread,
) -> None:
    """Ceka na vytvoreni identity zarizeni."""

    logging.info(
        "Kontroluji, zda je zarizeni pripraveno k registraci."
    )

    access_point_requested = False

    while True:
        if not installer_api_thread.is_alive():
            raise RuntimeError(
                "Installer API se behem cekani na registraci ukoncilo."
            )

        if not host_api_thread.is_alive():
            raise RuntimeError(
                "Host API se behem cekani na registraci ukoncilo."
            )

        if not access_point_thread.is_alive():
            raise RuntimeError(
                "Access Point Manager se behem cekani "
                "na registraci ukoncil."
            )

        if DEVICE_CONFIG_PATH.exists():
            logging.info(
                "Identita zarizeni byla vytvorena: %s",
                DEVICE_CONFIG_PATH,
            )
            return

        if not access_point_requested:
            try:
                vysledek_ap = request_access_point(
                    reason="device_not_provisioned",
                )

                logging.info(
                    "Access Point Manager byl pozadan "
                    "o spusteni instalacniho Access Pointu: %s",
                    vysledek_ap.get("path"),
                )

                access_point_requested = True

            except Exception:
                logging.exception(
                    "Pozadavek na spusteni instalacniho "
                    "Access Pointu se nepodarilo ulozit."
                )

        logging.info(
            "Zarizeni ceka na dokonceni instalace "
            "a vytvoreni identity. "
            "Dalsi kontrola za %s sekund.",
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
    """Prubezne kontroluje beh hlavnich sluzeb."""

    while True:
        for nazev, vlakno in vlakna.items():
            if not vlakno.is_alive():
                raise RuntimeError(
                    f"Sluzba '{nazev}' se neocekavane ukoncila."
                )

        time.sleep(SERVICE_CHECK_SECONDS)


def main() -> None:
    """Spusti lokalni API a po registraci cloudove sluzby."""

    logging.info("IQ FANDA Agent Core spusten.")

    installer_api_thread = vytvorit_vlakno(
        cil=spustit_installer_api,
        nazev="installer-api",
    )

    host_api_thread = vytvorit_vlakno(
        cil=spustit_host_api,
        nazev="host-api",
    )

    access_point_thread = vytvorit_vlakno(
        cil=spustit_access_point_manager,
        nazev="access-point-manager",
    )

    installer_api_thread.start()
    host_api_thread.start()
    access_point_thread.start()

    logging.info("Installer API bylo spusteno.")
    logging.info("Host API bylo spusteno.")
    logging.info("Access Point Manager byl spusten.")

    cekat_na_registraci_zarizeni(
        installer_api_thread=installer_api_thread,
        host_api_thread=host_api_thread,
        access_point_thread=access_point_thread,
    )

    logging.info(
        "Identita zarizeni je pripravena. "
        "Spoustim cloudove sluzby."
    )

    device_config_thread = vytvorit_vlakno(
        cil=spustit_synchronizaci_konfigurace,
        nazev="device-config-sync",
    )

    heartbeat_thread = vytvorit_vlakno(
        cil=spustit_heartbeat,
        nazev="heartbeat",
    )

    usb_inventory_thread = vytvorit_vlakno(
        cil=spustit_usb_inventory,
        nazev="usb-inventory",
    )

    communication_manager_thread = vytvorit_vlakno(
        cil=spustit_communication_manager,
        nazev="communication-manager",
    )

    device_config_thread.start()
    heartbeat_thread.start()
    usb_inventory_thread.start()
    communication_manager_thread.start()

    kontrolovat_sluzby(
        {
            "installer-api": installer_api_thread,
            "host-api": host_api_thread,
            "access-point-manager": access_point_thread,
            "device-config-sync": device_config_thread,
            "heartbeat": heartbeat_thread,
            "usb-inventory": usb_inventory_thread,
            "communication-manager": communication_manager_thread,
        }
    )


if __name__ == "__main__":
    main()
