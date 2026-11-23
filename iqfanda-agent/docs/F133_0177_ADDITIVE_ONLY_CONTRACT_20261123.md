# F133 – Agent 0.1.177 = fyzicky ověřený 0.1.176 + pouze Midea 00019

**Vytvořeno 23. 11. 2026. Tento commit není produkční release ani pokyn ke globální aktualizaci.** Agent 0.1.177 zůstává v draft PR #47, přenos na RPi 00019 ani první Modbus test zatím nejsou potvrzené.

## Závazný požadavek uživatele
> 177 nesmí absolutně nic změnit na žádném FANDOVI, musí to být jenom tak, že 177 umí navíc Mideu.

Z toho plyne:
- Veškerá **existující** logika verze 0.1.176 – GoodWe, FoxESS, DEYE, SolaX, baterie, nabíjení/vybíjení/STOP, generátory, teplotní regulace, Zigbee, síť, aktualizace Agenta a instalátor – nesmí změnit zdrojovou ani běhovou logiku.
- Nový Midea driver nesmí být spuštěn jen proto, že na zařízení běží verze 0.1.177. Smí být aktivován **pouze** pro přesné `device_uuid=648d4229-7c17-400f-bbed-ffe5ab04b844`, `site_id=980e5e5e-76b4-4cac-9b6c-1f31fa5fd6e1`, při explicitním schváleném čtecím cloud příkazu a se shodným FTDI identifikátorem. Na všech ostatních zařízeních je handler nevykonatelný.
- Midea komunikace používá pouze čtecí **FC04** a doplňkové **FC03**; žádné FC05/FC06/FC15/FC16, žádné výstupy, setpointy, relé, automatické sondování a Modbus sweep.
- Na všech ostatních zařízeních běží původní 0.1.176 kód, bez nové větve heartbeatu. Midea-only inventář ze skutečného `/config/usb_inventory.json` nesmí otevřít port ani vytvářet příkazy.
- **Žádná globální distribuce 0.1.177 a žádné automatické přechody ostatních FANDŮ**, dokud není canary 00019 a regresní kontrola schválená. Ostatní mohou bez omezení zůstat na ověřené 0.1.176.

## Nezměnitelný porovnávací základ F99
- Základní ověřený Agent 0.1.176: Git commit `6a722bac445cb571e26048a88df75a01f34f8c16` na `fandabodis-hash/homeassistant-apps`. Funkční obnova FoxESS 00018 a STOP/AUTO zdokumentována ve F99, main/develop tehdy shodné.
- Git PR #47 větve `feature/f106-midea-00019-readonly-modbus` vychází přímo z této verze.
- Existující produkční soubory se smějí změnit **jen**:
  1. `command_worker.py`: jedna nová podmíněná větev Midea, teď přísně omezená UUID a site; odebráním této větve vznikne **byte-for-byte shodný 0.1.176 soubor**.
  2. `heartbeat.py`: jedna podmíněná USB inventurní větev Midea 00019; odebráním této větve vznikne **byte-for-byte shodný 0.1.176 soubor**.
  3. `config.yaml` a `rootfs/app/VERSION`: pouze označení `0.1.176 → 0.1.177`; všechny ostatní položky a chování zůstávají byte-exact.
- Ostatní změny mohou být jen **nové** izolované moduly `midea_00019_*.py`, testy, Midea dokumentace a izolovaná CI. Zakázané jsou změny souborů 0.1.176 pro baterie, generátor, regulaci, Zigbee, aktualizátor atd.

## F133 automatický kontrolní mechanismus
- `iqfanda-agent/tests/test_f133_agent_0177_additive_contract.py` používá `git show` původního F99 commitu a `git diff --name-status` přímo v CI.
- `test_change_list_is_exclusively_approved_midea_scope` odmítne jakýkoliv starý soubor změněný mimo čtyři uvedené soubory a mimo explicitní Midea-only whitelist; zakáže vymazání/přejmenování.
- `test_worker_176_is_byte_identical...` a `test_heartbeat_176_is_byte_identical...` odeberou jen přesně ohraničené Midea-only vložení a očekávají **bitově stejný obsah jako ověřený 0.1.176 commit**.
- `test_version_metadata_only...` ověří, že veškeré staré addon settings jsou totožné po odebrání samotné změny verze.
- `test_only_exact_midea_identity...` vyžaduje, aby nová worker větev kontrolovala zároveň command type, UUID i site.
- `test_heartbeat_hook_is_uuid_only...` vyžaduje přesnou podmínku a zákaz jakýchkoliv Modbus I/O volání v heartbeatu.
- Test běží ve workflow `F106 Midea 00019 read-only contract` při změně větve; checkout má `fetch-depth: 0`, aby mohl skutečně porovnat pinned F99 baseline.
- Izolované testy čtecího driveru a cloud bridge pro 00019 zůstávají; tyto testy jsou bez připojeného fyzického hardware.

## Co zatím není potvrzeno
Statický source diff ani CI **nedokazují absolutní neexistenci runtime regrese** po aktualizaci addonu. Před širší distribucí je nutná 00019-only canary instalace, potvrzení funkčního fyzického FTDI, první jednotlivý FC04 readback, ověření cloudového backendu a funkčních regresí kritických služeb na referenčním zařízení. Žádný release na main, žádné automatické nasazení dalším klientům.

## Mimo rozsah
V tomto kroku nejsou povolené produkční zásahy, Agent 0.1.177 se nikde nenainstaloval, Modbus RTU se fyzicky nečetl a cloudový backend PR #42 nebyl nasazen.

## Závěrečný výsledek GitHub Actions (23. 11. 2026)
- PR #47 commit `e42800d00860c0cca4ecc9df46d90d43ba1c0a43`: workflow `F106 Midea 00019 read-only contract` **SUCCESS**.
- V jeho logu je F133 **6/6 PASS**, F132 5/5, F107 5/5 a F106 4/4 (celkem 20/20 mockovaných/zdrojových testů); žádné fyzické RTU I/O.
- `Agent universal Smart Control / version consistency`, `F47 R18C`, `F48 R13`, `F52`, `F43` a `F47 R111` jsou SUCCESS.
- Historické vydávací workflow `F99 FoxESS 00018 service recovery candidate` a `Manual battery release contract` zůstávají **FAIL výhradně na release-contract drift `agent_version`** (`0.1.176` → `0.1.177`). Jejich přímé jednotkové testy/F99 fyzická důkazní historie nejsou tímto selháním prokázané jako vadné; u F99 9/9 unit testů prošlo před release guardem.
- Tyto dvě vydávací pojistky **neobcházet, neodstraňovat a neslibovat globální 0.1.177 release**. Vývojový PR je draft; kompatibilita zdrojů je prokázána, fyzická regresní zkouška na 00019 ještě ne.
