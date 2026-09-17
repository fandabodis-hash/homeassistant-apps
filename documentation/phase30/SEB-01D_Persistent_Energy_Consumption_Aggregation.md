# SEB-01D — Persistent Energy Consumption Aggregation Mechanism

**Český název:** Mechanismus trvalého výpočtu, ukládání a agregace spotřeby energie  
**Pracovní označení:** SEB-01D — statistiky spotřeby energie  
**Projekt:** TNG IQ FANDA  
**Fáze:** F30.4E  
**Datum zápisu a uživatelského potvrzení:** 2026-09-17  
**Status:** závazný mechanismus pro opakované použití; referenční datová cesta a klientské zobrazení ověřeny na modelu 00012, SVJ Blansko  
**Referenční implementace:** backend F30.4E R7 + zobrazovací adaptér R8, spuštěný opraveným launcherem R8 R2.

## 1. Rozhodnutí a návaznost na rodinu SEB

SEB-01 zůstává hlavním mechanismem vazby entity. Pro spotřebu za období se zavádí podmechanismus **SEB-01D**. Nevzniká druhý nezávislý měřicí systém ani vlastní datový zdroj v prohlížeči.

| Mechanismus | Odpovědnost |
| --- | --- |
| SEB-01 | Vazba konkrétní entity na konkrétní místo nebo měřicí účel v konkrétní instalaci. |
| SEB-01A | Normalizace zdroje a transportu na společný formát entity. |
| SEB-01B | Průběžné doručení aktuálních hodnot a stavů do schématu společnou čtečkou. |
| SEB-01C | Převod aktuální veličiny na relativní škálu, například procenta a ručičku. |
| **SEB-01D** | **Serverový výpočet spotřeby z energetického čítače, trvalý stav a historie, den/měsíc/rok/celkem a jejich zpřístupnění schématu a statistikám.** |
| WIM-01 | Správné umístění prvku v právě aktivní pracovní ploše. |

Související dokumenty: [SEB-01B](SEB-01B_Runtime_Entity_Propagation.md), [SEB-01C](SEB-01C_Telemetry_Performance_Gauge_Mapping.md), [registr Fáze 30](README.md).

**R63 je existující implementační energetické jádro, nikoli konkurenční název SEB-01D.** Tento dokument pojmenovává celý ověřený tok R63 + současná SEB vazba + ukládání + SEB-01B zobrazení. Nepřejmenovává Python soubory, funkce ani `CORE_VERSION = "r63-v1"`.

## 2. Referenční potvrzení

Uživatel po opravě R8 R2 potvrdil funkci a požádal o její registraci pro další statistiky. Přiložený klientský screenshot modelu 00012 ukazuje:

```text
AKTUÁLNÍ ELEKTRICKÝ PŘÍKON   0,208 kW
DNEŠNÍ SPOTŘEBA              1,01 kWh *
Aktuální relativní výkon    0 %
```

Tento screenshot a výslovné potvrzení jsou důkazem skutečného zobrazení v klientském portálu. Hvězdička je součástí správného zobrazení neúplného období, nikoli chyba.

Backendový report R7 před tím doložil:

```text
measurement_key = tepelne_cerpadlo
source = seb01_current-electric-power
energy_entity_id = wifi:shelly_gen2_rpc:6825DDD24164:energy_import_total:0
energy_total_kwh = 133.60157
source_changed = true
delta_kwh = 0.0
today_kwh = 0.0
month_kwh = 12.202
year_kwh = 12.202
total_kwh = 12.202
PORTAL_DERIVED_SLOTS = PASS
CANARY_SEB01_R63_ENERGY = PASS
```

Čísla jsou referenční historické odečty, nesmějí být vložena do programu jako výchozí nebo náhradní živá data. Zachovaných 12,202 kWh není nově změřená spotřeba samotného Shelly od jeho připojení. Celková statistická spotřeba a fyzický stav čítače jsou rozdílné hodnoty.

## 3. Závazná datová cesta

