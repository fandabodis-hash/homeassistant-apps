"""HTTP rozhrani pro servisni zmenu Wi-Fi nainstalovaneho IQ FANDA."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
from typing import Any
from urllib.parse import urlsplit

from installer.network_manager import scan_wifi_networks
from installer_v2.models import InstallerMode
from installer_v2.wifi_maintenance import (
    apply_wifi_change,
    begin_wifi_maintenance,
    cancel_wifi_maintenance,
    get_wifi_maintenance_state,
)


VYCHOZI_HOST = "0.0.0.0"
VYCHOZI_PORT = 8099


BASE_STYLE = """
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:radial-gradient(circle at 50% -20%,#15365e 0,#071424 40%,#03080f 100%);color:#eef6ff}.page{width:min(680px,calc(100% - 28px));margin:0 auto;padding:30px 0 55px}.brand{text-align:center;margin-bottom:25px}.logo{font-size:29px;font-weight:800;letter-spacing:.07em}.logo span{color:#58b8ff}.subtitle{margin-top:6px;color:#91a8bd;font-size:14px}.card{padding:25px;border-radius:22px;border:1px solid rgba(129,187,255,.18);background:rgba(5,17,31,.92);box-shadow:0 18px 55px rgba(0,0,0,.35)}h1{font-size:25px;margin:0 0 12px}p{color:#abc0d2;line-height:1.55}.count{font-size:46px;font-weight:800;text-align:center;margin:22px 0}.row{display:flex;gap:10px}.row>*{flex:1}label{display:block;margin:18px 0 8px;color:#c6d8e7;font-size:14px}select,input{width:100%;padding:13px 14px;border-radius:12px;border:1px solid #294762;background:#081829;color:#fff}button{width:100%;margin-top:18px;padding:14px 16px;border:0;border-radius:12px;font-weight:700;cursor:pointer}.primary{background:#41aaff;color:#02101d}.secondary{background:#17314a;color:#dcecff}.danger{background:#41252b;color:#ffd8dc}.msg{margin-top:18px;padding:12px;border-radius:10px;background:#0b2238;color:#cfe9ff;display:none}.msg.show{display:block}.footer{text-align:center;color:#617c92;font-size:12px;margin-top:18px}
</style>
"""


SERVICE_HTML = f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TNG IQ FANDA - Servis Wi-Fi</title>{BASE_STYLE}</head>
<body><div class="page"><div class="brand"><div class="logo">TNG IQ <span>FANDA</span></div><div class="subtitle">Servisní připojení</div></div><div class="card"><h1>Změna Wi-Fi připojení</h1><p>Zařízení je již zaregistrované v cloudu. Pokud potřebujete změnit síť, pokračujte během servisního okna.</p><div id="count" class="count">60</div><button id="change" class="primary">Změnit Wi-Fi připojení</button><div id="msg" class="msg"></div></div><div class="footer">TNG IQ FANDA · servisní režim</div></div>
<script>
const count=document.getElementById('count');const msg=document.getElementById('msg');async function refresh(){{try{{const r=await fetch('/api/wifi-maintenance/status');const d=await r.json();if(d.remaining_seconds!==null)count.textContent=d.remaining_seconds;if(d.mode==='installed_run'){{count.textContent='0';document.getElementById('change').disabled=true;msg.className='msg show';msg.textContent='Servisní okno skončilo. Pro nové otevření zařízení restartujte.';}}if(d.mode==='wifi_maintenance')location.reload();}}catch(e){{}}}}setInterval(refresh,500);refresh();document.getElementById('change').onclick=async()=>{{const r=await fetch('/api/wifi-maintenance/start',{{method:'POST'}});const d=await r.json();if(r.ok&&d.ok){{location.reload();return;}}msg.className='msg show';msg.textContent=d.error||'Změnu Wi-Fi nelze spustit.';}};
</script></body></html>"""


MAINTENANCE_HTML = f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TNG IQ FANDA - Změna Wi-Fi</title>{BASE_STYLE}</head>
<body><div class="page"><div class="brand"><div class="logo">TNG IQ <span>FANDA</span></div><div class="subtitle">Změna klientské Wi-Fi</div></div><div class="card"><h1>Vyberte novou Wi-Fi</h1><p>Původní Wi-Fi profil zůstane uložený jako záloha až do úspěšného ověření nové sítě.</p><label for="ssid">Wi-Fi síť</label><div class="row"><select id="ssid"><option value="">Vyhledejte Wi-Fi sítě</option></select><button id="scan" class="secondary" style="margin-top:0">Vyhledat</button></div><label for="password">Heslo Wi-Fi</label><input id="password" type="password" autocomplete="new-password"><button id="apply" class="primary">Použít novou Wi-Fi</button><button id="cancel" class="danger">Zrušit změnu</button><div id="msg" class="msg"></div></div><div class="footer">TNG IQ FANDA · Wi-Fi maintenance</div></div>
<script>
const ssid=document.getElementById('ssid');const msg=document.getElementById('msg');document.getElementById('scan').onclick=async()=>{{msg.className='msg show';msg.textContent='Vyhledávám Wi-Fi sítě…';try{{const r=await fetch('/api/wifi/scan',{{method:'POST'}});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'Sken selhal.');ssid.innerHTML='<option value="">Vyberte Wi-Fi síť</option>';for(const n of d.networks||[]){{if(!n.ssid)continue;const o=document.createElement('option');o.value=n.ssid;o.textContent=n.ssid+(n.signal?' ('+n.signal+'%)':'');ssid.appendChild(o);}}msg.textContent='Nalezeno sítí: '+(d.networks||[]).length;}}catch(e){{msg.textContent=e.message;}}}};document.getElementById('apply').onclick=async()=>{{const payload={{ssid:ssid.value,wifi_password:document.getElementById('password').value}};msg.className='msg show';msg.textContent='Testuji novou Wi-Fi. Servisní síť se nyní odpojí.';const r=await fetch('/api/wifi-maintenance/apply',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});const d=await r.json();if(!r.ok||!d.ok)msg.textContent=d.error||'Změnu nelze spustit.';}};document.getElementById('cancel').onclick=async()=>{{await fetch('/api/wifi-maintenance/cancel',{{method:'POST'}});msg.className='msg show';msg.textContent='Změna byla zrušena. Obnovuji původní Wi-Fi.';}};async function state(){{try{{const r=await fetch('/api/wifi-maintenance/status');const d=await r.json();if(d.last_result&&!d.last_result.ok){{msg.className='msg show';msg.textContent='Poslední pokus selhal: '+(d.last_result.error||'neznámá chyba');}}}}catch(e){{}}}}state();
</script></body></html>"""


CLOSED_HTML = f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TNG IQ FANDA</title>{BASE_STYLE}</head><body><div class="page"><div class="brand"><div class="logo">TNG IQ <span>FANDA</span></div></div><div class="card"><h1>Servisní okno je zavřené</h1><p>Pro změnu Wi-Fi zařízení restartujte. Po startu bude servisní Access Point dostupný 60 sekund.</p></div></div></body></html>"""


