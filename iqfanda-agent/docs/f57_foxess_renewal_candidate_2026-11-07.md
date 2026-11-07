# F57 – kandidát lokální optimalizace FoxESS (7. 11. 2026)

**Stav: vývojový kandidát; není uvolněná verze Agenta a není nasazen na žádném zařízení.**

## Historický problém a naměřená evidence

Na FANDĚ 00018 (FoxESS H3-10.0-E, Agent 0.1.168) byl 7. 11. 2026 požadavek `charge_grid` 1000 W přijat a Modbus registry ověřeny přibližně za 11 s. Měřený výkon `baterie.vykon` zůstal 0 W až do 03:49:02 a nabíjení 901 W se objevilo v 03:50:05. Skutečný náběh nastal 4:17 až 5:20 po vytvoření povelu (čas kliknutí v browseru neznámý).

Před Agentem 0.1.168 vykonával `command_worker` `execute_battery_control_from_cloud_config` po obdrženém cloudovém záměru. Od 0.1.168 navíc `manual_battery_runtime.main` volá lokální `tick()` přibližně každých 10 sekund. Původní FoxESS driver při každém platném aktivním požadavku zapisoval do 44002 stejné znaménkové I16 číslo, i když ho registr již obsahoval. To je ověřený rozdíl v četnosti zápisů, **nikoli prokázané vysvětlení fyzické prodlevy**. Od 0.1.168 do 0.1.169 se driver a local runtime obsahově nezměnily.

## Cílená změna

Pouze `communication/foxess_ems_controller.py`:

- Při každém volání zůstává `_validate_protocol`, kontrola lokálního SOC, owner markeru, pracovního režimu 41000, remote enable 44000, timeout 44001 a readback 44002.
- Poslední fyzicky ověřený zápis setpointu a jeho UTC čas se atomicky zaznamená do stávajícího FoxESS owner markeru (schema_version=1). Pokud marker/čas chybí, je nečitelný, v budoucnu nebo setpoint nesouhlasí, provede se běžný výkonový zápis. Stejně při změně výkonu nebo rearmu.
- Když souhlasí registr, režim i ověřený marker a poslední zápis je mladší než 60 s, **44002 se podruhé nezapisuje**; přesto následuje stávající závěrečný readback. Výkon se obnovuje nejpozději po 60 s při pravidelně běžícím místním supervizoru; remote timeout zůstává 180 s.
- STOP/AUTO používá původní `_restore_auto` bez zdržení. Žádná změna nastavení SOC, BMS, Modbus adres, GoodWe, Solax, Deye nebo chráněných instalací 00012/00013.
- `write_performed` nyní pravdivě udává, zda tento konkrétní request vyvolal fyzický Modbus zápis.

## Testy

Izolované testy v `test_foxess_battery_control.py` zahrnují běžné směry a STOP, interval bez zbytečného zápisu, obnovu po 61 s, okamžité změny výkonu, rearm při ztrátě 44000/41000/44001, časovou anomálii, selhání readbacku a fail-safe AUTO. Žádná sériová linka ani zařízení se neotevírá.

Nový CI workflow má pouze `contents:read` a nic nenasazuje.

## Nejdůležitější hranice nasazení

Změna produkčního souboru automaticky poruší původní `manual_battery_manifest.json` a otisk runtime. **NENÍ povoleno zkopírovat jediný změněný .py soubor do běžícího Agenta** ani obejít integrity guard. Další revize release vyžaduje výslovný bump verze, konzistentní release + produkční manifest, testy, kontrolu skutečných stávajících profilových pinů a cílený fyzický canary na 00018. Samotné schválení PR nesmí aktualizovat ostatní klienty.

Před fyzickou aktivací je nutno ověřit na 00018 aktuální `stopped/auto`, žádné `stop_pending`, identitu měniče, platný SOC/BMS, stávající `44000` a přesný způsob cíleného bezpečného nasazení. K dispozici musí být cesta návratu a bezpečný fyzický dohled.

**Kandidát není důkazem, že se náběh zkrátil.** To potvrdí až nový experiment s fyzicky změřeným časem od START do výkonu baterie v obou směrech, včetně STOP/AUTO a watchdogu při ztrátě WAN.

## 2026-11-07 04:14 – F57 R4 cloud-only preflight

Read-only report `F57_R4_00018_BEZPECNY_PREFLIGHT_KANDIDATA_20261107-041359.txt` on `F800711-TNG-00018`:

- Agent `0.1.168`, `device_status=online`, heartbeat fresh.
- `local_battery_runtime`: `state=stopped, action=auto, manual=true, verified=true`, FoxESS H3 profile, `stop_attempts=0`, no retry pending.
- Cloud `battery:manual_immediate_charge`: `demand_state=idle`, not active, `reason=manual_user_stop`.
- SOC 60 %, battery power 0 W, recent AUTO commands succeeded, pending agent updates 0.
- Result `F57R4_CLOUD_ONLY_UPDATE_PREFLIGHT=PASS`. **This is not permission to install 0.1.171** and doesn't prove HA Supervisor state or physical Modbus registers.

The next file `F57_R5_00018_LOKALNI_HA_SUPERVISOR_READ_ONLY.ps1` (Windows Downloads, no ZIP) verifies the *local 00018 HA OS host only* via operator-supplied SSH access to that host with Docker permission. It first requires `/data/options.json` serial `F800711-TNG-00018`, then reads `/app/VERSION`, the battery manifest and local STOP state, and internally GETs Supervisor addon/repository metadata using the token already inside the container without revealing it. It **does not** open Modbus, submit commands, restart or update. On no local SSH, wrong serial, unknown auto-update or missing state it fails closed and emits TXT report in Downloads.

Supervisor access is container-internal; using the VPS as the SSH destination is explicitly rejected. Even local PASS does not establish fleet-wide auto-update policy, candidate availability or physical rollback. PR #39 remains a **draft, not published**.
