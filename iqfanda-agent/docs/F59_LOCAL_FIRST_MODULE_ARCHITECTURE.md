# F59 – lokální modulární Agent 0.1.173 (VÝVOJOVÝ KANDIDÁT)

**Nejde o vydaný firmware. Nenasazovat na klienty, nezveřejňovat v `main`, dokud nedoběhnou ochranné brány F58 a fyzické canary.** Vychází z návrhu F58 PR #41 (0.1.172), který dosud není fyzicky ověřený.

## Architektura

- `startup.py` pouze spustí společný host jako další vlákno; **žádný nový regulační algoritmus se do `main.py` ani `startup.py` nepřipisuje**.
- Každá další funkce vznikne v **samostatném souboru** v `local_modules/` a do `local_modules/registry.py` se explicitně připojí prověřená funkce. Není povolen import názvu modulu z cloudového payloadu.
- `local_modules/host.py` pracuje jen s lokálně uloženou `/config/cloud-config.json`. Nikdy nevyžaduje cloudové spojení pro opakované cykly. Před každou novou funkcí se ověřuje shoda `device_uuid` a aktivní instalace příslušného modulu.
- **Počáteční registr je prázdný.** Tento kandidát sám nepřebírá stávající baterie, TUV, vytápění, krby, generátory ani Zigbee/Modbus aktory. Stávající vlastníci zůstávají beze změny; dvojí řízení není přípustné.
- Nový modul musí mít samostatné lokální bezpečnostní testy (včetně WAN-offline, rebootu a safe STOP), samostatné řízení výstupů a prokazatelné předání vlastnictví od cloudové/legacy logiky dříve, než je vložen do registru.
- Cloud je **datová/instalační/konfigurační a zobrazovací vrstva**; nikoliv regulační smyčka. Cloudové schedulery se nevypínají před bezpečnou lokální náhradou.

## Ochrany

- F800711-TNG-00018 (FoxESS H3) měl dne 7. 11. 2026 uživatelsky potvrzeno nabíjení a vybíjení 500 W a okamžitý STOP **na skutečně nainstalovaném Agentovi 0.1.168 po F58 R4**. Tento test není fyzickým ověřením nového kódu.
- F800711-TNG-00007 používá 0.1.171; rollback/upgrade nesmí být plošný. Chránit 00012, 00013 a GoodWe 00003.
- Nový verzovaný fingerprint může vyžadovat nové fyzické profilové ověření; neobcházet ochranu, nemažte piny ani nelze interpretovat CI jako fyzické potvrzení.
- Před produkčním nasazením vytvořit **novou** nedestruktivní plnou plombu skutečně běžících Docker obrazů, DB, webrootu, autoritativních zdrojů a SHA256. Nikdy nemazat staré plomby.

## Stav

Zatím pouze bezpečný scaffold, bez jediné nové aktivní technologie a bez nasazení. V dalších PR převádět funkce **jednu po druhé**, nový regulační kód vždy do samostatných souborů. Issue: [F59 LOCAL-FIRST](https://github.com/fandabodis-hash/TNG-IQ-FANDA/issues/17). Související F58: [Agent issue #40](https://github.com/fandabodis-hash/homeassistant-apps/issues/40).
