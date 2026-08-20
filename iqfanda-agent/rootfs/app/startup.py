"""Koordinator startu sluzeb TNG IQ FANDA Agentu."""

import logging
import threading
import time

from command_worker import main as command_worker_main
from communication.communication_manager import main as communication_manager_main
from device_config import main as device_config_main
from heartbeat import main as heartbeat_main
from host.access_point_manager import access_point_manager
from host.iqf_host_api import main as host_api_main
from installer.access_point_service import (
    release_access_point,
    request_access_point,
)
from installer_v2.ap_workflow import (
    SERVICE_WINDOW_SECONDS,
)
from installer_v2.installer_api import (
    service_access_point_ssid,
    spustit_api,
)
from provisioning import DEVICE_CONFIG_PATH
from telemetrie_modulu import (
    main as telemetrie_modulu_main,
)
from usb_inventory import main as usb_inventory_main
from zha_bootstrap import main as zha_bootstrap_main


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


PROVISIONING_RETRY_SECONDS = 30
SERVICE_CHECK_SECONDS = 5


def spustit_heartbeat() -> None:
    """Spusti heartbeat sluzbu."""

    heartbeat_main()


def spustit_command_worker() -> None:
    """Spusti vykonavatele cloudovych prikazu."""

    command_worker_main()


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


def spustit_servisni_okno_po_startu() -> None:
    """
    Pri startu jiz instalovaneho IQ FANDA
    spusti servisni AP na 60 sekund.

    Pokud instalater behem okna zahaji zmenu Wi-Fi,
    casovac servisni AP jiz nevypina.
    """

    if not DEVICE_CONFIG_PATH.exists():
        return

    try:
        ssid = (
            service_access_point_ssid()
        )

        result = request_access_point(
            reason="installed_boot_window",
            ssid=ssid,
        )

        logging.warning(
            "Spoustim servisni AP %s "
            "na %s sekund: %s",
            ssid,
            SERVICE_WINDOW_SECONDS,
            result.get("path"),
        )

        deadline = (
            time.monotonic()
            + 15.0
        )

        while (
            time.monotonic()
            < deadline
        ):
            try:
                if (
                    access_point_manager
                    .is_access_point_active()
                ):
                    break

            except Exception:
                pass

            time.sleep(0.2)

        else:
            raise RuntimeError(
                "Servisni AP se po startu "
                "neaktivovalo."
            )

        logging.info(
            "Servisni AP %s je aktivni. "
            "Servisni okno: %s sekund.",
            ssid,
            SERVICE_WINDOW_SECONDS,
        )

        time.sleep(
            SERVICE_WINDOW_SECONDS
        )

        current_request = (
            access_point_manager
            .load_request()
        )

        if (
            not isinstance(
                current_request,
                dict,
            )
            or not current_request.get(
                "requested"
            )
            or str(
                current_request.get(
                    "reason"
                )
                or ""
            )
            != "installed_boot_window"
        ):
            logging.info(
                "Servisni AP bylo behem "
                "60s okna prevzato jinym "
                "workflow. Casovac jej nevypina."
            )
            return

        release_access_point(
            reason=(
                "installed_boot_window_expired"
            )
        )

        logging.info(
            "Servisni okno IQ FANDA "
            "po %s sekundach skoncilo.",
            SERVICE_WINDOW_SECONDS,
        )

    except Exception:
        logging.exception(
            "Servisni AP po startu "
            "se nepodarilo obslouzit."
        )


def spustit_usb_inventory() -> None:
    """Spusti automatickou inventarizaci USB zarizeni."""

    usb_inventory_main()


def spustit_communication_manager() -> None:
    """Spusti spravu komunikacniho centra."""

    communication_manager_main()


def spustit_telemetrii_modulu() -> None:
    """Spusti periodickou telemetrii modulu."""

    telemetrie_modulu_main()


def spustit_zha_bootstrap() -> None:
    """Spusti automatickou inicializaci ZHA."""

    zha_bootstrap_main()


def cekat_na_registraci_zarizeni(
    installer_api_thread: threading.Thread,
    host_api_thread: threading.Thread,
    access_point_thread: threading.Thread,
) -> None:
    """Ceka na dokonceni cloudove registrace zarizeni."""

    logging.info(
        "Kontroluji, zda je dokoncena cloudova registrace zarizeni."
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
                "Cloudova identita zarizeni byla vytvorena: %s",
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
            "a cloudovou registraci. "
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

    zarizeni_bylo_nainstalovane_pri_startu = (
        DEVICE_CONFIG_PATH.exists()
    )

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

    if zarizeni_bylo_nainstalovane_pri_startu:
        service_wifi_thread = vytvorit_vlakno(
            cil=spustit_servisni_okno_po_startu,
            nazev="service-wifi-boot-window",
        )

        service_wifi_thread.start()

    else:
        logging.info(
            "Prvni instalace byla dokoncena "
            "v tomto behu Agentu. "
            "Servisni 60s boot okno se nyn? "
            "znovu nespousti."
        )

    device_config_thread = vytvorit_vlakno(
        cil=spustit_synchronizaci_konfigurace,
        nazev="device-config-sync",
    )

    heartbeat_thread = vytvorit_vlakno(
        cil=spustit_heartbeat,
        nazev="heartbeat",
    )

    command_worker_thread = vytvorit_vlakno(
        cil=spustit_command_worker,
        nazev="command-worker",
    )

    usb_inventory_thread = vytvorit_vlakno(
        cil=spustit_usb_inventory,
        nazev="usb-inventory",
    )

    communication_manager_thread = vytvorit_vlakno(
        cil=spustit_communication_manager,
        nazev="communication-manager",
    )

    module_telemetry_thread = vytvorit_vlakno(
        cil=spustit_telemetrii_modulu,
        nazev="module-telemetry",
    )

    zha_bootstrap_thread = vytvorit_vlakno(
        cil=spustit_zha_bootstrap,
        nazev="zha-bootstrap",
    )

    device_config_thread.start()
    heartbeat_thread.start()
    command_worker_thread.start()
    usb_inventory_thread.start()
    communication_manager_thread.start()
    module_telemetry_thread.start()
    zha_bootstrap_thread.start()

    kontrolovat_sluzby(
        {
            "installer-api": installer_api_thread,
            "host-api": host_api_thread,
            "access-point-manager": access_point_thread,
            "device-config-sync": device_config_thread,
            "heartbeat": heartbeat_thread,
            "command-worker": command_worker_thread,
            "usb-inventory": usb_inventory_thread,
            "communication-manager": communication_manager_thread,
            "module-telemetry": module_telemetry_thread,
            "zha-bootstrap": zha_bootstrap_thread,
        }
    )


if __name__ == "__main__":
    main()
