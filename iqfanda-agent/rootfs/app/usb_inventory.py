"""Automaticka inventarizace USB zarizeni TNG IQ FANDA."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


USB_SYSFS_ROOT = Path("/sys/bus/usb/devices")
SERIAL_BY_ID_ROOT = Path("/dev/serial/by-id")
OUTPUT_PATH = Path(
    os.getenv(
        "IQF_USB_INVENTORY_PATH",
        "/config/usb_inventory.json",
    )
)

SCAN_INTERVAL_SECONDS = 5


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def nacti_text(
    cesta: Path,
) -> str | None:
    """Bezpecne nacte kratkou hodnotu ze sysfs."""

    try:
        hodnota = cesta.read_text(
            encoding="utf-8",
            errors="replace",
        ).strip()
    except (
        FileNotFoundError,
        PermissionError,
        OSError,
    ):
        return None

    return hodnota or None


def normalizuj_hex(
    hodnota: str | None,
) -> str | None:
    """Vrati VID nebo PID velkymi pismeny."""

    if not hodnota:
        return None

    return hodnota.strip().upper()


def najdi_stabilni_serial_cesty() -> dict[str, str]:
    """Vrati mapu skutecneho /dev portu na /dev/serial/by-id."""

    vysledek: dict[str, str] = {}

    if not SERIAL_BY_ID_ROOT.exists():
        return vysledek

    try:
        polozky = sorted(
            SERIAL_BY_ID_ROOT.iterdir(),
            key=lambda cesta: cesta.name.lower(),
        )
    except OSError:
        logging.exception(
            "Nepodarilo se nacist %s.",
            SERIAL_BY_ID_ROOT,
        )
        return vysledek

    for polozka in polozky:
        try:
            cil = polozka.resolve(strict=True)
        except (
            FileNotFoundError,
            OSError,
        ):
            continue

        vysledek[str(cil)] = str(polozka)

    return vysledek


def najdi_tty_porty(
    usb_cesta: Path,
    stabilni_cesty: dict[str, str],
) -> list[dict[str, str | None]]:
    """Najde ttyUSB/ttyACM porty nalezejici USB zarizeni."""

    porty: list[dict[str, str | None]] = []
    nalezene_nazvy: set[str] = set()

    try:
        kandidati = usb_cesta.rglob("tty*")
    except OSError:
        return porty

    for kandidat in kandidati:
        nazev = kandidat.name

        if not (
            nazev.startswith("ttyUSB")
            or nazev.startswith("ttyACM")
        ):
            continue

        if nazev in nalezene_nazvy:
            continue

        nalezene_nazvy.add(nazev)

        cesta_portu = f"/dev/{nazev}"

        porty.append(
            {
                "port": cesta_portu,
                "stable_path": stabilni_cesty.get(
                    cesta_portu
                ),
            }
        )

    return sorted(
        porty,
        key=lambda polozka: polozka["port"] or "",
    )


def klasifikuj_zarizeni(
    vyrobce: str | None,
    produkt: str | None,
    vid: str | None,
    pid: str | None,
    porty: list[dict[str, str | None]],
) -> tuple[str, str]:
    """Provede zakladni bezpecnou klasifikaci USB prvku."""

    popis = " ".join(
        hodnota
        for hodnota in (
            vyrobce,
            produkt,
        )
        if hodnota
    ).lower()

    zigbee_vyrazy = (
        "zigbee",
        "sonoff",
        "conbee",
        "skyconnect",
        "zbdongle",
        "cc2531",
        "cc2652",
        "efr32",
        "ember",
    )

    rs485_vyrazy = (
        "rs485",
        "rs-485",
        "modbus",
    )

    rs232_vyrazy = (
        "rs232",
        "rs-232",
    )

    if any(vyraz in popis for vyraz in zigbee_vyrazy):
        return (
            "zigbee_coordinator",
            "Zigbee USB koordinátor",
        )

    if any(vyraz in popis for vyraz in rs485_vyrazy):
        return (
            "usb_rs485",
            "USB/RS485 převodník",
        )

    if any(vyraz in popis for vyraz in rs232_vyrazy):
        return (
            "usb_rs232",
            "USB/RS232 převodník",
        )

    # Známé čipy sériových převodníků. Samotný čip nemusí
    # jednoznačně určit, zda je elektrické rozhraní RS485
    # nebo RS232, proto jej označujeme obecně.
    serial_vid_pid = {
        ("0403", "6001"),  # FTDI FT232
        ("0403", "6015"),  # FTDI FT231X
        ("067B", "2303"),  # Prolific PL2303
        ("1A86", "7523"),  # QinHeng CH340
        ("1A86", "5523"),  # QinHeng CH341
        ("10C4", "EA60"),  # Silicon Labs CP210x
    }

    if (
        (vid, pid) in serial_vid_pid
        or porty
    ):
        return (
            "usb_serial",
            "USB sériový převodník",
        )

    return (
        "usb_device",
        "USB zařízení",
    )


def vytvor_identifikator(
    sysfs_name: str,
    vid: str | None,
    pid: str | None,
    serial_number: str | None,
) -> str:
    """Vytvori stabilni technicky identifikator zaznamu."""

    zdroj = "|".join(
        (
            vid or "",
            pid or "",
            serial_number or "",
            sysfs_name,
        )
    )

    return hashlib.sha256(
        zdroj.encode("utf-8")
    ).hexdigest()[:24]


def nacti_usb_zarizeni() -> list[dict[str, Any]]:
    """Nacte fyzicka USB zarizeni dostupna v kontejneru."""

    if not USB_SYSFS_ROOT.exists():
        logging.warning(
            "USB sysfs neni dostupne: %s",
            USB_SYSFS_ROOT,
        )
        return []

    stabilni_cesty = najdi_stabilni_serial_cesty()
    zarizeni: list[dict[str, Any]] = []

    try:
        usb_polozky = sorted(
            USB_SYSFS_ROOT.iterdir(),
            key=lambda cesta: cesta.name,
        )
    except OSError:
        logging.exception(
            "Nepodarilo se nacist USB sysfs."
        )
        return []

    for usb_cesta in usb_polozky:
        vid = normalizuj_hex(
            nacti_text(usb_cesta / "idVendor")
        )
        pid = normalizuj_hex(
            nacti_text(usb_cesta / "idProduct")
        )

        # Slozky rozhrani a pomocne polozky nemaji VID/PID.
        if not vid or not pid:
            continue

        vyrobce = nacti_text(
            usb_cesta / "manufacturer"
        )
        produkt = nacti_text(
            usb_cesta / "product"
        )
        serial_number = nacti_text(
            usb_cesta / "serial"
        )
        usb_version = nacti_text(
            usb_cesta / "version"
        )
        speed_mbps = nacti_text(
            usb_cesta / "speed"
        )

        porty = najdi_tty_porty(
            usb_cesta=usb_cesta,
            stabilni_cesty=stabilni_cesty,
        )

        typ, typ_nazev = klasifikuj_zarizeni(
            vyrobce=vyrobce,
            produkt=produkt,
            vid=vid,
            pid=pid,
            porty=porty,
        )

        zarizeni.append(
            {
                "inventory_id": vytvor_identifikator(
                    sysfs_name=usb_cesta.name,
                    vid=vid,
                    pid=pid,
                    serial_number=serial_number,
                ),
                "type": typ,
                "type_name": typ_nazev,
                "manufacturer": vyrobce,
                "product": produkt,
                "vid": vid,
                "pid": pid,
                "serial_number": serial_number,
                "usb_version": usb_version,
                "speed_mbps": speed_mbps,
                "sysfs_name": usb_cesta.name,
                "sysfs_path": str(usb_cesta),
                "serial_ports": porty,
                "connected": True,
            }
        )

    return sorted(
        zarizeni,
        key=lambda polozka: (
            str(polozka.get("type_name") or ""),
            str(polozka.get("manufacturer") or ""),
            str(polozka.get("product") or ""),
            str(polozka.get("inventory_id") or ""),
        ),
    )


def vypocitej_otisk(
    zarizeni: list[dict[str, Any]],
) -> str:
    """Vypocita otisk aktualniho inventare."""

    obsah = json.dumps(
        zarizeni,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        obsah.encode("utf-8")
    ).hexdigest()


def uloz_inventar(
    zarizeni: list[dict[str, Any]],
    otisk: str,
) -> None:
    """Atomicky ulozi USB inventar."""

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "inventory_hash": otisk,
        "device_count": len(zarizeni),
        "devices": zarizeni,
    }

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    docasna_cesta = OUTPUT_PATH.with_suffix(
        OUTPUT_PATH.suffix + ".tmp"
    )

    with docasna_cesta.open(
        "w",
        encoding="utf-8",
    ) as soubor:
        json.dump(
            payload,
            soubor,
            ensure_ascii=False,
            indent=2,
        )
        soubor.flush()
        os.fsync(soubor.fileno())

    os.replace(
        docasna_cesta,
        OUTPUT_PATH,
    )


def zaloguj_zmenu(
    zarizeni: list[dict[str, Any]],
) -> None:
    """Zapise prehled zarizeni po zmene inventare."""

    logging.info(
        "USB inventar se zmenil. Nalezeno zarizeni: %s.",
        len(zarizeni),
    )

    if not zarizeni:
        logging.info(
            "Neni pripojeno zadne viditelne USB zarizeni."
        )
        return

    for polozka in zarizeni:
        porty = polozka.get("serial_ports") or []

        port_text = ", ".join(
            (
                port.get("stable_path")
                or port.get("port")
                or "bez portu"
            )
            for port in porty
        )

        if not port_text:
            port_text = "bez sériového portu"

        logging.info(
            "USB: typ=%s, vyrobce=%s, produkt=%s, "
            "VID=%s, PID=%s, serial=%s, port=%s",
            polozka.get("type_name") or "USB zařízení",
            polozka.get("manufacturer") or "neuveden",
            polozka.get("product") or "neuveden",
            polozka.get("vid") or "neuveden",
            polozka.get("pid") or "neuveden",
            polozka.get("serial_number") or "neuveden",
            port_text,
        )


def main() -> None:
    """Prubezne sleduje USB inventar."""

    logging.info(
        "USB inventory sluzba spustena. "
        "Vystup: %s, interval: %s s.",
        OUTPUT_PATH,
        SCAN_INTERVAL_SECONDS,
    )

    posledni_otisk: str | None = None

    while True:
        try:
            zarizeni = nacti_usb_zarizeni()
            otisk = vypocitej_otisk(zarizeni)

            if otisk != posledni_otisk:
                uloz_inventar(
                    zarizeni=zarizeni,
                    otisk=otisk,
                )
                zaloguj_zmenu(zarizeni)
                posledni_otisk = otisk

        except Exception:
            logging.exception(
                "Kontrola USB inventare skoncila chybou."
            )

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
