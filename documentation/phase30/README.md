# Fáze 30 — registr mechanismů SEB

**Projekt:** TNG IQ FANDA  
**Aktualizace:** 2026-09-17

Tento rozcestník propojuje závazné mechanismy pro práci s entitami ve schématu a pro opakované použití ve statistikách spotřeby. Zachovává hlavní SEB-01 a nezavádí nový oddělený systém podle typu komunikace.

## Rodina mechanismů

| Označení | Název / význam | Dokumentace |
| --- | --- | --- |
| SEB-01 | Schema Entity Binding Mechanism — vazba entity na místo a účel v konkrétní instalaci. | Nadřazený mechanismus společný následujícím dokumentům. |
| SEB-01A | Entity Source Normalization Adapter — normalizace podporovaných zdrojů a transportů. | Popsáno v návaznosti SEB-01B. |
| SEB-01B | Runtime Entity Propagation Mechanism — živé propsání hodnoty nebo stavu do schématu. | [SEB-01B](SEB-01B_Runtime_Entity_Propagation.md) |
| SEB-01C | Telemetry Performance Gauge Mapping Mechanism — převod telemetrie na relativní škálu, společné procento a ručičku. | [SEB-01C](SEB-01C_Telemetry_Performance_Gauge_Mapping.md) |
| **SEB-01D** | **Persistent Energy Consumption Aggregation Mechanism — trvalý výpočet, ukládání a agregace spotřeby energie: den, měsíc, rok, celé evidované období.** | **[SEB-01D](SEB-01D_Persistent_Energy_Consumption_Aggregation.md)** |
| WIM-01 | Workspace Injection Mechanism — vložení prvku do skutečné aktivní pracovní plochy. | Umístění je společný předpoklad mechanismů SEB, ne náhrada jejich datové cesty. |

## Který mechanismus použít

- „Zobraz toto čidlo / stav switche zde“ → SEB-01 + SEB-01A + SEB-01B, umístění podle WIM-01.
- „Převeď tuto hodnotu na procenta a ručičku“ → SEB-01C nad již dostupnou živou telemetrií.
- „Zobraz spotřebu dnes / tento měsíc / tento rok / celkem a použij ji ve statistikách“ → **SEB-01D**, zobrazení přes SEB-01B. Použít trvalé serverové měření, nikoli součet v prohlížeči.

## Referenční potvrzení

SEB-01B: Wi-Fi příkon Shelly v individuálním schématu modelu 00012 po aktualizaci Agentu na 0.1.124; vedle toho stejný zobrazovací tok pro Zigbee entity.

SEB-01C: F30.4D, uživatelsky ověřená společná procentní hodnota a poloha ručičky. Kalibrace TČ 0,3 kW → 0 %, 15 kW → 100 %.

**SEB-01D: F30.4E, backend R7 + opravený řádkový adaptér R8 spuštěný launcherem R8 R2. Uživatel 2026-09-17 potvrdil funkci; klientský screenshot ukazuje `DNEŠNÍ SPOTŘEBA 1,01 kWh *`.** Hvězdička označuje neúplné období.

## Pravidla pro pokračování

Nejprve přečíst příslušný mechanismus a ověřit současnou produkční render větev i datový kontrakt. Historický marker, název souboru nebo PASS nasazovacího skriptu není důkaz aktuálního zobrazení.

Základní šablona individuální instalace zůstává zachovaná. Další adaptér musí být omezený na správnou instalaci a skutečný slot, nikoli globálně přepisovat rodičovský DOM. Zařízení se kvůli zobrazení a statistikám znovu nepárují.

Referenční energetické jádro používá `mereni.<measurement_key>.energie_dnes`, `energie_mesic`, `energie_rok`, `energie_celkem`. Pro tepelné čerpadlo je `<measurement_key> = tepelne_cerpadlo`. Identita měření zahrnuje vlastnící modul a zařízení; samotný text klíče není globálně jedinečný.

Kumulativní historické hodnoty se nesmějí prostě sečíst. Relativní procento budíku ani jeho prahová hodnota nenahrazují energetický čítač; skutečný pohotovostní odběr se do spotřeby započítává.

Univerzální jádro a rozhraní nejsou automatickou úpravou všech vizuálních šablon. Další měřicí profil či transport vyžaduje ověřený čítač, jednotky, vlastnictví a testy. Referenční ověření 00012 není potvrzením přesnosti všech kalendářních přechodů ani end-to-end prodlevy do 20 sekund.

Tento dokumentační zápis nemění Agent, backend, frontend, bindingy ani energetickou historii v produkci.