```text
registrovaný a podporovaný elektroměr
    ↓
průběžná telemetrie konkrétního Fandy
    ↓
normalizované entity činného příkonu a kumulativní odebrané energie
    ↓
SEB-01: uložená vazba zdroje v dané instalaci
    ↓
SEB-01D / R63: zpracování v backendu při device_heartbeat
    ↓
rozdíl kumulativního čítače a zařazení do kalendářních období
    ↓
ModuleEntityState + ModuleEntitySample
    ↓
measurement_statistics_view + současné schema-entity-values
    ↓
SEB-01B: existující společná pětisekundová čtečka
    ↓
konkrétní řádek ve schématu / budoucí grafy a tabulky Statistik
```

Počítání nesmí záviset na otevřeném prohlížeči, přihlášení klienta ani přepnutí karty Statistiky. Referenční zápis je součástí backendového zpracování heartbeatu; čtení prohlížečem samo spotřebu nepřičítá.

## 4. Entity a identita měření

Obecný formát již používaný jádrem:

```text
mereni.<measurement_key>.energie_dnes
mereni.<measurement_key>.energie_mesic
mereni.<measurement_key>.energie_rok
mereni.<measurement_key>.energie_celkem
```

Referenční měřicí klíč je `tepelne_cerpadlo`:

| Význam | Trvalý klíč entity | Slot ve společném value-only rozhraní | Jednotka |
| --- | --- | --- | --- |
| Dnešní spotřeba | `mereni.tepelne_cerpadlo.energie_dnes` | `heatpump-energy-today` | kWh |
| Tento měsíc | `mereni.tepelne_cerpadlo.energie_mesic` | `heatpump-energy-month` | kWh |
| Tento rok | `mereni.tepelne_cerpadlo.energie_rok` | `heatpump-energy-year` | kWh |
| Celé evidované období | `mereni.tepelne_cerpadlo.energie_celkem` | `heatpump-energy-total` | kWh |

Jádro dále obsahuje `mereni.tepelne_cerpadlo.vykon_aktualni` v kW. V technickém rozhraní jde o elektrický příkon; není to tepelný výkon ani procento ze SEB-01C.

Stejný název entity může existovat v různých instalacích. **Samotný řetězec `entity_key` není globální identifikátor.** Referenční úložiště vyhledává stav v kombinaci `installation_module_id + entity_key`; záznamy současně nesou `source_device_id`. Vlastnictví a přístup zůstávají v kontextu zařízení, modulu a lokality. Při více měřeních na jedné lokalitě se musí použít jednoznačné měřicí profily, ne přepsat existující profil.

Dřívější dočasné klíče `heatpump_energy_today_kwh` apod. ve `window` nejsou autoritativní trvalé entity. Pro nové statistiky se nepoužívají jako samostatný zdroj pravdy.

## 5. Výběr správného energetického zdroje

Referenční SEB vazba:

```text
schema_entity_bindings_v1["current-electric-power"]
```

Pro 00012:

```text
příkon: wifi:shelly_gen2_rpc:6825DDD24164:power_total:0
energie: wifi:shelly_gen2_rpc:6825DDD24164:energy_import_total:0
runtime source: wifi_runtime_poll
transport: wifi
```

R7 vytváří kompatibilní profil `tepelne_cerpadlo` z této vazby u modulu `individual_project`. Výslovně vytvořený profil se zdrojem `admin_uzv` má v kódu přednost; jinak současná SEB vazba nahrazuje historický fallback `individual_electric_power_meters`.

Energetický zdroj se hledá mezi normalizovanými entitami stejného zařízení jako navázaný příkon. R7 přijímá telemetrické větve s `devices[] → entities[]`; celkový importní energetický čítač preferuje před fázovými čítači.

Při každém dalším použití ověřit:

- stabilní identitu elektroměru a shodu s měřicím účelem;
- činnou odebranou energii, nikoli dodanou energii, zdánlivý výkon VA nebo okamžitý příkon W;
- jednotku a převod Wh/kWh; převádět jednotku jen jednou;
- zda je zdroj celkový třífázový čítač, nebo jednotlivé fáze. Nikdy nesčítat celkový čítač znovu s L1/L2/L3;
- dostupnost skutečné runtime telemetrie, nikoli uloženého discovery snapshotu.

`phase_mode = 1` v referenčním profilu znamená jeden vstupní čítač, který už zahrnuje všechny tři fáze Shelly; není to tvrzení, že elektroměr je jednofázový.

Současné automatické přiřazení energetické entity používá bodování kandidátů. U nového typu elektroměru se jeho správnost musí ověřit; při nejednoznačnosti nelze jen převzít první energetickou entitu.

