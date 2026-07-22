#!/usr/bin/env python3

import threading
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SetupState(str, Enum):
    IDLE = "idle"
    CONNECTING_WIFI = "connecting_wifi"
    WAITING_FOR_NETWORK = "waiting_for_network"
    REGISTERING = "registering"
    HEARTBEAT = "heartbeat"
    FINISHED = "finished"
    ERROR = "error"


class SetupManager:
    """
    ĹĂ­dĂ­ stav onboardingu zaĹ™Ă­zenĂ­.

    V prvnĂ­ etapÄ› pouze bezpeÄŤnÄ› uklĂˇdĂˇ a vracĂ­ stav.
    VlastnĂ­ pĹ™ipojenĂ­ Wi-Fi, registraci a vypnutĂ­ AP
    doplnĂ­me v dalĹˇĂ­ch krocĂ­ch.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {
            "state": SetupState.IDLE.value,
            "progress": 0,
            "message": "Setup Manager je pĹ™ipraven.",
            "error": None,
            "started_at": None,
            "updated_at": self._utc_now(),
            "finished_at": None,
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
        PĹ™ipojĂ­ zaĹ™Ă­zenĂ­ k vybranĂ© Wi-Fi sĂ­ti.
        """
        self.set_status(
            SetupState.CONNECTING_WIFI,
            15,
            f"PĹ™ipojuji zaĹ™Ă­zenĂ­ k Wi-Fi {ssid}.",
        )

        result = connector(
            ssid=ssid,
            password=password,
            interface=interface,
        )

        if not isinstance(result, dict):
            raise RuntimeError(
                "Wi-Fi konektor vrĂˇtil neplatnĂ˝ vĂ˝sledek."
            )

        if not result.get("ok"):
            raise RuntimeError(
                str(
                    result.get("error")
                    or "PĹ™ipojenĂ­ k Wi-Fi se nezdaĹ™ilo."
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
        ÄŚekĂˇ na dostupnost odchozĂ­ho internetovĂ©ho pĹ™ipojenĂ­.
        """
        from time import monotonic, sleep

        self.set_status(
            SetupState.WAITING_FOR_NETWORK,
            35,
            "Wi-Fi je pĹ™ipojena. ÄŚekĂˇm na dostupnost internetu.",
        )

        deadline = monotonic() + timeout_seconds
        last_error = "InternetovĂ© pĹ™ipojenĂ­ zatĂ­m nenĂ­ dostupnĂ©."

        while monotonic() < deadline:
            result = network_checker()

            if (
                isinstance(result, dict)
                and result.get("ok")
            ):
                return result

            if isinstance(result, dict):
                last_error = str(
                    result.get("error")
                    or last_error
                )
            else:
                last_error = (
                    "Kontrola internetu vrĂˇtila neplatnĂ˝ vĂ˝sledek."
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
        Zaregistruje zaĹ™Ă­zenĂ­ v cloudu a bezpeÄŤnÄ› uloĹľĂ­ jeho token.
        """
        self.set_status(
            SetupState.REGISTERING,
            55,
            "Registruji ĂşÄŤet, objekt a zaĹ™Ă­zenĂ­ v IQ FANDA Cloud.",
        )

        result = registrar(**registration)

        if not isinstance(result, dict):
            raise RuntimeError(
                "CloudovĂˇ registrace vrĂˇtila neplatnĂ˝ vĂ˝sledek."
            )

        if not result.get("ok"):
            raise RuntimeError(
                str(
                    result.get("error")
                    or "Registrace zaĹ™Ă­zenĂ­ se nezdaĹ™ila."
                )
            )

        data = result.get("data")

        if not isinstance(data, dict):
            raise RuntimeError(
                "Cloud nevrĂˇtil registraÄŤnĂ­ Ăşdaje zaĹ™Ă­zenĂ­."
            )

        save_result = credential_saver(data)

        if not isinstance(save_result, dict):
            raise RuntimeError(
                "ĂšloĹľiĹˇtÄ› identity vrĂˇtilo neplatnĂ˝ vĂ˝sledek."
            )

        if not save_result.get("ok"):
            raise RuntimeError(
                str(
                    save_result.get("error")
                    or "Identitu zaĹ™Ă­zenĂ­ se nepodaĹ™ilo uloĹľit."
                )
            )

        return {
            "cloud": data,
            "storage": save_result,
        }

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
        HlavnĂ­ orchestrĂˇtor onboardingu zaĹ™Ă­zenĂ­.

        InstalaÄŤnĂ­ AP se v tĂ©to fĂˇzi nevypĂ­nĂˇ ani nemaĹľe.
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

            self._register_device(
                registrar=registrar,
                credential_saver=credential_saver,
                registration=registration,
            )

            self.set_status(
                SetupState.HEARTBEAT,
                75,
                "ZaĹ™Ă­zenĂ­ je zaregistrovĂˇno. PĹ™ipravuji prvnĂ­ heartbeat.",
            )

        except Exception as exc:
            current_state = self.get_status().get("state")

            if current_state == SetupState.CONNECTING_WIFI.value:
                message = "PĹ™ipojenĂ­ k Wi-Fi se nezdaĹ™ilo."
                progress = 15
            elif current_state == SetupState.WAITING_FOR_NETWORK.value:
                message = "NepodaĹ™ilo se ovÄ›Ĺ™it pĹ™Ă­stup k internetu."
                progress = 35
            elif current_state == SetupState.REGISTERING.value:
                message = "Registrace zaĹ™Ă­zenĂ­ v cloudu se nezdaĹ™ila."
                progress = 55
            else:
                message = "BÄ›hem onboardingu nastala chyba."
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
        ZahĂˇjĂ­ onboarding v samostatnĂ©m pracovnĂ­m vlĂˇknÄ›.
        """
        from threading import Thread

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
        # Sériové číslo a další hardwarové údaje jsou pevně
        # přidělené při výrobě. Uživatel je během onboardingu nezadává.
        from identity import identity_service

        try:
            device_identity = identity_service.load()
        except (OSError, ValueError, TypeError) as exc:
            return {
                "ok": False,
                "error": (
                    "Zařízení nemá platnou výrobní identitu: "
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
                "error": "SSID nesmĂ­ bĂ˝t prĂˇzdnĂ©.",
                "setup": self.get_status(),
            }

        if len(password) < 8:
            return {
                "ok": False,
                "error": "Heslo Wi-Fi musĂ­ mĂ­t alespoĹ 8 znakĹŻ.",
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
                    "ChybĂ­ povinnĂ© registraÄŤnĂ­ Ăşdaje: "
                    + ", ".join(missing_registration_fields)
                ),
                "setup": self.get_status(),
            }

        if len(account_password) < 10:
            return {
                "ok": False,
                "error": (
                    "Heslo cloudovĂ©ho ĂşÄŤtu musĂ­ mĂ­t "
                    "alespoĹ 10 znakĹŻ."
                ),
                "setup": self.get_status(),
            }

        if "@" not in email:
            return {
                "ok": False,
                "error": "E-mailovĂˇ adresa nenĂ­ platnĂˇ.",
                "setup": self.get_status(),
            }

        if len(device_serial_number) < 3:
            return {
                "ok": False,
                "error": (
                    "SĂ©riovĂ© ÄŤĂ­slo zaĹ™Ă­zenĂ­ musĂ­ mĂ­t "
                    "alespoĹ 3 znaky."
                ),
                "setup": self.get_status(),
            }

        if not callable(connector):
            return {
                "ok": False,
                "error": "Wi-Fi konektor nenĂ­ dostupnĂ˝.",
                "setup": self.get_status(),
            }

        if not callable(network_checker):
            return {
                "ok": False,
                "error": "Kontrola internetu nenĂ­ dostupnĂˇ.",
                "setup": self.get_status(),
            }

        if not callable(registrar):
            return {
                "ok": False,
                "error": "CloudovĂˇ registrace nenĂ­ dostupnĂˇ.",
                "setup": self.get_status(),
            }

        if not callable(credential_saver):
            return {
                "ok": False,
                "error": "ĂšloĹľiĹˇtÄ› identity nenĂ­ dostupnĂ©.",
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
                    "error": "Onboarding jiĹľ probĂ­hĂˇ.",
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

        status = self.set_status(
            SetupState.CONNECTING_WIFI,
            10,
            f"PĹ™ipravuji pĹ™ipojenĂ­ k Wi-Fi {ssid}.",
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
                "message": "Setup Manager je pĹ™ipraven.",
                "error": None,
                "started_at": None,
                "updated_at": self._utc_now(),
                "finished_at": None,
                "wifi": None,
                "registration": None,
            }

            return deepcopy(self._status)


setup_manager = SetupManager()
