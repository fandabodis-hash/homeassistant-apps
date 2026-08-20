"""Servisni zmena Wi-Fi pro jiz nainstalovany TNG IQ FANDA."""

from __future__ import annotations

import math
import os
import socket
import ssl
import tempfile
import threading
import time
from typing import Any

from host.access_point_manager import access_point_manager
from installer.access_point_service import (
    release_access_point,
    request_access_point,
)
from installer.network_manager import (
    check_internet_access,
    get_wifi_status,
    run_nmcli,
)
from installer_v2.models import InstallerMode


SERVICE_WINDOW_SECONDS = 60
WIFI_INTERFACE = "wlan0"
PENDING_PROFILE = "IQF Client WiFi Pending"
FINAL_PROFILE = "IQF Client WiFi"
BACKUP_PROFILE = "IQF Client WiFi Backup"
CLOUD_HOST = "api.tngiqfanda.cz"
CLOUD_PORT = 443

_STATE_LOCK = threading.RLock()
_OPERATION_LOCK = threading.Lock()

_mode = InstallerMode.INSTALLED_RUN
_deadline: float | None = None
_previous_profile: str | None = None
_service_ssid: str | None = None
_last_result: dict[str, Any] | None = None


def _command_error(
    result: Any,
    fallback: str,
) -> str:
    stderr = str(
        getattr(result, "stderr", "") or ""
    ).strip()
    stdout = str(
        getattr(result, "stdout", "") or ""
    ).strip()

    return stderr or stdout or fallback


def _set_last_result(
    result: dict[str, Any] | None,
) -> None:
    global _last_result

    with _STATE_LOCK:
        _last_result = result


def get_wifi_maintenance_state() -> dict[str, Any]:
    with _STATE_LOCK:
        mode = _mode
        deadline = _deadline
        service_ssid = _service_ssid
        last_result = (
            dict(_last_result)
            if isinstance(_last_result, dict)
            else None
        )

    remaining_seconds = None

    if (
        mode == InstallerMode.INSTALLED_BOOT_WINDOW
        and deadline is not None
    ):
        remaining_seconds = max(
            0,
            int(
                math.ceil(
                    deadline - time.monotonic()
                )
            ),
        )

    return {
        "ok": True,
        "mode": mode.value,
        "remaining_seconds": remaining_seconds,
        "service_ssid": service_ssid,
        "last_result": last_result,
    }


def _wait_ap_state(
    expected_active: bool,
    timeout_seconds: float = 20.0,
) -> bool:
    deadline = (
        time.monotonic()
        + max(
            1.0,
            float(timeout_seconds),
        )
    )

    while time.monotonic() < deadline:
        try:
            active = (
                access_point_manager
                .is_access_point_active()
            )

            if active is expected_active:
                return True

        except Exception:
            pass

        time.sleep(0.2)

    return False


def _detect_previous_profile() -> str | None:
    status = get_wifi_status()

    if (
        status.get("ok")
        and status.get("connected")
    ):
        candidate = str(
            status.get("connection_name") or ""
        ).strip()

        if (
            candidate
            and candidate
            != access_point_manager.profile_name
        ):
            return candidate

    result = run_nmcli(
        "-t",
        "-f",
        "NAME,TYPE",
        "connection",
        "show",
    )

    if result.returncode == 0:
        for line in result.stdout.splitlines():
            name, _, connection_type = line.partition(":")

            if (
                name.strip() == FINAL_PROFILE
                and connection_type.strip()
                in ("wifi", "802-11-wireless")
            ):
                return FINAL_PROFILE

    return None


def _restore_previous_profile() -> dict[str, Any]:
    with _STATE_LOCK:
        profile = _previous_profile

    if profile:
        result = run_nmcli(
            "connection",
            "up",
            profile,
            "ifname",
            WIFI_INTERFACE,
        )

        if result.returncode == 0:
            return {
                "ok": True,
                "profile_name": profile,
            }

        return {
            "ok": False,
            "profile_name": profile,
            "error": _command_error(
                result,
                "Puvodni Wi-Fi profil se nepodarilo obnovit.",
            ),
        }

    result = run_nmcli(
        "device",
        "connect",
        WIFI_INTERFACE,
    )

    return {
        "ok": result.returncode == 0,
        "profile_name": None,
        "error": (
            None
            if result.returncode == 0
            else _command_error(
                result,
                "Wi-Fi rozhrani se nepodarilo znovu pripojit.",
            )
        ),
    }


def _service_ssid_for_serial(
    serial_number: str,
) -> str:
    serial = str(
        serial_number or ""
    ).strip().upper()

    suffix = (
        serial.rsplit("-", 1)[-1]
        if serial
        else "SERVICE"
    )

    return f"TNG_IQ_FANDA_{suffix}"


