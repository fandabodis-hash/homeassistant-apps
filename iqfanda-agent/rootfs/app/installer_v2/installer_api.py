"""Lokalni HTTP API TNG IQ FANDA Installer V2."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from http import HTTPStatus
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
from typing import Any
from urllib.parse import urlsplit

from host.access_point_manager import (
    access_point_manager,
)
from installer.access_point_service import (
    release_access_point,
    request_access_point,
)
from installer.network_manager import (
    connect_wifi,
    get_network_info,
    scan_wifi_networks,
)
from installer_v2.ap_workflow import (
    TypPripojeni,
    konfigurace_ap_pro_rezim,
    pole_prvni_instalace,
    validuj_prvni_instalaci,
)
from installer_v2.cloud_registration import (
    cloud_identity_exists,
    ensure_cloud_registration,
)
from installer_v2.factory_provisioning import (
    provision_factory_v2_from_portal,
)
from installer_v2.models import InstallerMode


VYCHOZI_HOST = "0.0.0.0"
VYCHOZI_PORT = 8099

NETWORK_RESULT_PATH = Path(
    "/tmp/installer_v2_network_result.json"
)

NETWORK_CHANGE_LOCK = threading.Lock()

MANUFACTURING_IDENTITY_PATH = Path(
    "/data/device_identity.json"
)


def _existing_manufacturing_serial() -> str:
    if not MANUFACTURING_IDENTITY_PATH.is_file():
        return ""

    payload = json.loads(
        MANUFACTURING_IDENTITY_PATH.read_text(
            encoding="utf-8-sig"
        )
    )

    serial_number = str(
        payload.get("serial_number")
        or ""
    ).strip().upper()

    if not serial_number:
        raise RuntimeError(
            "Existujici vyrobni identita "
            "nema seriove cislo."
        )

    return serial_number


def _ensure_factory_provisioned(
    *,
    admin_email: str,
    admin_password: str,
) -> dict[str, Any]:
    """
    Vytvori Factory V2 identitu pouze u noveho kusu.

    Pokud device_identity.json uz existuje,
    nove seriove cislo se nikdy neprideli.
    """

    existing_serial = (
        _existing_manufacturing_serial()
    )

    if existing_serial:
        return {
            "ok": True,
            "factory_called": False,
            "cloud_claim_created": False,
            "device_identity_created": False,
            "serial_number":
                existing_serial,
        }

    result = dict(
        provision_factory_v2_from_portal(
            admin_email=admin_email,
            admin_password=admin_password,
        )
    )

    result["factory_called"] = True

    return result


def service_access_point_ssid() -> str:
    """
    Vrati servisni SSID existujiciho IQ FANDA.

    Priklad:
    F800711-TNG-00010
    -> TNG_IQ_FANDA_00010
    """

    try:
        payload = json.loads(
            MANUFACTURING_IDENTITY_PATH.read_text(
                encoding="utf-8-sig"
            )
        )

        serial_number = str(
            payload.get("serial_number")
            or ""
        ).strip().upper()

        suffix = (
            serial_number.rsplit(
                "-",
                1,
            )[-1]
            if serial_number
            else ""
        )

        if (
            len(suffix) == 5
            and suffix.isdigit()
        ):
            return (
                "TNG_IQ_FANDA_"
                + suffix
            )

    except Exception:
        pass

    return "TNG_IQ_FANDA_SERVICE"


def _write_network_result(
    payload: dict[str, object],
) -> None:
    try:
        NETWORK_RESULT_PATH.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _wait_ap_stopped(
    timeout_seconds: float = 10.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            if not access_point_manager.is_access_point_active():
                return True
        except Exception:
            pass

        time.sleep(0.2)

    return False


def _wifi_switch_worker(
    ssid: str,
    password: str,
    customer_name: str,
    email: str,
    factory_admin_email: str,
    factory_admin_password: str,
) -> None:
    with NETWORK_CHANGE_LOCK:
        time.sleep(1.5)

        result = {
            "ok": False,
            "connection_type": "wifi",
            "ssid": ssid,
            "cloud_called": False,
            "serial_allocated": False,
        }

        try:
            release_access_point(
                reason="installer_v2_first_install_wifi"
            )

            if not _wait_ap_stopped():
                raise RuntimeError(
                    "Instalacni AP se nepodarilo vypnout."
                )

            connection = connect_wifi(
                ssid=ssid,
                password=password,
                interface="wlan0",
            )

            if not connection.get("ok"):
                raise RuntimeError(
                    str(
                        connection.get("error")
                        or "Pripojeni k Wi-Fi selhalo."
                    )
                )

            status = connection.get("status") or {}

            factory_result = (
                _ensure_factory_provisioned(
                    admin_email=(
                        factory_admin_email
                    ),
                    admin_password=(
                        factory_admin_password
                    ),
                )
            )

            factory_admin_password = ""

            registration = ensure_cloud_registration(
                customer_name=customer_name,
                email=email,
            )

            result.update({
                "ok": True,
                "interface": "wlan0",
                "ip_address": status.get("ip_address"),
                "ap_active": False,
                "cloud_called": registration.get(
                    "cloud_called"
                ),
                "cloud_registration_created":
                    registration.get(
                        "registration_created"
                    ),
                "cloud_identity_created":
                    registration.get(
                        "identity_created"
                    ),
                "serial_number":
                    registration.get(
                        "serial_number"
                    ),
                "device_uuid":
                    registration.get(
                        "device_uuid"
                    ),
                "factory_called":
                    factory_result.get(
                        "factory_called"
                    ),
                "factory_claim_created":
                    factory_result.get(
                        "cloud_claim_created"
                    ),
                "factory_identity_created":
                    factory_result.get(
                        "device_identity_created"
                    ),
                "serial_allocated":
                    bool(
                        factory_result.get(
                            "cloud_claim_created"
                        )
                        or
                        factory_result.get(
                            "device_identity_created"
                        )
                    ),
            })

            _write_network_result(result)

            print(
                "Installer V2 Wi-Fi switch OK:",
                ssid,
                result.get("ip_address"),
                flush=True,
            )

        except Exception as exc:
            result["error"] = str(exc)

            try:
                request_access_point(
                    reason="installer_v2_wifi_failed",
                    ssid=(
                        service_access_point_ssid()
                    ),
                )

                result["ap_restore_requested"] = True

            except Exception as restore_exc:
                result["ap_restore_error"] = str(
                    restore_exc
                )

            _write_network_result(result)

            print(
                "Installer V2 Wi-Fi switch FAILED:",
                exc,
                flush=True,
            )


def _wifi_maintenance_worker(
    ssid: str,
    password: str,
) -> None:
    """
    Zmeni pouze Wi-Fi existujiciho zarizeni.

    Nikdy nemeni:
    - device_identity.json,
    - provisioning_id,
    - serial_number,
    - device.json,
    - device_uuid,
    - device_token.
    """

    with NETWORK_CHANGE_LOCK:
        time.sleep(1.5)

        result = {
            "ok": False,
            "action": "wifi_maintenance",
            "ssid": ssid,
            "serial_allocated": False,
            "identity_changed": False,
        }

        try:
            release_access_point(
                reason=(
                    "installer_v2_"
                    "wifi_maintenance"
                )
            )

            if not _wait_ap_stopped():
                raise RuntimeError(
                    "Servisni AP se nepodarilo vypnout."
                )

            connection = connect_wifi(
                ssid=ssid,
                password=password,
                interface="wlan0",
            )

            if not connection.get("ok"):
                raise RuntimeError(
                    str(
                        connection.get("error")
                        or
                        "Pripojeni k nove Wi-Fi selhalo."
                    )
                )

            status = (
                connection.get("status")
                or {}
            )

            result.update({
                "ok": True,
                "interface": "wlan0",
                "ip_address":
                    status.get(
                        "ip_address"
                    ),
                "ap_active": False,
            })

            _write_network_result(
                result
            )

            print(
                "Installer V2 Wi-Fi maintenance OK:",
                ssid,
                result.get("ip_address"),
                flush=True,
            )

        except Exception as exc:
            result["error"] = str(exc)

            try:
                request_access_point(
                    reason=(
                        "installer_v2_"
                        "wifi_maintenance_failed"
                    ),
                    ssid=(
                        service_access_point_ssid()
                    ),
                )

                result[
                    "ap_restore_requested"
                ] = True

            except Exception as restore_exc:
                result[
                    "ap_restore_error"
                ] = str(
                    restore_exc
                )

            _write_network_result(
                result
            )

            print(
                "Installer V2 Wi-Fi maintenance FAILED:",
                exc,
                flush=True,
            )


SERVICE_HTML = """<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>
<title>TNG IQ FANDA - Změna Wi-Fi</title>

