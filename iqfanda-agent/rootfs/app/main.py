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


def run_heartbeat() -> None:
    heartbeat_main()


def run_device_config_sync() -> None:
    device_config_main()


def cekat_na_dokonceni_instalace() -> None:
    logging.warning(
        "Zařízení není zatím zaregistrováno. "
        "Agent čeká na dokončení instalace."
    )

    while True:
        time.sleep(30)


def main() -> None:
    logging.info("IQ FANDA Agent Core spuštěn.")

    try:
        ensure_device_is_provisioned()
    except FileNotFoundError as chyba:
        logging.warning("%s", chyba)
        cekat_na_dokonceni_instalace()
        return
    except Exception:
        logging.exception(
            "Provisioning zařízení selhal. "
            "Heartbeat ani synchronizace konfigurace "
            "nebudou spuštěny."
        )
        cekat_na_dokonceni_instalace()
        return

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