def start_installed_boot_window(
    *,
    serial_number: str,
    window_seconds: int = SERVICE_WINDOW_SECONDS,
) -> dict[str, Any]:
    global _mode
    global _deadline
    global _previous_profile
    global _service_ssid

    previous_profile = _detect_previous_profile()
    service_ssid = _service_ssid_for_serial(
        serial_number
    )

    with _STATE_LOCK:
        _previous_profile = previous_profile
        _service_ssid = service_ssid
        _mode = InstallerMode.INSTALLED_BOOT_WINDOW
        _deadline = None
        _set_last_result(None)

    request_access_point(
        reason="installed_boot_window",
        ssid=service_ssid,
    )

    if not _wait_ap_state(True):
        try:
            release_access_point(
                reason="installed_boot_window_failed"
            )
        except Exception:
            pass

        restore = _restore_previous_profile()

        with _STATE_LOCK:
            _mode = InstallerMode.INSTALLED_RUN
            _deadline = None

        result = {
            "ok": False,
            "error": "Servisni AP se nepodarilo spustit.",
            "restore": restore,
        }
        _set_last_result(result)
        return result

    with _STATE_LOCK:
        _deadline = (
            time.monotonic()
            + max(1, int(window_seconds))
        )

    while True:
        with _STATE_LOCK:
            mode = _mode
            deadline = _deadline

        if mode == InstallerMode.WIFI_MAINTENANCE:
            return {
                "ok": True,
                "maintenance_requested": True,
                "service_ssid": service_ssid,
            }

        if (
            deadline is not None
            and time.monotonic() >= deadline
        ):
            break

        time.sleep(0.2)

    release_access_point(
        reason="installed_boot_window_expired"
    )

    ap_stopped = _wait_ap_state(False)
    restore = _restore_previous_profile()

    with _STATE_LOCK:
        _mode = InstallerMode.INSTALLED_RUN
        _deadline = None

    result = {
        "ok": bool(
            ap_stopped
            and restore.get("ok")
        ),
        "expired": True,
        "ap_stopped": ap_stopped,
        "restore": restore,
    }
    _set_last_result(result)
    return result


def begin_wifi_maintenance() -> dict[str, Any]:
    global _mode
    global _deadline

    with _STATE_LOCK:
        if _mode != InstallerMode.INSTALLED_BOOT_WINDOW:
            return {
                "ok": False,
                "error": "Servisni okno neni aktivni.",
                "mode": _mode.value,
            }

        _mode = InstallerMode.WIFI_MAINTENANCE
        _deadline = None

    _set_last_result(None)

    return {
        "ok": True,
        "mode": InstallerMode.WIFI_MAINTENANCE.value,
    }


def cancel_wifi_maintenance() -> dict[str, Any]:
    global _mode
    global _deadline

    release_access_point(
        reason="wifi_maintenance_cancelled"
    )

    ap_stopped = _wait_ap_state(False)
    restore = _restore_previous_profile()

    with _STATE_LOCK:
        _mode = InstallerMode.INSTALLED_RUN
        _deadline = None

    result = {
        "ok": bool(
            ap_stopped
            and restore.get("ok")
        ),
        "cancelled": True,
        "restore": restore,
    }
    _set_last_result(result)
    return result


def _connect_pending_profile(
    *,
    ssid: str,
    password: str,
) -> dict[str, Any]:
    run_nmcli(
        "connection",
        "delete",
        PENDING_PROFILE,
    )

    add_result = run_nmcli(
        "connection",
        "add",
        "type",
        "wifi",
        "ifname",
        WIFI_INTERFACE,
        "con-name",
        PENDING_PROFILE,
        "ssid",
        ssid,
    )

    if add_result.returncode != 0:
        raise RuntimeError(
            _command_error(
                add_result,
                "Pending Wi-Fi profil nelze vytvorit.",
            )
        )

    modify_result = run_nmcli(
        "connection",
        "modify",
        PENDING_PROFILE,
        "802-11-wireless.mode",
        "infrastructure",
        "802-11-wireless-security.key-mgmt",
        "wpa-psk",
        "802-11-wireless-security.psk",
        password,
        "ipv4.method",
        "auto",
        "ipv6.method",
        "auto",
        "connection.autoconnect",
        "no",
    )

    if modify_result.returncode != 0:
        run_nmcli(
            "connection",
            "delete",
            PENDING_PROFILE,
        )
        raise RuntimeError(
            _command_error(
                modify_result,
                "Pending Wi-Fi profil nelze nastavit.",
            )
        )

    password_file_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="iqf-wifi-maintenance-",
            suffix=".passwd",
            dir="/tmp",
            delete=False,
        ) as password_file:
            password_file.write(
                "802-11-wireless-security.psk:"
                + password
                + "\n"
            )
            password_file.flush()
            os.fsync(password_file.fileno())
            password_file_path = password_file.name

        os.chmod(password_file_path, 0o600)

        up_result = run_nmcli(
            "connection",
            "up",
            PENDING_PROFILE,
            "ifname",
            WIFI_INTERFACE,
            "passwd-file",
            password_file_path,
        )

    finally:
        if password_file_path:
            try:
                os.unlink(password_file_path)
            except FileNotFoundError:
                pass

    if up_result.returncode != 0:
        raise RuntimeError(
            _command_error(
                up_result,
                "Nova Wi-Fi se nepodarila aktivovat.",
            )
        )

    status = get_wifi_status()

    if (
        not status.get("ok")
        or not status.get("connected")
        or str(
            status.get("connection_name") or ""
        ).strip() != PENDING_PROFILE
        or not status.get("ip_address")
    ):
        raise RuntimeError(
            "Nova Wi-Fi nema platne DHCP pripojeni."
        )

    return status