<style>
* { box-sizing: border-box; }

body {
    margin: 0;
    min-height: 100vh;
    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
    background:
        radial-gradient(
            circle at 50% -20%,
            #15365e 0,
            #071424 40%,
            #03080f 100%
        );
    color: #eef6ff;
}

.page {
    width: min(
        680px,
        calc(100% - 28px)
    );
    margin: 0 auto;
    padding: 30px 0 55px;
}

.logo {
    text-align: center;
    font-size: 29px;
    font-weight: 800;
    margin-bottom: 24px;
}

.logo span {
    color: #58b8ff;
}

.card {
    padding: 25px;
    border-radius: 22px;
    border:
        1px solid
        rgba(115,174,224,.22);
    background:
        rgba(8,20,34,.97);
}

h1 {
    margin-top: 0;
}

p {
    color: #94a9be;
    line-height: 1.5;
}

label {
    display: block;
    margin: 18px 0 7px;
    font-size: 13px;
}

select,
input,
button {
    width: 100%;
    height: 49px;
    border-radius: 12px;
    font: inherit;
}

select,
input {
    padding: 0 14px;
    border: 1px solid #29445e;
    background: #081828;
    color: #eef7ff;
}

button {
    border: 0;
    margin-top: 14px;
    cursor: pointer;
    font-weight: 750;
}