## 6. Výpočet a význam období

Při nezměněném zdroji a neklesajícím kumulativním čítači platí:

```text
delta_kwh = counter_kwh_now - counter_kwh_previous
statistická spotřeba = dosavadní evidovaná spotřeba + delta_kwh
```

U více samostatných vstupních čítačů jádro sčítá jejich odpovídající přírůstky. Stejná hodnota čítače dává nulový přírůstek, nikoli další započtení celého stavu čítače.

První vzorek nového zdroje nastavuje referenční stav; nepřičítá celý celoživotní stav elektroměru. Při změně zdroje R63 používá `source_rebound`, zachovává evidované celkové množství a začíná sledovat nový čítač. R7 navíc opravuje přenos staré dnešní hodnoty do jiného dne při přepojení zdroje. Přechody měsíce a roku při výměně zdroje je před dalším rozšířením potřeba zvlášť otestovat; samotný screenshot je neověřuje.

Referenční časová zóna profilu je `Europe/Prague`:

| Metrika | Identifikátor období | Význam |
| --- | --- | --- |
| `energie_dnes` | `YYYY-MM-DD` | Od místní půlnoci, pokud je období plně pokryté. |
| `energie_mesic` | `YYYY-MM` | Od začátku místního kalendářního měsíce, s uvedením neúplnosti. |
| `energie_rok` | `YYYY` | Od začátku místního kalendářního roku, s uvedením neúplnosti. |
| `energie_celkem` | Bez denního resetu | Součet od začátku evidovaného měření, včetně zachované návazné historie. |

Když dva odečty překročí hranici období, současné R63 rozděluje přírůstek časovým poměrem intervalu. **Jde o aproximaci rozdělení energie v čase, ne o důkaz přesného odečtu o půlnoci.** Po výpadku přes více období nelze tvrdit přesné rozdělení bez dalších dat.

Při poklesu čítače současná větev R63 předpokládá reset a jako přírůstek bere nezápornou aktuální hodnotu. Tento předpoklad není univerzálním důkazem skutečného resetu. U nových měřidel a při opožděných nebo promíchaných vzorcích je nutné chování ověřit; výměnu měřidla nezaměnit s obyčejným resetem.

**SEB-01C kalibrace 0,3–15 kW se do energetického účtování nepřenáší.** Nulové procento budíku neznamená nulovou spotřebu. SEB-01D započítává skutečné přírůstky energie včetně pohotovostního odběru; nic od nich neodečítá kvůli prahu budíku.

## 7. Trvalý stav a historie

Referenční implementace používá:

```text
app/services/universal_energy_metering_service.py
app/routers/device.py → device_heartbeat
process_universal_energy_measurements(...)
measurement_statistics_view(...)

ModuleEntityState → module_entity_states
ModuleEntitySample → module_entity_samples
source = universal_energy_metering_core_r63
```

Ve stavu se uchovávají také informace o měřicím profilu, předchozím čítači a období, například `meter_entity_ids`, `source_energy_entity_ids`, `last_phase_totals_kwh`, `last_source_total_kwh`, `last_delta_kwh`, `last_captured_at`, `period_id`, `full_period` a `total_from_activation`.

Referenční kód ukládá číselné hodnoty zaokrouhlené na šest desetinných míst. Zobrazení dnešní spotřeby používá dvě desetinná místa. Datum zpracování v cloudu se nesmí vydávat za přesný čas fyzického měření; původní runtime čas má samostatný význam.

Nasazení R7 nevyžadovalo změnu databázového schématu. To nenahrazuje kontrolu dostupnosti modelů a skutečných sloupců při dalším nasazení. Přítomnost historického souboru ve starém skriptu není důkaz, že existuje v aktuálním kontejneru.

## 8. Výstup pro schéma a statistiky

Současná funkce `f30_portal_get_schema_entity_values` doplňuje do odpovědi odvozené sloty z tabulky v oddílu 4. Zpřístupňuje hodnoty, jednotku, dostupnost, kvalitu a čas aktualizace, nikoli klientský výběr zdroje nebo technickou identitu zařízení.