class WifiMaintenanceHandler(BaseHTTPRequestHandler):
    server_version = "TNG-IQ-FANDA-WiFi-Maintenance/0.1"

    def _path(self) -> str:
        return urlsplit(self.path).path

    def _json(
        self,
        status: HTTPStatus | int,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.send_response(int(status))
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(
            self.headers.get("Content-Length", "0")
        )

        if length <= 0:
            return {}

        payload = json.loads(
            self.rfile.read(length).decode("utf-8")
        )

        if not isinstance(payload, dict):
            raise ValueError("JSON musi byt objekt.")

        return payload

    def do_GET(self) -> None:
        path = self._path()

        if path in ("", "/"):
            state = get_wifi_maintenance_state()
            mode = state.get("mode")

            if mode == InstallerMode.INSTALLED_BOOT_WINDOW.value:
                self._html(SERVICE_HTML)
            elif mode == InstallerMode.WIFI_MAINTENANCE.value:
                self._html(MAINTENANCE_HTML)
            else:
                self._html(CLOSED_HTML)
            return

        if path == "/health":
            state = get_wifi_maintenance_state()
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "wifi-maintenance",
                    **state,
                },
            )
            return

        if path == "/api/wifi-maintenance/status":
            self._json(
                HTTPStatus.OK,
                get_wifi_maintenance_state(),
            )
            return

        self._json(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "error": "Endpoint nebyl nalezen.",
            },
        )

    def do_POST(self) -> None:
        path = self._path()

        if path == "/api/wifi/scan":
            result = scan_wifi_networks()
            self._json(
                HTTPStatus.OK
                if result.get("ok")
                else HTTPStatus.SERVICE_UNAVAILABLE,
                result,
            )
            return

        if path == "/api/wifi-maintenance/start":
            result = begin_wifi_maintenance()
            self._json(
                HTTPStatus.OK
                if result.get("ok")
                else HTTPStatus.CONFLICT,
                result,
            )
            return

        if path == "/api/wifi-maintenance/cancel":
            result = cancel_wifi_maintenance()
            self._json(
                HTTPStatus.OK
                if result.get("ok")
                else HTTPStatus.SERVICE_UNAVAILABLE,
                result,
            )
            return

        if path == "/api/wifi-maintenance/apply":
            try:
                payload = self._read_json()
            except Exception:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": "Neplatna data formulare.",
                    },
                )
                return

            ssid = str(
                payload.get("ssid") or ""
            ).strip()
            password = str(
                payload.get("wifi_password") or ""
            )

            if not ssid:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": "SSID nesmi byt prazdne.",
                    },
                )
                return

            if len(password) < 8:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": "Heslo Wi-Fi musi mit alespon 8 znaku.",
                    },
                )
                return

            threading.Thread(
                target=apply_wifi_change,
                kwargs={
                    "ssid": ssid,
                    "password": password,
                },
                name="wifi-maintenance-switch",
                daemon=True,
            ).start()

            self._json(
                HTTPStatus.ACCEPTED,
                {
                    "ok": True,
                    "action": "testing_new_wifi",
                    "ssid": ssid,
                },
            )
            return

        self._json(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "error": "Endpoint nebyl nalezen.",
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
    server = ThreadingHTTPServer(
        (host, port),
        WifiMaintenanceHandler,
    )

    print(
        f"Wi-Fi Maintenance API posloucha na {host}:{port}",
        flush=True,
    )

    server.serve_forever()