.secondary {
    background: #15334c;
    color: #d9efff;
}

.primary {
    margin-top: 26px;
    background:
        linear-gradient(
            135deg,
            #2898e8,
            #54c0ff
        );
    color: #03111d;
}

.message {
    margin-top: 18px;
    font-size: 13px;
}
</style>
</head>

<body>

<div class="page">

<div class="logo">
    TNG IQ <span>FANDA</span>
</div>

<div class="card">

<h1>Změna Wi-Fi</h1>

<p>
    Vyberte Wi-Fi síť v nové lokalitě.
    Sériové číslo a registrace zařízení
    zůstanou beze změny.
</p>

<label for="ssid">
    Nová Wi-Fi síť
</label>

<select id="ssid">
<option value="">
    Vyhledejte Wi-Fi sítě
</option>
</select>

<button
    id="scan"
    class="secondary"
    type="button"
>
    Vyhledat sítě
</button>

<label for="password">
    Heslo nové Wi-Fi
</label>

<input
    id="password"
    type="password"
    autocomplete="new-password"
>

<button
    id="save"
    class="primary"
    type="button"
>
    Připojit k nové Wi-Fi
</button>

<div
    id="message"
    class="message"
></div>

</div>
</div>

<script>
const ssid =
    document.getElementById("ssid");

const password =
    document.getElementById("password");

const scan =
    document.getElementById("scan");

const save =
    document.getElementById("save");

const message =
    document.getElementById("message");

scan.addEventListener(
    "click",
    async () => {
        scan.disabled = true;

        try {
            const response =
                await fetch(
                    "/api/wifi/scan",
                    { method: "POST" }
                );

            const data =
                await response.json();

            if (
                !response.ok ||
                !data.ok
            ) {
                throw new Error(
                    data.error ||
                    "Vyhledání Wi-Fi selhalo."
                );
            }

            ssid.innerHTML =
                '<option value="">' +
                'Vyberte Wi-Fi síť' +
                '</option>';

            (
                Array.isArray(data.networks)
                    ? data.networks
                    : []
            ).forEach(
                network => {
                    if (
                        !network ||
                        !network.ssid
                    ) {
                        return;
                    }

                    const option =
                        document.createElement(
                            "option"
                        );

                    option.value =
                        network.ssid;

                    option.textContent =
                        network.ssid;

                    ssid.appendChild(
                        option
                    );
                }
            );

            message.textContent =
                "Vyberte novou Wi-Fi.";
        }
        catch (error) {
            message.textContent =
                error.message;
        }
        finally {
            scan.disabled = false;
        }
    }
);

save.addEventListener(
    "click",
    async () => {
        const payload = {
            ssid:
                ssid.value,
            wifi_password:
                password.value
        };

        try {
            save.disabled = true;

            const response =
                await fetch(
                    "/api/wifi/change",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type":
                                "application/json"
                        },
                        body:
                            JSON.stringify(
                                payload
                            )
                    }
                );

            const data =
                await response.json();

            if (
                !response.ok ||
                !data.ok
            ) {
                throw new Error(
                    data.error ||
                    "Změnu Wi-Fi nelze spustit."
                );
            }

            message.textContent =
                "Připojuji TNG IQ FANDA " +
                "k nové Wi-Fi. " +
                "Servisní síť se nyní odpojí.";
        }
        catch (error) {
            save.disabled = false;

            message.textContent =
                error.message;
        }
    }
);
</script>

</body>
</html>
"""


HTML = """<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>
<title>TNG IQ FANDA production installation V1.0</title>

<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    min-height: 100vh;
    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
    background:
        radial-gradient(
            circle at 50% -20%,
            #15365e 0,
            #071424 40%,
            #03080f 100%
        );
    color: #eef6ff;
}

.page {
    width: min(
        680px,
        calc(100% - 28px)
    );
    margin: 0 auto;
    padding: 30px 0 55px;
}

.brand {
    text-align: center;
    margin-bottom: 25px;
}