def _verify_dns() -> None:
    socket.getaddrinfo(
        CLOUD_HOST,
        CLOUD_PORT,
        type=socket.SOCK_STREAM,
    )


def _verify_cloud_tls() -> None:
    context = ssl.create_default_context()

    with socket.create_connection(
        (CLOUD_HOST, CLOUD_PORT),
        timeout=5,
    ) as raw_socket:
        with context.wrap_socket(
            raw_socket,
            server_hostname=CLOUD_HOST,
        ):
            pass


def _commit_pending_profile() -> None:
    with _STATE_LOCK:
        old_profile = _previous_profile

    backup_created = False

    if old_profile == FINAL_PROFILE:
        run_nmcli(
            "connection",
            "delete",
            BACKUP_PROFILE,
        )

        backup_result = run_nmcli(
            "connection",
            "modify",
            FINAL_PROFILE,
            "connection.id",
            BACKUP_PROFILE,
        )

        if backup_result.returncode != 0:
            raise RuntimeError(
                _command_error(
                    backup_result,
                    "Puvodni Wi-Fi profil nelze zalohovat.",
                )
            )

        backup_created = True

    rename_result = run_nmcli(
        "connection",
        "modify",
        PENDING_PROFILE,
        "connection.id",
        FINAL_PROFILE,
        "connection.autoconnect",
        "yes",
    )

    if rename_result.returncode != 0:
        if backup_created:
            run_nmcli(
                "connection",
                "modify",
                BACKUP_PROFILE,
                "connection.id",
                FINAL_PROFILE,
            )

        raise RuntimeError(
            _command_error(
                rename_result,
                "Novou Wi-Fi nelze potvrdit jako hlavni profil.",
            )
        )

    if backup_created:
        run_nmcli(
            "connection",
            "delete",
            BACKUP_PROFILE,
        )

    elif (
        old_profile
        and old_profile
        not in (PENDING_PROFILE, FINAL_PROFILE)
    ):
        run_nmcli(
            "connection",
            "delete",
            old_profile,
        )


def apply_wifi_change(
    *,
    ssid: str,
    password: str,
) -> dict[str, Any]:
    global _mode
    global _deadline
    global _previous_profile

    normalized_ssid = str(ssid or "").strip()
    normalized_password = str(password or "")

    if not normalized_ssid:
        return {
            "ok": False,
            "error": "SSID nesmi byt prazdne.",
        }

    if len(normalized_password) < 8:
        return {
            "ok": False,
            "error": "Heslo Wi-Fi musi mit alespon 8 znaku.",
        }

    with _OPERATION_LOCK:
        with _STATE_LOCK:
            if _mode != InstallerMode.WIFI_MAINTENANCE:
                result = {
                    "ok": False,
                    "error": "Rezim zmeny Wi-Fi neni aktivni.",
                    "mode": _mode.value,
                }
                _set_last_result(result)
                return result

        release_access_point(
            reason="wifi_maintenance_test_new_wifi"
        )

        if not _wait_ap_state(False):
            result = {
                "ok": False,
                "error": "Servisni AP se pred testem nove Wi-Fi nevypnul.",
            }
            _set_last_result(result)
            return result

        try:
            status = _connect_pending_profile(
                ssid=normalized_ssid,
                password=normalized_password,
            )

            _verify_dns()

            internet = check_internet_access()

            if not internet.get("ok"):
                raise RuntimeError(
                    str(
                        internet.get("error")
                        or "Internet neni dostupny."
                    )
                )

            _verify_cloud_tls()
            _commit_pending_profile()

            with _STATE_LOCK:
                _previous_profile = FINAL_PROFILE
                _mode = InstallerMode.INSTALLED_RUN
                _deadline = None

            result = {
                "ok": True,
                "changed": True,
                "ssid": normalized_ssid,
                "profile_name": FINAL_PROFILE,
                "ip_address": status.get("ip_address"),
                "dns": True,
                "internet": True,
                "cloud": True,
            }
            _set_last_result(result)
            return result

        except Exception as exc:
            run_nmcli(
                "connection",
                "delete",
                PENDING_PROFILE,
            )

            restore = _restore_previous_profile()

            with _STATE_LOCK:
                service_ssid = _service_ssid
                _mode = InstallerMode.WIFI_MAINTENANCE
                _deadline = None

            try:
                request_access_point(
                    reason="wifi_maintenance_failed",
                    ssid=service_ssid,
                )
                ap_restored = _wait_ap_state(True)

            except Exception:
                ap_restored = False

            result = {
                "ok": False,
                "changed": False,
                "error": str(exc),
                "rollback": restore,
                "ap_restored": ap_restored,
            }
            _set_last_result(result)
            return result
