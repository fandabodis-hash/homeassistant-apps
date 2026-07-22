import logging
import threading
import time

from device_config import main as device_config_main
from heartbeat import main as heartbeat_main
from provisioning import ensure_device_is_provisioned


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


PROVISIONING_RETRY_SECONDS = 30


def run_heartbeat() -> None:
    heartbeat_main()


def run_device_config_sync() -> None:
    device_config_main()


def cekat_na_registraci_zarizeni() -> None:
    logging.info(
        "Kontroluji, zda je zařízení připraveno k registraci."
    )

    while True:
        try:
            ensure_device_is_provisioned()
            return

        except FileNotFoundError as chyba:
            logging.warning("%s", chyba)
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


def main() -> None:
    logging.info("IQ FANDA Agent Core spuštěn.")

    cekat_na_registraci_zarizeni()

    logging.info(
        "Identita zařízení je připravena. "
        "Spouštím cloudové služby."
    )

    device_config_thread = threading.Thread(
        target=run_device_config_sync,
        name="device-config-sync",
        daemon=True,
    )

    heartbeat_thread = threading.Thread(
        target=run_heartbeat,
        name="heartbeat",
        daemon=True,
    )

    device_config_thread.start()
    heartbeat_thread.start()

    while True:
        if not device_config_thread.is_alive():
            logging.error(
                "Vlákno synchronizace konfigurace se "
                "neočekávaně ukončilo."
            )
            break

        if not heartbeat_thread.is_alive():
            logging.error(
                "Heartbeat vlákno se neočekávaně ukončilo."
            )
            break

        time.sleep(5)

    raise RuntimeError(
        "Jedna z hlavních služeb Agenta se ukončila."
    )


if __name__ == "__main__":
    main()