.logo {
    font-size: 29px;
    font-weight: 800;
    letter-spacing: .07em;
}

.logo span {
    color: #58b8ff;
}

.subtitle {
    margin-top: 6px;
    color: #91a8bd;
    font-size: 14px;
}

.card {
    padding: 25px;
    border-radius: 22px;
    border:
        1px solid
        rgba(115,174,224,.22);
    background:
        linear-gradient(
            180deg,
            rgba(20,38,59,.96),
            rgba(8,20,34,.97)
        );
    box-shadow:
        0 24px 70px
        rgba(0,0,0,.42);
}

.state {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-bottom: 22px;
    padding: 11px 13px;
    border-radius: 12px;
    background:
        rgba(59,157,255,.08);
    border:
        1px solid
        rgba(76,167,255,.18);
    color: #b8dcff;
    font-size: 13px;
}

.dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: #4ecb82;
    box-shadow:
        0 0 14px #4ecb82;
}

h1 {
    margin: 0 0 8px;
    font-size: 24px;
}

.description {
    margin-bottom: 24px;
    color: #94a9be;
    line-height: 1.5;
}

label {
    display: block;
    margin: 18px 0 7px;
    color: #c9d9e8;
    font-size: 13px;
    font-weight: 650;
}

input,
select,
button {
    font: inherit;
}

input,
select {
    width: 100%;
    height: 49px;
    padding: 0 14px;
    border-radius: 12px;
    border: 1px solid #29445e;
    background: #081828;
    color: #eef7ff;
    outline: none;
}

.mode-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
}

.mode {
    position: relative;
}

.mode input {
    position: absolute;
    opacity: 0;
}

.mode-label {
    display: block;
    cursor: pointer;
    text-align: center;
    padding: 16px 8px;
    border-radius: 13px;
    border: 1px solid #29445e;
    background: #081828;
}

.mode input:checked + .mode-label {
    border-color: #53b7ff;
    background:
        rgba(54,154,225,.15);
}

.mode-title {
    font-weight: 750;
}

.mode-desc {
    margin-top: 4px;
    color: #8098ae;
    font-size: 12px;
}

#wifiSection {
    display: none;
}

.scan-row {
    display: grid;
    grid-template-columns:
        1fr auto;
    gap: 9px;
}

button {
    border: 0;
    border-radius: 12px;
    cursor: pointer;
    font-weight: 750;
}

.scan {
    padding: 0 17px;
    background: #15334c;
    color: #d9efff;
}

.primary {
    width: 100%;
    height: 52px;
    margin-top: 27px;
    background:
        linear-gradient(
            135deg,
            #2898e8,
            #54c0ff
        );
    color: #03111d;
}

.message {
    display: none;
    margin-top: 18px;
    padding: 14px;
    border-radius: 12px;
    font-size: 13px;
}

.message.ok {
    display: block;
    color: #a9ebc5;
    background:
        rgba(58,184,112,.10);
    border:
        1px solid
        rgba(58,184,112,.28);
}

.message.error {
    display: block;
    color: #ffc3c3;
    background:
        rgba(255,91,91,.09);
    border:
        1px solid
        rgba(255,91,91,.25);
}

.footer {
    margin-top: 18px;
    text-align: center;
    color: #587087;
    font-size: 11px;
}

.production-hidden,
label[for="customerName"],
#customerName,
label[for="email"],
#email,
.mode-grid,
#wifiSection {
    display: none !important;
}

.production-serial {
    letter-spacing: .06em;
    font-weight: 750;
    color: #9bd6ff;
}

.production-note {
    margin-top: 18px;
    padding: 15px;
    border-radius: 13px;
    border: 1px solid rgba(83,183,255,.20);
    background: rgba(46,145,215,.08);
    color: #a9bfd3;
    font-size: 13px;
    line-height: 1.55;
}

.production-flow {
    display: grid;
    gap: 9px;
    margin-top: 20px;
}

.production-step {
    padding: 12px 14px;
    border-radius: 12px;
    background: rgba(255,255,255,.025);
    border: 1px solid rgba(126,167,202,.12);
    color: #91a8bd;
    font-size: 13px;
    line-height: 1.45;
}

.production-step strong {
    display: block;
    margin-bottom: 3px;
    color: #d9edff;
}

.primary:disabled {
    cursor: not-allowed;
    opacity: .55;
}

@media (max-width: 520px) {
    .mode-grid {
        grid-template-columns: 1fr;
    }
}
</style>
</head>

<body>

<div class="page">

<div class="brand">
    <div class="logo">
        TNG IQ <span>FANDA</span>
    </div>

    <div class="subtitle">
        production installation V1.0
    </div>
</div>

<div class="card">

