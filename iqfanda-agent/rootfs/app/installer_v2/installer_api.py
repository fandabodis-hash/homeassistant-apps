"""Lokalni HTTP API TNG IQ FANDA Installer V2."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
from typing import Any
from urllib.parse import urlsplit

from installer.network_manager import (
    scan_wifi_networks,
)
from installer_v2.ap_workflow import (
    TypPripojeni,
    konfigurace_ap_pro_rezim,
    pole_prvni_instalace,
    validuj_prvni_instalaci,
)
from installer_v2.models import InstallerMode


VYCHOZI_HOST = "0.0.0.0"
VYCHOZI_PORT = 8099


HTML = """<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>
<title>TNG IQ FANDA - Instalace</title>

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
        Instalace zařízení
    </div>
</div>

<div class="card">

<div class="state">
    <span class="dot"></span>
    Installer V2 · první instalace
</div>

<h1>Nastavení zařízení</h1>

<div class="description">
    Zadejte základní údaje zákazníka
    a způsob připojení TNG IQ FANDA.
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

<label>
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
>
    Pokračovat
</button>

<div
    id="message"
    class="message"
></div>

</div>

<div class="footer">
    TNG IQ FANDA · Installer V2
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
                    "/api/installer/validate",
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

            message.textContent =
                "Údaje jsou v pořádku. " +
                "Installer V2 je připraven " +
                "na další krok.";
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
                HTML
            )
            return

        if path == "/health":
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service":
                        "installer-v2",
                    "mode":
                        InstallerMode
                        .FIRST_INSTALL
                        .value,
                    "writes_enabled":
                        False,
                },
            )
            return

        if (
            path
            == "/api/installer/status"
        ):
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "installer":
                        konfigurace_ap_pro_rezim(
                            InstallerMode
                            .FIRST_INSTALL
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