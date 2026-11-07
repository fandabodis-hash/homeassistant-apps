# F58/F59 0.1.174 — ochrana všech fungujících funkcí při aktualizaci
Datum: 2026-11-07. Stav: **RELEASE BLOCKED / DRAFT**. Žádné automatické nasazení.

## Nezbytná logika vydání

**NIKDY nepovyšovat Git CI na důkaz fyzického fungování.** Žádná aktualizace z`0.1.168`, `0.1.169` nebo `0.1.171` nesmí bez bezpečného migračního kontraktu přepsat již fungující nastavení konkrétní instalace. Různé modely FANDY mohou mít odlišné ovladače, releové mapování, lokální důkazy a firmware střídače.

### 1. Zapečetění skutečné produkce před *každým* zásahem
- Nová neměnná záloha **skutečně běžících** Docker image ID, frontend webrootu a autoritativních zdrojů, backendu, DB dumpu, Compose a SHA256 manifestu.
- Zachovat všechny staré plomby, včetně `F58-LOCK-R1R4-20261107T110234Z`. **Žádné `docker prune`, přepis ani odstranění archivů.**
- Protože historický Git `develop` a frontend VPS se liší, nové nasazení musí nejprve přesně rekoncilovat a důvěryhodně identifikovat *aktuální* vizuálně schválenou produkci. Žádný slepý rollback starého `main.jsx`.
- Před výrobní aktualizací Agenta zhotovit mimo hlavní běh bezpečný export lokálního `/config`, identity, Zigbee/WiFi mapování, historických pinů a přesných otisků, bez vypisování tokenů.

### 2. Celkové automatické regresní testy
`f58-f59-174-cross-module-regression.yml` běží samostatně nad kandidátní větví a ověřuje:
- FoxESS, GoodWe, SolaX a DEYE – dostupné profilové testy, manuální START/STOP/AUTO, SOC a BMS, obnovu po restartu.
- TUV a bivalence 00012, lokální směšování 00013, bezpečnost serv a Zigbee, termostatický týdenní single-owner, Reset OS.
- Nedotknutelnost ochranného SHA256 manifestu a append-only historie fyzických důkazů.
- Oddělené nové lokální moduly, `device_uuid` izolaci a zákaz automatického návratu starého příkazu.
Tyto testy jsou **pouze software/simulace** a **nezahrnují výsledek fyzické instalace**.

### 3. Vizuální a funkční portál musí být ověřen zvlášť
Povinná automatizovaná kontrola **skutečně vykresleného DOM a screenshotů** proti poslední uživatelsky schválené produkci: ne pouze HTTP 200 ani hledání textu ve zdrojovém JS.
- Chytrá karta pro aktivní FVE/bateriový modul – viditelnost, ruční nabíjení, vybíjení, STOP/AUTO, prezentace stavu, graf a výška menu (00007, 00018, 00003).
- Klientské schéma FVE + TČ, spodní symbolický blok TČ, práce s energií, spodní menu; admin TČ pro 00015 a aktivní `heat_pump`, Wi-Fi/Zigbee mapování izolované dle `device_uuid`.
- 00012 TUV, bivalence a obě topné větve; 00013 UFH čerpadlo/krb/servo; 00014 termostat; 00011 generátor; moduly neaktivované na klientském schématu nezobrazovat.
- Žádné neočekávané změny verzovaných aliasů, root dispatcheru, uživatelských mapování a DB.

**Aktuální stav: automatický frontendový vizuální gate ještě NENÍ implementován — RELEASE BLOCKED.**

### 4. Speciální ochrana Agentů a hardwaru
- **00018** (FoxESS H3) má uživatelsky fyzicky ověřené manuální nabíjení a vybíjení 500 W na Agentu `0.1.168` po F58 R4. Bez samostatného přijetí **neaktualizovat**.
- **00007** (FoxESS H3) má Agent `0.1.171`, pin `physically_verified` pro `0.1.169`, po update neshoda brání START. `44000=0`, `44002=0`, Work Mode Self Use, cloud manuální relace idle. Starý pin `4baabda610229bb7b0ac55f0990ef464b3db3deb86e7fed98fc3473929530f9c`, evidence ID `82f9bc6f-0ea9-4dd2-ac58-0526bf5a739b`. Zachovat byte-exact registry SHA256 `7717125241c7b4903f9f7f183d3929e49481a4b2ead77e3f5ae33e07fc7a6cdc`. Nedovolit silent rearm, pin reset, výmaz evidence ani výjimku pro zero export.
- **00012/00013** chráněné: bez samostatného fyzického canary neprovádět zásah. **00003** GoodWe chránit dosavadní manuální nabíjení. Zohlednit i 00014 termostat.
- Vždy single-owner fyzických relé a Modbus; fyzický STOP/AUTO nesmí být blokován bezpečnostním fingerprintem. Nikdy nevyvolávat automatický nový START staré manuální relace.
- Nulové přetoky a omezení BMS se nepovoluje obcházet; případné discharge-grid testy pouze se samostatným nezávislým aktuálním měřením a bezpečným výstupem STOP/AUTO.
- Nové regulační algoritmy do `local_modules/<funkce>.py`; hlavní soubor pouze napojení, cloud jen data/vizualizace/konfigurace.

### 5. Tři nezaměnitelné stavy
- **SOURCE_PASS** = kontrola Gitu, testů, statické integrity a zdrojových manifestů.
- **PREVIEW_PASS** = byte-exact image/bundle + vykreslený frontend, DOM/screenshot a všechny UI kontrakty.
- **FIELD_PASS** = skutečný konkrétní Agent + fyzické bezpečné chování, WAN-offline, START/STOP, reakce na BMS/SOC, restart/obnova, bezpečná změna profilu.

Pro vydání musí být **všechny tři PASS, nová neměnná plomba a výslovná uživatelská akceptace**. Dokud chybí jakýkoli bod, finální stav je **NO_RELEASE / NO_DEVICE_UPDATE**.

### 6. Kontrola po nasazení, návrat a archivace
- Porovnat zařízení po jednom, žádná hromadná aktualizace. Při driftu okamžitě zastavit rollout; nemačkat re-START výkonu.
- Automatický rollback pouze předpřipraveným přesným image, otestovaným pro konkrétní instalaci; nevracet staré zdroje frontendu či cloudovou DB globálně.
- Kompletní TXT audit do `Downloads`, vstupní a výstupní SHA/versions, explicitně `SOURCE_PASS`, `PREVIEW_PASS`, `FIELD_PASS`, `USER_ACCEPTED`; bez osobních hesel/tokenů.
- Udržovat staré release tagy, obrazy, evidence i plomby; nikdy nemažte a nepřepisujte.

**Vydávací rozhodnutí pro 0.1.174 dne 7. 11. 2026: HOLD.**