<div class="state">
    <span class="dot"></span>
    Výrobní režim · Production V1.0
</div>

<h1>Výrobní registrace TNG IQ FANDA</h1>

<div class="description">
    Nový TNG IQ FANDA je při výrobě
    připojen k internetu přes Ethernet.
    Výrobce se autorizuje administrátorským
    účtem a zařízení následně získá své
    trvalé výrobní sériové číslo.
</div>

<label for="factoryAdminEmail">
    E-mail administrátora TNG IQ FANDA
</label>

<input
    id="factoryAdminEmail"
    type="email"
    autocomplete="username"
    placeholder="admin@tngiqfanda.cz"
>

<label for="factoryAdminPassword">
    Heslo administrátora
</label>

<input
    id="factoryAdminPassword"
    type="password"
    autocomplete="current-password"
>

<div class="description">
    Přihlášení slouží pouze jako autorizace
    výrobce. Administrátorské heslo se do
    zařízení nebude trvale ukládat.
</div>

<label for="productionSerial">
    Sériové číslo TNG IQ FANDA
</label>

<input
    id="productionSerial"
    class="production-serial"
    type="text"
    value="Automaticky přidělí cloud"
    readonly
>

<div class="production-note">
    <strong>Výrobní stanoviště</strong><br>
    Připojení: Ethernet<br>
    IP adresa je při výrobě dostupná
    na monitoru připojeném přes HDMI.<br><br>

    Po přidělení sériového čísla výrobce
    vytiskne SN samolepku a nalepí ji
    na bok zařízení.
</div>

<div class="production-flow">

    <div class="production-step">
        <strong>1 · Výroba</strong>
        Autorizace výrobce → přidělení SN →
        cloud → READY_FOR_INSTALL.
    </div>

    <div class="production-step">
        <strong>2 · Instalace u zákazníka</strong>
        Po zapnutí se podle potřeby aktivuje AP.
        Instalatér nastaví Wi-Fi nebo použije
        Ethernet. Fanda se objeví v admin portálu
        online pod svým výrobním SN.
    </div>

    <div class="production-step">
        <strong>3 · Technologická instalace</strong>
        Instalatér v cloudu připojí střídač,
        komunikační rozhraní, Zigbee zařízení,
        čidla, zásuvky a ostatní moduly.
    </div>

    <div class="production-step">
        <strong>4 · Předání zákazníkovi</strong>
        Po dokončení instalace je Fanda předána
        zákazníkovi. Zákazník aktivuje připravený
        účet svým e-mailem a nastaví vlastní heslo.
    </div>

</div>

<label for="customerName">
    Jméno zákazníka
</label>

<input
    id="customerName"
    autocomplete="name"
    placeholder="Jan Novák"
>

<label for="email">
    E-mail zákazníka
</label>

<input
    id="email"
    type="email"
    autocomplete="email"
    placeholder="jan@novak.cz"
>

<label class="production-hidden">
    Způsob připojení
</label>

<div class="mode-grid">

<label class="mode">
<input
    type="radio"
    name="connection"
    value="ethernet"
    checked
>
<span class="mode-label">
    <div class="mode-title">
        Ethernet
    </div>
    <div class="mode-desc">
        Síťový kabel
    </div>
</span>
</label>

<label class="mode">
<input
    type="radio"
    name="connection"
    value="wifi"
>
<span class="mode-label">
    <div class="mode-title">
        Wi-Fi
    </div>
    <div class="mode-desc">
        Bezdrátová síť
    </div>
</span>
</label>

</div>

<div id="wifiSection">

<label for="ssid">
    Wi-Fi síť
</label>

<div class="scan-row">

<select id="ssid">
<option value="">
    Vyhledejte Wi-Fi sítě
</option>
</select>

<button
    id="scanButton"
    class="scan"
    type="button"
>
    Vyhledat
</button>

</div>

<label for="wifiPassword">
    Heslo Wi-Fi
</label>

<input
    id="wifiPassword"
    type="password"
    autocomplete="new-password"
>

</div>

<button
    id="continueButton"
    class="primary"
    type="button"
    disabled
    aria-disabled="true"
>
    Odeslat na cloud · funkce bude aktivována po výrobním testu
</button>

<div
    id="message"
    class="message"
></div>

</div>

<div class="footer">
    TNG IQ FANDA · production installation V1.0 · UI preview
</div>

</div>

<script>
const wifiSection =
    document.getElementById(
        "wifiSection"
    );

const ssidSelect =
    document.getElementById(
        "ssid"
    );

const scanButton =
    document.getElementById(
        "scanButton"
    );

const continueButton =
    document.getElementById(
        "continueButton"
    );

const message =
    document.getElementById(
        "message"
    );