SEB-01B čte tyto hodnoty stejným společným cyklem jako ostatní entity. Referenční čtečka je `IQF_SEB01_RUNTIME`, verze `F30.4C-R5-PERSISTED-READER`; interval je 5 sekund. SEB-01D kvůli jednomu políčku nepřidává vlastní síťový timer. Koncová prodleva celé cesty do 20 sekund zůstává projektovým požadavkem, nikoli výsledkem časového měření tohoto screenshotu.

Referenční R8 adaptér registruje `heatpump-energy-today` a hledá skutečný řádek:

```text
[data-f29-00012-schema="svj-blansko-only"] .f29-00012-heatpump
  .f29-00012-metric-box.f29-00012-metric-box-r2
    > .f29-00012-metric-row
      > span.f29-00012-metric-label    [Dnešní spotřeba]
      > strong.f29-00012-metric-value  [listový textový uzel]
```

Hodnotu mění jen uvnitř jednoznačně nalezeného listového textového uzlu. Nemění rodičovský `textContent`, nepřestavuje React větev a nepřepisuje budík, příkon, tlačítka ani další karty.

**R8 je adaptér konkrétního schématu 00012, ne automatická úprava všech šablon.** Jádro a měřicí profil se znovu používají; u dalšího schématu se připojí jeho skutečný slot přes WIM-01/SEB-01B. UUID a CSS třídy 00012 se nesmějí slepě kopírovat do jiného klienta.

## 9. Kvalita hodnot a hvězdička

```text
platná nula                         → 0,00 kWh
platná energie                      → například 1,01 kWh
partial_period / initializing /
source_rebound                       → například 1,01 kWh *
chybějící nebo nedostupná hodnota     → — kWh
```

Referenční R8 připouští číselnou konečnou nezápornou hodnotu v kWh s platnou dostupností. Odmítá mimo jiné `snapshot_only`, `unknown`, `unavailable`, `offline`, `stale`, `api_stale`, `waiting_for_value` a `not_initialized`.

Hvězdička znamená, že hodnota nemusí pokrývat celé kalendářní období. Po najetí myší se zobrazí vysvětlení. Při aktivaci měření uprostřed dne nesmí být nasbíraný přírůstek prezentován jako kompletní spotřeba od půlnoci. Samotné plynutí času neopraví chybějící historické odečty.

Backendový most R7 odvozuje `has_value` z přítomnosti konečného čísla a předává kvalitu. Nové grafy proto nesmějí posuzovat úplnost nebo čerstvost jen podle `has_value`; musí zachovat i kvalitu, období a časy. Zobrazení není certifikace fakturační přesnosti.

## 10. Závazný postup při dalším použití

1. Určit Fandu, lokalitu, vlastnící modul a jednoznačný `measurement_key`.
2. V aktuální produkci ověřit dostupnost energetického jádra, ORM modelů, heartbeat napojení a současného SEB rozhraní. Neobnovovat slepě staré administrační API a jeho pomocné funkce.
3. Použít již existující a pojmenovanou infrastrukturu. Vybrat a ověřit zdroj příkonu i kumulativní odebrané energie stejného měření; žádné nové párování kvůli statistikám.
4. Použít standardní SEB vazbu nebo existující explicitní měřicí profil. Nevytvářet druhý nezávislý součet v prohlížeči.
5. Ověřit jednotky, celkový versus fázový čítač, časovou zónu, výchozí stav a návaznost historie při změně zdroje.
6. Nechat backend zapisovat stav i historii při zpracování telemetrie, nezávisle na klientovi.
7. Ve stejném autorizačním kontextu ověřit čtyři odvozené hodnoty a jejich kvalitu. Chybějící metrika není nula.
8. Pro konkrétní zobrazení najít aktivní render větev a připojit správný slot k existující SEB-01B čtečce. Preferovat stav/komponentu React; u existujícího adaptéru změnit pouze ověřený listový uzel.
9. Ověřit skutečné vykreslení u oprávněného admina i klienta, nulovou hodnotu, přírůstek, hvězdičku a nedostupnost. Úspěch kopírování souborů není úspěch zobrazení.
10. Uchovat přesný funkční postup a referenční ověření, aniž se znovu mění základní šablona individuální instalace.

Pracovní zadání pro další použití například:

> Použij SEB-01D pro spotřebu bojleru: den, měsíc, rok a celé evidované období. Využij existující měřicí profil a zobraz hodnoty přes SEB-01B.

