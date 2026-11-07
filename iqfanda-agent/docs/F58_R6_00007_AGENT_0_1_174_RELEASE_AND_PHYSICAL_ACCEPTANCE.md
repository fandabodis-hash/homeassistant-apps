# F58 R6 – Agent 0.1.174: bezpečné obnovení FoxESS H3 00007

Datum: 2026-11-07. **Stav: SOURCE/ARM64 TEST PASS, FIELD PASS PENDING, NENASAZOVAT PLOŠNĚ.**

## Autoritativní evidence pro 00007
- `device_uuid`: `1c25a8d4-b986-43ee-b2fc-eaa152e9bdf8`; `F800711-TNG-00007`.
- Aktuální Agent je `0.1.171`; `GUARD_SOURCE=PASS`, `GUARD_FOXESS=BLOCKED`: starý fyzicky ověřený pin je pro `0.1.169`.
- Starý fingerprint: `4baabda610229bb7b0ac55f0990ef464b3db3deb86e7fed98fc3473929530f9c`.
- Starý SHA256 souboru registry: `7717125241c7b4903f9f7f183d3929e49481a4b2ead77e3f5ae33e07fc7a6cdc`.
- Staré evidence ID: `82f9bc6f-0ea9-4dd2-ac58-0526bf5a739b`.
- FoxESS místní readback R5R3: `remote_enable=0`, `remote_active_power_w=0`, `work_mode=0`, `battery_soc_percent=41`, `owner_marker=missing`.
- Cloud R5R1: 3 entity `smartmeter.vykon_l1=-10 W`, `l2=+25 W`, `l3=-21 W` (`age_s=1.4`). Profil `foxess_h3_10_0_e.json` přitom má `smartmeter.vykon_l1–3` ve stavu `enabled:false`, `pending_grid_test`; tyto hodnoty **nepotvrzují signální konvenci ani nezávislou fyzickou ochranu nulového exportu**. Součet při tom konkrétním čase byl -6 W – není doložena žádná bezpečná 500W rezerva pro vybíjecí canary.

## Co je skutečně implementováno ve verzi 0.1.174

1. Nový **nepřepisující** návrh migrace starého fyzického pinu. Při explicitním `manual_battery_canary_v1` pro 00007 vyžaduje přesné UUID, `explicit_technician_approval=true`, konkrétní verzi, runtime SHA a `expected_previous_pin_sha256`. Nesouhlas => STOP ještě před Modbus.
2. Před fyzickým výkonovým testem ukládá celé staré JSON **byte-exact** do `/config/manual_battery_acceptance/prior_pins/pin-registry-<SHA256>.json` pomocí `O_EXCL`. Nikdy starý důkaz nepřepisuje; pokud archiv nelze potvrdit, nedovolí nový test.
3. Během canary obě směrové zkoušky po 500 W, nepřerušená uzamčená RS485 sběrnice, 3 opakované skutečné hodnoty výkonu baterie v každé fázi, ověřené AUTO před / mezi / po. Pin 0.1.174 zapíše **pouze** při PASS skutečného výsledku včetně obnovy `44000=0 / 44002=0`, jinak starý pin zachová.
4. **Nulový export:** `local_modules/foxess_commissioning_grid_guard.py` vyžaduje skutečný nezávislý elektroměr s ověřeným kladným importem, fyzicky ověřený hardwarový limit zákazu přetoků, lokální HA senzor ve wattech a správné `device_uuid`. Měření musí být maximálně 3 s staré, před vybíjením min. 800 W importu, během vybíjení min. 300 W. Výpadek měření => konec canary a AUTO. Tento software **nenahrazuje nezávislou hardwarovou ochranu exportu**.
5. Manuální `discharge_grid` pro **00007** navíc kontroluje lokální měření před prvním i při dalších cyklech. Neznámé měření => nezapínat, STOP/AUTO bez blokace. 00018 má odlišné UUID a zůstává mimo novou povinnost, ale není na 0.1.174 aktualizována.
6. F58 oprava blokování nové manuální relace po STOP (původní draft 0.1.172), F59 pasivní modulární host (draft 0.1.173), bez vložení další fyzické řídicí smyčky.
7. GitHub CI: release manifest append-only rev 15, **9/9 jobů PASS** včetně simulace hardwaru, 3.12/3.13, Zigbee/serva, 00012/00013 TUV, termostat, reset OS, izolovaný ARM64 Docker build (bez publikace).

