#!/usr/bin/env python3

import logging
import threading
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from installer.access_point_service import release_access_point


class SetupState(str, Enum):
    IDLE = "idle"
    CONNECTING_WIFI = "connecting_wifi"
    WAITING_FOR_NETWORK = "waiting_for_network"
    REGISTERING = "registering"
    WAITING_FOR_HEARTBEAT = "waiting_for_heartbeat"
    WAITING_FOR_CLOUD_CONFIG = "waiting_for_cloud_config"
    FINISHED = "finished"
    ERROR = "error"


class SetupManager:
    """
    Ridi stav onboardingu zarizeni.

    Orchestruje pripojeni k Wi-Fi, overeni internetu,
    registraci v cloudu, prvni heartbeat a prvni stazeni
    cloudove konfigurace.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {
            "state": SetupState.IDLE.value,
            "progress": 0,
            "message": "Setup Manager je pripraven.",
            "error": None,
            "started_at": None,
            "updated_at": self._utc_now(),
            "finished_at": None,
            "wifi": None,
            "registration": None,
        }

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._status)

    def set_status(
        self,
        state: SetupState,
        progress: int,
        message: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        progress = max(0, min(100, int(progress)))

        with self._lock:
            if (
                state != SetupState.IDLE
                and self._status["started_at"] is None
            ):
                self._status["started_at"] = self._utc_now()

            self._status["state"] = state.value
            self._status["progress"] = progress
            self._status["message"] = message
            self._status["error"] = error
            self._status["updated_at"] = self._utc_now()

            if state in (SetupState.FINISHED, SetupState.ERROR):
                self._status["finished_at"] = self._utc_now()
            else:
                self._status["finished_at"] = None

            return deepcopy(self._status)

    def _connect_wifi(
        self,
        connector: Any,
        ssid: str,
        password: str,
        interface: str,
    ) -> dict[str, Any]:
        """
        Pripoji zarizeni k vybrane Wi-Fi siti.
        """
        self.set_status(
            SetupState.CONNECTING_WIFI,
            15,
            f"Pripojuji zarizeni k Wi-Fi {ssid}.",
        )

        result = connector(
            ssid=ssid,
            password=password,
            interface=interface,
        )

        if not isinstance(result, dict):
            raise RuntimeError(
                "Wi-Fi konektor vratil neplatny vysledek."
            )

        if not result.get("ok"):
            raise RuntimeError(
                str(
                    result.get("error")
                    or "Pripojeni k Wi-Fi se nezdarilo."
                )
            )

        return result

    def _wait_for_network(
        self,
        network_checker: Any,
        timeout_seconds: int = 45,
        interval_seconds: int = 3,
    ) -> dict[str, Any]:
        """
        Ceka na dostupnost odchoziho internetoveho pripojeni.
        """
        from time import monotonic, sleep

        self.set_status(
            SetupState.WAITING_FOR_NETWORK,
            35,
            "Wi-Fi je pripojena. Cekam na dostupnost internetu.",
        )

        deadline = monotonic() + timeout_seconds
        last_error = "Internetove pripojeni zatim neni dostupne."

        while monotonic() < deadline:
            result = network_checker()

            if isinstance(result, dict) and result.get("ok"):
                return result

            if isinstance(result, dict):
                last_error = str(
                    result.get("error")
                    or last_error
                )
            else:
                last_error = (
                    "Kontrola internetu vratila neplatny vysledek."
                )

            sleep(interval_seconds)

        raise RuntimeError(last_error)

    def _register_device(
        self,
        registrar: Any,
        credential_saver: Any,
        registration: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Zaregistruje zarizeni v cloudu a bezpecne ulozi jeho token.
        """
        self.set_status(
            SetupState.REGISTERING,
            55,
            "Registruji ucet, objekt a zarizeni v IQ FANDA Cloud.",
        )

        result = registrar(registration)

        if not isinstance(result, dict):
            raise RuntimeError(
                "Cloudova registrace vratila neplatny vysledek."
            )

        if not result.get("ok"):
            raise RuntimeError(
                str(
                    result.get("error")
                    or "Registrace zarizeni se nezdarila."
                )
            )

        data = result.get("data")

        if not isinstance(data, dict):
            raise RuntimeError(
                "Cloud nevratil registracni udaje zarizeni."
            )

        save_result = credential_saver(data)

        if not isinstance(save_result, dict):
            raise RuntimeError(
                "Uloziste identity vratilo neplatny vysledek."
            )

        if not save_result.get("ok"):
            raise RuntimeError(
                str(
                    save_result.get("error")
                    or "Identitu zarizeni se nepodarilo ulozit."
                )
            )

        return {
            "cloud": data,
            "storage": save_result,
        }

    def _send_first_heartbeat(self) -> dict[str, Any]:
        """
        Odesle prvni heartbeat s prave ulozenou identitou zarizeni.
        """
        from heartbeat import load_device_config, send_heartbeat

        self.set_status(
            SetupState.WAITING_FOR_HEARTBEAT,
            75,
            "Zarizeni je zaregistrovano. Odesilam prvni heartbeat.",
        )

        device_config = load_device_config()
        result = send_heartbeat(device_config)

        if not isinstance(result, dict):
            raise RuntimeError(
                "Prvni heartbeat vratil neplatnou odpoved."
            )

        return result

    def _sync_first_cloud_config(self) -> dict[str, Any]:
        """
        Stahne a ulozi prvni cloudovou konfiguraci zarizeni.
        """
        from device_config import sync_device_config

        self.set_status(
            SetupState.WAITING_FOR_CLOUD_CONFIG,
            90,
            "Heartbeat byl uspesny. Stahuji cloudovou konfiguraci.",
        )

        cloud_config = sync_device_config()

        if not isinstance(cloud_config, dict):
            raise RuntimeError(
                "Cloudova konfigurace ma neplatny format."
            )

        if not cloud_config.get("device_uuid"):
            raise RuntimeError(
                "Cloudova konfigurace neobsahuje device_uuid."
            )

        if cloud_config.get("config_version") is None:
            raise RuntimeError(
                "Cloudova konfigurace neobsahuje config_version."
            )

        return cloud_config

    def _run_setup(
        self,
        connector: Any,
        network_checker: Any,
        registrar: Any,
        credential_saver: Any,
        registration: dict[str, Any],
        ssid: str,
        password: str,
        interface: str,
    ) -> None:
        """
        Hlavni orchestrator onboardingu zarizeni.

        Instalacni AP se v teto fazi nevypina ani nemaze.
        """
        try:
            self._connect_wifi(
                connector=connector,
                ssid=ssid,
                password=password,
                interface=interface,
            )

            self._wait_for_network(
                network_checker=network_checker,
            )

            registration_result = self._register_device(
                registrar=registrar,
                credential_saver=credential_saver,
                registration=registration,
            )

            heartbeat_result = self._send_first_heartbeat()
            cloud_config = self._sync_first_cloud_config()

            try:
                access_point_result = release_access_point(
                    reason="onboarding_completed",
                )
                logging.info(
                    "Host Agent byl pozadan o ukonceni "
                    "instalacniho Access Pointu: %s",
                    access_point_result.get("path"),
                )

            except Exception:
                access_point_result = {
                    "ok": False,
                    "error": (
                        "Pozadavek na ukonceni "
                        "instalacniho Access Pointu "
                        "se nepodarilo ulozit."
                    ),
                }
                logging.exception(
                    "Pozadavek na ukonceni instalacniho "
                    "Access Pointu se nepodarilo ulozit. "
                    "Onboarding zustava uspesny."
                )

            with self._lock:
                self._status["result"] = {
                    "device_uuid": registration_result[
                        "cloud"
                    ].get("device_uuid"),
                    "device_status": registration_result[
                        "cloud"
                    ].get("device_status"),
                    "heartbeat": heartbeat_result,
                    "access_point": access_point_result,
                    "config_version": cloud_config.get(
                        "config_version"
                    ),
                    "active_modules": cloud_config.get(
                        "active_modules",
                        [],
                    ),
                }

            self.set_status(
                SetupState.FINISHED,
                100,
                "Zarizeni je pripojeno, zaregistrovano a pripraveno.",
            )

        except Exception as exc:
            current_state = self.get_status().get("state")

            if current_state == SetupState.CONNECTING_WIFI.value:
                message = "Pripojeni k Wi-Fi se nezdarilo."
                progress = 15
            elif current_state == SetupState.WAITING_FOR_NETWORK.value:
                message = "Nepodarilo se overit pristup k internetu."
                progress = 35
            elif current_state == SetupState.REGISTERING.value:
                message = "Registrace zarizeni v cloudu se nezdarila."
                progress = 55
            elif current_state == SetupState.WAITING_FOR_HEARTBEAT.value:
                message = "Prvni heartbeat se nezdaril."
                progress = 75
            elif current_state == SetupState.WAITING_FOR_CLOUD_CONFIG.value:
                message = "Prvni stazeni cloudove konfigurace se nezdarilo."
                progress = 90
            else:
                message = "Behem onboardingu nastala chyba."
                progress = 0

            self.set_status(
                SetupState.ERROR,
                progress,
                message,
                error=str(exc),
            )

    def start(
        self,
        ssid: str,
        password: str,
        provisioning_id: str,
        first_name: str,
        last_name: str,
        email: str,
        account_password: str,
        site_name: str,
        site_type: str,
        device_serial_number: str | None = None,
        device_name: str = "TNG IQ FANDA",
        software_version: str | None = None,
        interface: str = "wlan0",
        connector: Any = None,
        network_checker: Any = None,
        registrar: Any = None,
        credential_saver: Any = None,
    ) -> dict[str, Any]:
        """
        Zahaji onboarding v samostatnem pracovnim vlakne.
        """
        from threading import Thread
        from host.identity import identity_service

        ssid = str(ssid or "").strip()
        password = str(password or "")
        interface = str(interface or "wlan0").strip() or "wlan0"

        provisioning_id = str(provisioning_id or "").strip()
        first_name = str(first_name or "").strip()
        last_name = str(last_name or "").strip()
        email = str(email or "").strip().lower()
        account_password = str(account_password or "")
        site_name = str(site_name or "").strip()
        site_type = str(site_type or "house").strip() or "house"

        try:
            device_identity = identity_service.load()
        except (OSError, ValueError, TypeError) as exc:
            return {
                "ok": False,
                "error": (
                    "Zarizeni nema platnou vyrobni identitu: "
                    f"{exc}"
                ),
                "setup": self.get_status(),
            }

        device_serial_number = str(
            device_identity.get("serial_number") or ""
        ).strip().upper()

        device_model = str(
            device_identity.get("model") or ""
        ).strip()

        hardware_revision = str(
            device_identity.get("hardware_revision") or ""
        ).strip()

        device_hostname = str(
            device_identity.get("hostname") or ""
        ).strip()

        device_name = (
            str(device_name or "TNG IQ FANDA").strip()
            or "TNG IQ FANDA"
        )

        software_version = (
            str(software_version).strip()
            if software_version is not None
            else None
        )

        if not ssid:
            return {
                "ok": False,
                "error": "SSID nesmi byt prazdne.",
                "setup": self.get_status(),
            }

        if len(password) < 8:
            return {
                "ok": False,
                "error": "Heslo Wi-Fi musi mit alespon 8 znaku.",
                "setup": self.get_status(),
            }

        required_registration_fields = {
            "provisioning_id": provisioning_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "account_password": account_password,
            "site_name": site_name,
            "device_serial_number": device_serial_number,
            "device_model": device_model,
            "hardware_revision": hardware_revision,
            "device_hostname": device_hostname,
            "device_name": device_name,
        }

        missing_registration_fields = [
            field_name
            for field_name, field_value
            in required_registration_fields.items()
            if not field_value
        ]

        if missing_registration_fields:
            return {
                "ok": False,
                "error": (
                    "Chybi povinne registracni udaje: "
                    + ", ".join(missing_registration_fields)
                ),
                "setup": self.get_status(),
            }

        if len(account_password) < 10:
            return {
                "ok": False,
                "error": (
                    "Heslo cloudoveho uctu musi mit "
                    "alespon 10 znaku."
                ),
                "setup": self.get_status(),
            }

        if "@" not in email:
            return {
                "ok": False,
                "error": "E-mailova adresa neni platna.",
                "setup": self.get_status(),
            }

        if len(device_serial_number) < 3:
            return {
                "ok": False,
                "error": (
                    "Seriove cislo zarizeni musi mit "
                    "alespon 3 znaky."
                ),
                "setup": self.get_status(),
            }

        if not callable(connector):
            return {
                "ok": False,
                "error": "Wi-Fi konektor neni dostupny.",
                "setup": self.get_status(),
            }

        if not callable(network_checker):
            return {
                "ok": False,
                "error": "Kontrola internetu neni dostupna.",
                "setup": self.get_status(),
            }

        if not callable(registrar):
            return {
                "ok": False,
                "error": "Cloudova registrace neni dostupna.",
                "setup": self.get_status(),
            }

        if not callable(credential_saver):
            return {
                "ok": False,
                "error": "Uloziste identity neni dostupne.",
                "setup": self.get_status(),
            }

        with self._lock:
            current_state = self._status["state"]

            if current_state not in (
                SetupState.IDLE.value,
                SetupState.FINISHED.value,
                SetupState.ERROR.value,
            ):
                return {
                    "ok": False,
                    "error": "Onboarding jiz probiha.",
                    "setup": deepcopy(self._status),
                }

            self._status["wifi"] = {
                "ssid": ssid,
                "interface": interface,
            }

            self._status["registration"] = {
                "provisioning_id": provisioning_id,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "site_name": site_name,
                "site_type": site_type,
                "device_serial_number": device_serial_number,
                "device_model": device_model,
                "hardware_revision": hardware_revision,
                "device_hostname": device_hostname,
                "device_name": device_name,
                "software_version": software_version,
            }

            self._status["result"] = None

        status = self.set_status(
            SetupState.CONNECTING_WIFI,
            10,
            f"Pripravuji pripojeni k Wi-Fi {ssid}.",
        )

        worker = Thread(
            target=self._run_setup,
            kwargs={
                "connector": connector,
                "network_checker": network_checker,
                "registrar": registrar,
                "credential_saver": credential_saver,
                "registration": {
                    "provisioning_id": provisioning_id,
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "password": account_password,
                    "site_name": site_name,
                    "site_type": site_type,
                    "device_serial_number": device_serial_number,
                    "device_model": device_model,
                    "hardware_revision": hardware_revision,
                    "device_hostname": device_hostname,
                    "device_name": device_name,
                    "software_version": software_version,
                },
                "ssid": ssid,
                "password": password,
                "interface": interface,
            },
            name="iqf-setup-wifi",
            daemon=True,
        )
        worker.start()

        return {
            "ok": True,
            "setup": status,
        }

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._status = {
                "state": SetupState.IDLE.value,
                "progress": 0,
                "message": "Setup Manager je pripraven.",
                "error": None,
                "started_at": None,
                "updated_at": self._utc_now(),
                "finished_at": None,
                "wifi": None,
                "registration": None,
                "result": None,
            }

            return deepcopy(self._status)


setup_manager = SetupManager()
