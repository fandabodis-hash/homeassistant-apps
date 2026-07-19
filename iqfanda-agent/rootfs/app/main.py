import logging
import threading
import time

from device_config import main as spust_synchronizaci_konfigurace
from heartbeat import main as spust_heartbeat
from provisioning import zajisti_registraci_zarizeni


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def main() -> None:
    logging.info("IQ FANDA Agent Core spusten.")

    try:
        zajisti_registraci_zarizeni()
    except Exception:
        logging.exception(
            "Provisioning zarizeni selhal. "
            "Heartbeat ani synchronizace konfigurace "
            "nebudou spusteny."
        )
        raise

    logging.info(
        "Identita zarizeni je pripravena. "
        "Spoustim cloudove sluzby."
    )

    vlakno_konfigurace = threading.Thread(
        target=spust_synchronizaci_konfigurace,
        name="synchronizace-konfigurace",
        daemon=True,
    )

    vlakno_heartbeat = threading.Thread(
        target=spust_heartbeat,
        name="heartbeat",
        daemon=True,
    )

    vlakno_konfigurace.start()
    vlakno_heartbeat.start()

    while True:
        if not vlakno_konfigurace.is_alive():
            logging.error(
                "Vlakno synchronizace konfigurace se "
                "neocekavane ukoncilo."
            )
            break

        if not vlakno_heartbeat.is_alive():
            logging.error(
                "Heartbeat vlakno se neocekavane ukoncilo."
            )
            break

        time.sleep(5)

    raise RuntimeError(
        "Jedna z hlavnich sluzeb Agenta se ukoncila."
    )


if __name__ == "__main__":
    main()