function connectionType() {
    return document.querySelector(
        'input[name="connection"]:checked'
    ).value;
}

function updateMode() {
    wifiSection.style.display =
        connectionType() === "wifi"
            ? "block"
            : "none";

    message.className =
        "message";

    message.textContent =
        "";
}

document.querySelectorAll(
    'input[name="connection"]'
).forEach(
    element =>
        element.addEventListener(
            "change",
            updateMode
        )
);

scanButton.addEventListener(
    "click",
    async () => {
        scanButton.disabled = true;
        scanButton.textContent = "...";

        try {
            const response =
                await fetch(
                    "/api/wifi/scan",
                    {
                        method: "POST"
                    }
                );

            const data =
                await response.json();

            if (
                !response.ok ||
                !data.ok
            ) {
                throw new Error(
                    data.error ||
                    "Wi-Fi scan selhal."
                );
            }

            ssidSelect.innerHTML =
                '<option value="">' +
                'Vyberte Wi-Fi síť' +
                '</option>';

            const networks =
                Array.isArray(
                    data.networks
                )
                    ? data.networks
                    : [];

            networks.forEach(
                network => {
                    if (
                        !network ||
                        !network.ssid
                    ) {
                        return;
                    }

                    const option =
                        document.createElement(
                            "option"
                        );

                    option.value =
                        network.ssid;

                    option.textContent =
                        network.ssid +
                        (
                            network.signal
                                ? " (" +
                                  network.signal +
                                  "%)"
                                : ""
                        );

                    ssidSelect.appendChild(
                        option
                    );
                }
            );

            message.className =
                "message ok";

            message.textContent =
                "Nalezeno sítí: " +
                networks.length;
        }
        catch (error) {
            message.className =
                "message error";

            message.textContent =
                error.message;
        }
        finally {
            scanButton.disabled = false;
            scanButton.textContent =
                "Vyhledat";
        }
    }
);

continueButton.addEventListener(
    "click",
    async () => {
        const payload = {
            factory_admin_email:
                document
                    .getElementById(
                        "factoryAdminEmail"
                    )
                    .value
                    .trim(),

            factory_admin_password:
                document
                    .getElementById(
                        "factoryAdminPassword"
                    )
                    .value,

            customer_name:
                document
                    .getElementById(
                        "customerName"
                    )
                    .value
                    .trim(),

            email:
                document
                    .getElementById(
                        "email"
                    )
                    .value
                    .trim(),

            connection_type:
                connectionType(),

            ssid:
                ssidSelect.value,

            wifi_password:
                document
                    .getElementById(
                        "wifiPassword"
                    )
                    .value
        };

        try {
            const response =
                await fetch(
                    "/api/installer/apply",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type":
                                "application/json"
                        },
                        body:
                            JSON.stringify(
                                payload
                            )
                    }
                );

            const data =
                await response.json();

            if (
                !response.ok ||
                !data.ok
            ) {
                throw new Error(
                    data.error ||
                    "Kontrola údajů selhala."
                );
            }

            message.className =
                "message ok";

            if (
                data.action
                === "switching_to_wifi"
            ) {
                continueButton.disabled = true;

                message.textContent =
                    "Připojuji TNG IQ FANDA k Wi-Fi " +
                    data.ssid +
                    ". Instalační síť se nyní odpojí.";
            }
            else {
                message.textContent =
                    "Ethernet je připojen. " +
                    "TNG IQ FANDA je připraven.";
            }
        }
        catch (error) {
            message.className =
                "message error";

            message.textContent =
                error.message;
        }
    }
);

updateMode();
</script>