## Fyzický předávací krok – nepřeskakovat

Za instalaci nelze označit stav `PASS` bez:
1. Nové neměnné místní zálohy Agenta 00007: add-on image + `/config`, včetně pinu a Zigbee mapování, a nové nezaměnitelné produkční VPS plomby před jakoukoli změnou cloudové části. Staré zálohy zachovat. Bezpečně bez tokenů v TXT.
2. **Fyzicky ověřeného nezávislého měření importu sítě, znaménka a čerstvosti.** Neodvozovat znaménko z časově jednoho cloudového vzorku. Na 00007 lokálně při známé zátěži a vypnutém manuálním řízení potvrdit kladný import ve fyzicky samostatném elektroměru.
3. Po ověření uložit pouze pro 00007 lokální soubor `/config/foxess_grid_commissioning_guard.json` v přesném schématu:
   `{"schema_version":1,"device_uuid":"1c25a8d4-b986-43ee-b2fc-eaa152e9bdf8","entity_id":"sensor.<potvrzeny_skutecny_import_w>","physically_verified":true,"independent_grid_meter":true,"hardware_zero_export_verified":true,"positive_import_watts":true,"evidence_id":"<jednoznacny_fyzicky_protokol>"}`.
   **Nepoužívat imaginární `sensor` či odhad z PV/spotřebičů**. Tento soubor nesmí být vytvořen jen proto, že kontrola očekává PASS.
4. Pilotní Agent 0.1.174 instalovat jen na 00007 po úplné kontrole aktuálního release, se zachováním add-on data a rollback image. Nedotknout se 00018/00012/00013.
5. Před jediným fyzickým testem ověřit `verify_battery_feature()` a profilové `BLOCKED` pro starý pin, současný SOC 30–90%, absence jiné relace, zásadně `44000=0` / `44002=0`, samostatný export-limit.
6. Jeden explicitně schválený canary `manual_battery_canary_v1` s `power_w=500`, `expected_agent_version="0.1.174"`, `expected_runtime_fingerprint="82b16e393f516a042381418fc6633a665b4a3e1435e5403d274c50863745c7c1"`, `expected_previous_pin_sha256="7717125241c7b4903f9f7f183d3929e49481a4b2ead77e3f5ae33e07fc7a6cdc"`, exact UUID a technické schválení. Zda backend podporuje bezpečné queueování commissioning požadavku pouze pro daný FANDA ověřit ještě zvlášť; **neposílat slepé Modbus zápisy**.
7. Výsledek testu: 500 W vybíjení pouze při aktuální bezpečné zátěži, 500 W nabíjení, nový profilový pin a 3x AUTO readback. Při FAIL nepokračovat manuálními START.
8. Po fyzickém PASS ověřit přes klientskou Chytrou kartu **novou samostatnou relaci** 500 W nabíjení → STOP/AUTO a následně vybíjení při nezávislém nulovém exportu → STOP/AUTO, bez zastavení termostatů či jiných instalací. Neověřené výkony nad 500 W vyžadují nové posouzení a fyzický limit baterie/BMS.

## Tvrdé závěry
- **SOFTWARE + ARM64 BUILD PASS.** To neznamená, že zařízení již nabíjí/vybíjí.
- **00007 FIELD PASS PENDING**, starý pin zůstává, `0.1.174` není na 00007 nainstalován.
- **NO_GLOBAL_PUBLISH/NO_GLOBAL_DEVICE_UPDATE** do fyzické akceptace a ověření instalace 00018 a chráněných 00012/00013.
- Kritický bod před uvedením do provozu: nezávislé fyzické potvrzení znaménka a zdroje importu. Bez toho automaticky **zastavit** vybíjecí test.