Pokud čidlo poskytuje jen W/kW a nemá použitelný energetický čítač, tato referenční větev sama kWh nevytvoří. Integrace výkonu v čase by byla samostatné, označené rozšíření s testy mezer a času, ne záměna jednotek ani skrytý fallback.

## 11. Pravidla pro budoucí grafy Statistik

Schéma i statistiky musí používat stejné měřicí profily a trvalé hodnoty. Měsíční a roční přehledy se nesmějí počítat z hodnot opisovaných z DOM.

**Historické vzorky periodické kumulativní hodnoty se nesmějí prostě sečíst.** Opakované vzorky `energie_dnes = 1,01 kWh` nejsou nové přírůstky po 1,01 kWh. Pro graf se použije významově správný konečný stav období nebo přírůstky odvozené s respektováním hranic, resetů a změn zdroje. Rovněž se nesčítají navzájem metriky den + měsíc + rok + celkem.

Odvozené sloty dne/měsíce/roku/celkem jsou připraveným vstupem. Hotové grafy historie v kartě Statistiky nejsou tímto dokumentačním zápisem automaticky implementované.

## 12. Poučení z F30.4E a hranice ověření

- R4 narazila na chybějící soubor R63 v produkci. R5 narazila na chybějící historickou administrační závislost. Zdroj musí odpovídat aktuální produkci, nikoli starému worktree.
- R7 doložila zpracování kumulativního čítače, zápis a serverové odvozené hodnoty. Její původní frontend hledal historický `<small>`, ne aktuální `.f29-00012-metric-row`.
- Neúspěšný starší přepis rodičovského `textContent` poškodil obsah portálu. Tento způsob je zakázaný.
- R8 napravila lokalizaci skutečného listového pole. První launcher R8 selhal na uvozovkách před nasazením. Funkční R8 R2 předává program přes standardní vstup jednoduchému `python3 -u -`.
- R7 skončila lokálním kódem 127 až po dokončeném vzdáleném nasazení kvůli zbylému CR znaku. Samotný konečný PASS nebo FAIL není náhradou za čtení celého reportu a ověření aktuálního stavu.
- Finální uživatelské potvrzení a screenshot `1,01 kWh *` uzavírají referenční chybu zobrazení. Nejsou důkazem pokrytí celého dne, všech transportů, všech instalací, přechodů roku, dlouhodobých výpadků ani měření koncové prodlevy do 20 sekund.

Obecný mechanismus je určen pro všechny Fandy s kompatibilním měřicím profilem a normalizovaným energetickým čítačem. Referenční produkční ověření se týká 00012/Shelly. Další transport, jiný modul, více elektroměrů, reset, opožděné vzorky, souběh zpracování a změna času vyžadují příslušné testy; nelze je prohlásit za ověřené pouze z tohoto případu.

## 13. Zdrojové podklady tohoto zápisu

- Uživatelské potvrzení z 2026-09-17 a screenshot klientského schématu: `DNEŠNÍ SPOTŘEBA 1,01 kWh *`.
- `F30_04E_R7_SEB01_R63_ENERGY_20260917-130928.txt`: profil, zdroj čítače, canary zápis, hodnoty a rozhraní.
- `F30_04E_R7_SEB01_R63_Universal_HeatPump_Energy.ps1`: skutečný vložený kód R63, kompatibilita SEB, heartbeat napojení a value-only most.
- `F30_04E_R8_Energy_Row_Source.zip`: zejména `energy-adapter-r8.js`, `seb01-runtime.js`, `probe_backend.py` a `README_CZ.md`.
- `F30_04E_R8_R2_Fix_HeatPump_Energy_Row.ps1`: opravené spuštění téhož R8 balíčku.

Tyto názvy označují artefakty z pracovního chatu; netvrdí, že jsou všechny uložené v tomto repozitáři. Tento commit registruje dokumentaci a nenahrazuje samostatnou synchronizaci produkčního aplikačního kódu z VPS.

## 14. Závazný závěr

**Pro další požadavky na dnešní, měsíční, roční a celkovou spotřebu se používá SEB-01D.** Zdroj vybere SEB-01, normalizaci zajistí SEB-01A, energii vypočítá a uchová serverové jádro SEB-01D/R63 a hodnoty zobrazí SEB-01B v místě určeném WIM-01. SEB-01C zůstává samostatným relativním ukazatelem a nenahrazuje měření energie.