</body>
</html>
"""


class InstallerV2Handler(
    BaseHTTPRequestHandler
):
    """HTTP obsluha Installeru V2."""

    server_version = (
        "TNG-IQ-FANDA-Installer-V2/0.1"
    )

    def _path(self) -> str:
        return urlsplit(
            self.path
        ).path

    def _json(
        self,
        status: HTTPStatus | int,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode(
            "utf-8"
        )

        self.send_response(
            int(status)
        )

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        self.end_headers()

        self.wfile.write(
            body
        )

    def _html(
        self,
        html: str,
    ) -> None:
        body = html.encode(
            "utf-8"
        )

        self.send_response(
            HTTPStatus.OK
        )

        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        self.end_headers()

        self.wfile.write(
            body
        )

    def _read_json(
        self,
    ) -> dict[str, Any]:
        length = int(
            self.headers.get(
                "Content-Length",
                "0",
            )
        )

        if length <= 0:
            return {}

        raw = self.rfile.read(
            length
        )

        payload = json.loads(
            raw.decode(
                "utf-8"
            )
        )

        if not isinstance(
            payload,
            dict,
        ):
            raise ValueError(
                "JSON musi byt objekt."
            )

        return payload

    def do_GET(
        self,
    ) -> None:
        path = self._path()

        if path in (
            "",
            "/",
        ):
            self._html(
                (
                    SERVICE_HTML
                    if cloud_identity_exists()
                    else HTML
                )
            )
            return

        if path == "/health":
            installed = cloud_identity_exists()

            mode = (
                InstallerMode.INSTALLED_RUN
                if installed
                else InstallerMode.FIRST_INSTALL
            )

            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service":
                        "installer-v2",
                    "mode":
                        mode.value,
                    "installed":
                        installed,
                    "writes_enabled":
                        True,
                    "cloud_enabled":
                        True,
                    "serial_allocation_enabled":
                        False,
                },
            )
            return

        if (
            path
            == "/api/installer/status"
        ):
            installed = cloud_identity_exists()

            mode = (
                InstallerMode.INSTALLED_RUN
                if installed
                else InstallerMode.FIRST_INSTALL
            )

            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "installer":
                        konfigurace_ap_pro_rezim(
                            mode
                        ),
                },
            )
            return

        self._json(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "error":
                    "Endpoint nebyl nalezen.",
            },
        )

    def do_POST(
        self,
    ) -> None:
        path = self._path()

        if path == "/api/wifi/scan":
            result = (
                scan_wifi_networks()
            )

            self._json(
                (
                    HTTPStatus.OK
                    if result.get("ok")
                    else
                    HTTPStatus
                    .SERVICE_UNAVAILABLE
                ),
                result,
            )
            return

        if path == "/api/wifi/change":
            if not cloud_identity_exists():
                self._json(
                    HTTPStatus.CONFLICT,
                    {
                        "ok": False,
                        "error":
                            "device_not_installed",
                    },
                )
                return

            try:
                if not (
                    access_point_manager
                    .is_access_point_active()
                ):
                    self._json(
                        HTTPStatus.CONFLICT,
                        {
                            "ok": False,
                            "error":
                                "service_ap_not_active",
                        },
                    )
                    return

            except Exception as exc:
                self._json(
                    HTTPStatus
                    .SERVICE_UNAVAILABLE,
                    {
                        "ok": False,
                        "error": str(exc),
                    },
                )
                return

            try:
                payload = self._read_json()

            except Exception:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error":
                            "Neplatna data formulare.",
                    },
                )
                return

            ssid = str(
                payload.get("ssid")
                or ""
            ).strip()

            password = str(
                payload.get(
                    "wifi_password"
                )
                or ""
            )

            if not ssid:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error":
                            "Vyberte Wi-Fi sit.",
                    },
                )
                return

            if len(password) < 8:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error":
                            "Heslo Wi-Fi je prilis kratke.",
                    },
                )
                return

            self._json(
                HTTPStatus.ACCEPTED,
                {
                    "ok": True,
                    "action":
                        "wifi_maintenance",
                    "ssid": ssid,
                    "serial_allocated":
                        False,
                    "identity_changed":
                        False,
                },
            )

            threading.Thread(
                target=(
                    _wifi_maintenance_worker
                ),
                args=(
                    ssid,
                    password,
                ),
                name=(
                    "installer-v2-"
                    "wifi-maintenance"
                ),
                daemon=True,
            ).start()

            return

        if (
            path
            == "/api/installer/apply"
        ):
            try:
                payload = self._read_json()

            except (
                ValueError,
                UnicodeError,
                json.JSONDecodeError,
            ):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": "Neplatná data formuláře.",
                    },
                )
                return

            if cloud_identity_exists():
                self._json(
                    HTTPStatus.CONFLICT,
                    {
                        "ok": False,
                        "error":
                            "device_already_installed",
                    },
                )
                return

            factory_admin_email = str(
                payload.get(
                    "factory_admin_email"
                )
                or ""
            ).strip().lower()

            factory_admin_password = str(
                payload.get(
                    "factory_admin_password"
                )
                or ""
            )

            validation_payload = dict(
                payload
            )

            validation_payload.pop(
                "factory_admin_email",
                None,
            )

            validation_payload.pop(
                "factory_admin_password",
                None,
            )

            validation = validuj_prvni_instalaci(
                validation_payload
            )

            if not validation.get("ok"):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    validation,
                )
                return

            if (
                not MANUFACTURING_IDENTITY_PATH.exists()
                and (
                    not factory_admin_email
                    or "@"
                    not in factory_admin_email
                    or not factory_admin_password
                )
            ):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error":
                            "Pro nov? za??zen? zadejte "
                            "administr?torsk? e-mail "
                            "a heslo.",
                    },
                )
                return

            connection_type = str(
                validation.get(
                    "connection_type"
                )
                or ""
            )

            if (
                connection_type
                == TypPripojeni.ETHERNET.value
            ):
                network = get_network_info()

                wired = [
                    item
                    for item
                    in network.get(
                        "interfaces",
                        [],
                    )
                    if (
                        isinstance(item, dict)
                        and item.get("type") == "ethernet"
                        and item.get("connected")
                    )
                ]

                if not wired:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {
                            "ok": False,
                            "error":
                                "Ethernet není připojen.",
                        },
                    )
                    return

                try:
                    factory_result = (
                        _ensure_factory_provisioned(
                            admin_email=(
                                factory_admin_email
                            ),
                            admin_password=(
                                factory_admin_password
                            ),
                        )
                    )

                    factory_admin_password = ""

                    registration = ensure_cloud_registration(
                        customer_name=validation[
                            "customer_name"
                        ],
                        email=validation[
                            "email"
                        ],
                    )

                    release_access_point(
                        reason=(
                            "installer_v2_"
                            "first_install_completed"
                        )
                    )

                except Exception as exc:
                    result = {
                        "ok": False,
                        "action":
                            "cloud_registration_failed",
                        "connection_type":
                            "ethernet",
                        "interface":
                            wired[0].get("name"),
                        "ip_address":
                            wired[0].get(
                                "ip_address"
                            ),
                        "cloud_called": True,
                        "serial_allocated": False,
                        "error": str(exc),
                    }

                    _write_network_result(result)

                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        result,
                    )
                    return

                result = {
                    "ok": True,
                    "action":
                        "installation_completed",
                    "connection_type":
                        "ethernet",
                    "interface":
                        wired[0].get("name"),
                    "ip_address":
                        wired[0].get(
                            "ip_address"
                        ),
                    "cloud_called":
                        registration.get(
                            "cloud_called"
                        ),
                    "cloud_registration_created":
                        registration.get(
                            "registration_created"
                        ),
                    "cloud_identity_created":
                        registration.get(
                            "identity_created"
                        ),
                    "serial_number":
                        registration.get(
                            "serial_number"
                        ),
                    "device_uuid":
                        registration.get(
                            "device_uuid"
                        ),
                    "factory_called":
                        factory_result.get(
                            "factory_called"
                        ),
                    "factory_claim_created":
                        factory_result.get(
                            "cloud_claim_created"
                        ),
                    "factory_identity_created":
                        factory_result.get(
                            "device_identity_created"
                        ),
                    "serial_allocated":
                        bool(
                            factory_result.get(
                                "cloud_claim_created"
                            )
                            or
                            factory_result.get(
                                "device_identity_created"
                            )
                        ),
                }

                _write_network_result(result)

                self._json(
                    HTTPStatus.OK,
                    result,
                )
                return

            ssid = str(
                payload.get("ssid")
                or ""
            ).strip()

            password = str(
                payload.get("wifi_password")
                or ""
            )

            self._json(
                HTTPStatus.ACCEPTED,
                {
                    "ok": True,
                    "action": "switching_to_wifi",
                    "connection_type": "wifi",
                    "ssid": ssid,
                    "cloud_called": False,
                    "serial_allocated": False,
                },
            )

            threading.Thread(
                target=_wifi_switch_worker,
                args=(
                    ssid,
                    password,
                    validation[
                        "customer_name"
                    ],
                    validation[
                        "email"
                    ],
                    factory_admin_email,
                    factory_admin_password,
                ),
                name="installer-v2-wifi-switch",
                daemon=True,
            ).start()

            return

        if (
            path
            == "/api/installer/validate"
        ):
            try:
                payload = (
                    self._read_json()
                )

            except (
                ValueError,
                UnicodeError,
                json.JSONDecodeError,
            ):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error":
                            "Neplatná data formuláře.",
                    },
                )
                return

            result = (
                validuj_prvni_instalaci(
                    payload
                )
            )

            self._json(
                (
                    HTTPStatus.OK
                    if result.get("ok")
                    else
                    HTTPStatus.BAD_REQUEST
                ),
                result,
            )
            return

        self._json(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "error":
                    "Endpoint nebyl nalezen.",
            },
        )

    def log_message(
        self,
        format: str,
        *args: Any,
    ) -> None:
        return


def spustit_api(
    host: str = VYCHOZI_HOST,
    port: int = VYCHOZI_PORT,
) -> None:
    """Spusti HTTP server Installer V2."""

    server = ThreadingHTTPServer(
        (
            host,
            port,
        ),
        InstallerV2Handler,
    )

    print(
        (
            "Installer V2 API posloucha "
            f"na {host}:{port}"
        ),
        flush=True,
    )

    server.serve_forever()