# SEB-01 — Schema Entity Binding Mechanism

**Český název:** Mechanismus vazby entity Zigbee/Wi‑Fi do místa schématu  
**Projekt:** TNG IQ FANDA  
**Fáze:** 30  
**Status:** závazný funkční mechanismus / významný milník  
**Referenční ověření:** model 00012, individuální schéma SVJ Blansko, karta `BOJLER`, bod `TEPLOTA`  
**Referenční vazba:** `Teplota bojler → Teplota`  
**Referenční entita:** `sensor.sonoff_snzb_02ld`  
**Datum zápisu:** 2026-09-15

---

## 1. Účel mechanismu

SEB-01 definuje jednotný způsob, jak v individuálním grafickém schématu zákazníka napojit konkrétní místo v grafu na konkrétní entitu zařízení, které je napárované na právě editovaného Fandu.

Typický postup:

1. Uživatel v individuálním schématu ukáže nebo určí místo, kde se má zobrazit hodnota.
2. Systém v tomto místě zobrazí administrační výběr.
3. Výběr obsahuje Zigbee a Wi‑Fi zařízení patřící pouze k právě editovanému modelu / účtu / místu instalace.
4. Zařízení se zobrazují pod názvy, které zadal instalatér.
5. Pod zařízením se zobrazují jeho hlavní entity, například `Teplota`, `Napětí`, `Proud`, `Výkon`, `Energie`, `Stav`, `Baterie`.
6. Admin vybere konkrétní entitu a klikne na `Uložit`.
7. Systém uloží vazbu a okamžitě zobrazí hodnotu v daném místě schématu.
8. Klientský účet nevidí výběr ani technické entity; vidí pouze výslednou hodnotu / stav.

---

## 2. Název mechanismu

**SEB-01 — Schema Entity Binding Mechanism**

Doporučený český název v dokumentaci a komentářích:

**Mechanismus vazby entity zařízení do místa schématu**

Krátký pracovní název:

**Vazba entity do schématu**

---

## 3. Vztah k WIM-01

SEB-01 navazuje na mechanismus:

**WIM-01 — Workspace Injection Mechanism**  
**Mechanismus vkládání prvků do aktivní pracovní plochy**

WIM-01 řeší, jak do správné aktivní render větve vložit vizuální komponentu.

SEB-01 řeší, jak tato komponenta získá a uloží vazbu na konkrétní entitu zařízení a jak tuto hodnotu následně zobrazuje adminovi i klientovi.

Zjednodušeně:

```text
WIM-01 = kam se prvek vloží
SEB-01 = jak se vložený prvek napojí na konkrétní entitu zařízení
```

---

## 4. Závazný princip rozsahu

SEB-01 nikdy nesmí hledat zařízení globálně přes celý cloud.

Každý výběr entit musí být omezen na aktuální kontext:

```text
device_uuid právě editovaného Fandy
site_id právě editovaného místa instalace
účet / zákazník daného zařízení
aktuální individuální modul / schéma
```

Pro referenční model 00012:

```text
target_model: 00012
target_device_uuid: 28fb44c3-4f8d-4ea4-90ab-f7e6a3b509fc
target_site_id: 9c4c4838-ae80-41ed-9d45-2e75cad523a9
schema: f29-00012-schema-v2
card: BOJLER
value: TEPLOTA_BOJLERU
```

---

## 5. Závazné pravidlo zobrazování názvů

Selector nesmí jako hlavní názvy zobrazovat technické identifikátory typu:

```text
BASIC-ZB1GSP
SNZB-02LD bez vlastního názvu
ieee
nwk
neighbor_count
entity_count
device_reg_id
entity_id jako název zařízení
```

Selector musí jako primární názvy zobrazovat názvy, které zadal instalatér, například:

```text
Teplota bojler
Elektroměr L1
Elektroměr L2
Elektroměr L3
Akumulační nádrž
Teplota přívod topná bojler
Teplota zpátečka topná bojler
Spínač bojler stupeň 1
Spínač bojler stupeň 2
```

Technické údaje mohou být uloženy uvnitř vazby, ale běžně se nemají zobrazovat jako hlavní text výběru.

---

## 6. Struktura výběru

Výběr má být pro admina seskupený podle instalátorského názvu zařízení.

Příklad pro Zigbee seznam modelu 00012:

```text
Doporučeno pro tento bod
  Teplota bojler → Teplota

Teplota bojler
  Teplota
  Baterie
  LQI
  RSSI

Elektroměr L1
  Napětí
  Proud
  Výkon
  Energie
  Stav
  LQI
  RSSI

Elektroměr L2
  Napětí
  Proud
  Výkon
  Energie
  Stav
  LQI
  RSSI

Elektroměr L3
  Napětí
  Proud
  Výkon
  Energie
  Stav
  LQI
  RSSI
```

U konkrétního bodu ve schématu se má nahoře zobrazit doporučená skupina podle významu bodu.

Příklad:

```text
Bod ve schématu: BOJLER → TEPLOTA
Doporučená položka: Teplota bojler → Teplota
```

---

## 7. Datový model vazby

Doporučený minimální tvar uložené vazby:

```json
{
  "mechanism": "SEB-01",
  "target_model": "00012",
  "target_device_uuid": "28fb44c3-4f8d-4ea4-90ab-f7e6a3b509fc",
  "target_site_id": "9c4c4838-ae80-41ed-9d45-2e75cad523a9",
  "schema_key": "f29-00012-schema-v2",
  "element_key": "boiler.temperature",
  "target_card": "BOJLER",
  "target_value": "TEPLOTA_BOJLERU",
  "transport": "zigbee",
  "device_name": "Teplota bojler",
  "entity_name": "Teplota",
  "entity_id": "sensor.sonoff_snzb_02ld",
  "capability": "temperature",
  "unit": "°C",
  "client_visibility": "value_only",
  "admin_visibility": "selector_and_value"
}
```

---

## 8. Admin pohled

Admin pohled musí obsahovat:

```text
1. aktuální hodnotu v grafu,
2. výběrové pole pro vazbu,
3. seznam zařízení podle instalátorských názvů,
4. hlavní entity pod každým zařízením,
5. tlačítko Uložit,
6. potvrzení, že vazba byla uložena a hodnota převzata.
```

Admin může vidět technické údaje jen jako rozšířenou diagnostiku, ne jako hlavní text výběru.

---

## 9. Klientský pohled

Klientský pohled nesmí zobrazovat:

```text
selector,
entity_id,
device_uuid,
site_id,
technické názvy zařízení,
vnitřní vazební klíče,
diagnostické texty,
tlačítko Uložit.
```

Klientský pohled smí zobrazit pouze běžný uživatelský výsledek, například:

```text
TEPLOTA 51,7 °C
```

Volitelně může zobrazit srozumitelný zdroj bez technických identifikátorů:

```text
Zdroj: Teplota bojler
```

---

## 10. Bezpečnostní pravidla

SEB-01 nesmí při výběru nebo uložení vazby odeslat žádný příkaz do fyzického zařízení.

Zakázáno:

```text
Zigbee command
Wi-Fi command
zapnutí/vypnutí zásuvky
párování
identify efekt
reset zařízení
změna konfigurace zařízení
změna command queue
globální cleanup cloudu
```

Povolené v rámci SEB-01:

```text
čtení katalogu zařízení a entit,
zobrazení selectoru adminovi,
uložení vazby entity do konfigurace individuálního modulu,
zobrazení hodnoty v grafu,
zobrazení hodnoty klientovi.
```

---

## 11. Napojení na Zigbee a Wi‑Fi infrastrukturu

SEB-01 nesmí zakládat Zigbee ani Wi‑Fi zařízení.

Platí pravidlo:

```text
Zigbee/Wi‑Fi infrastruktura je samostatná.
Zařízení se nejdříve napárují a pojmenují v příslušné infrastrukturní kartě.
Technologický modul nebo individuální schéma z těchto existujících zařízení pouze vybírá entity.
```

To znamená:

```text
Konfigurovat Zigbee síť = párování, pojmenování, stav zařízení
Konfigurovat Wi‑Fi zařízení = registrace, pojmenování, stav zařízení
Individuální schéma = výběr již existující entity a její zobrazení
```

---

## 12. Referenční ověření ve Fázi 30

Referenční ověřený případ:

```text
model: 00012
schéma: SVJ Blansko
karta: BOJLER
bod: TEPLOTA
zařízení: Teplota bojler
entita: Teplota
HA entita: sensor.sonoff_snzb_02ld
typ hodnoty: temperature
jednotka: °C
admin: výběr + uložit
klient: pouze hodnota
```

Ověřený uživatelský závěr:

```text
Seznam má používat názvy zadané instalatérem.
Pro bod ve schématu se má vybrat konkrétní entita.
Po uložení se hodnota musí zobrazit normálně v admin pohledu i v klientském pohledu.
```

---

## 13. Závazný postup pro další body ve schématu

Pro každý další bod se použije stejný postup:

```text
1. Identifikovat aktivní schéma a konkrétní bod.
2. Vložit selector pouze do tohoto místa.
3. Načíst zařízení pouze pro aktuální model / device_uuid / site_id.
4. Zobrazit názvy podle instalátora.
5. Doporučit relevantní entity podle typu bodu.
6. Po výběru uložit vazbu.
7. V admin pohledu zobrazit selector + hodnotu.
8. V klientském pohledu zobrazit pouze hodnotu.
```

Příklady dalších bodů:

```text
AKU NÁDRŽ → TEPLOTA
TOPNÝ OKRUH 1 → STAV
TOPNÝ OKRUH 2 → STAV
ELEKTROMĚR → AKTUÁLNÍ VÝKON
BIVALENCE TUV → STAV ZAP/VYP
BIVALENCE TUV → MOTO HODINY
```

---

## 14. Poznámka k dočasným implementacím F30

Dočasné hotfixy F30.2I až F30.2U slouží jako ověřovací implementace mechanismu.

Finální implementace má být sjednocena takto:

```text
1. Nepoužívat statické JSON soubory jako trvalé úložiště.
2. Vazbu ukládat serverově do konfigurace individuálního modulu.
3. Katalog entit generovat z aktuálního cloudového stavu zařízení.
4. Klientskému pohledu vracet pouze sanitizovanou hodnotu.
5. Technické entity a identifikátory neukazovat běžnému klientovi.
6. Mechanismus zobecnit pro všechny body individuálního schématu.
```

---

## 15. Stav dokumentace

Tento dokument zavádí mechanismus **SEB-01** jako závazný projektový celek.

Od této chvíle se při řešení individuálních schémat nesmí znovu začínat od globálního hledání entit v cloudu. Každý další bod ve schématu musí vycházet z tohoto mechanismu.

<!-- SEB01_WIFI_EXTENSION_F30_03F_R2 -->

## 16. Rozšíření SEB-01 pro Wi‑Fi entity — závazné pravidlo od F30.3E

### 16.1 SEB-01 zůstává jediný mechanismus zobrazení

Pro Wi‑Fi entity se **nezavádí samostatný mechanismus zobrazení**. Zigbee a Wi‑Fi se liší pouze zdrojovou / infrastrukturní vrstvou před vstupem do SEB-01.

Závazná architektura:

```text
Zigbee infrastruktura
    ↓
normalizovaná Zigbee entita
    ┐
    ├──→ SEB-01 → schema_entity_bindings_v1[element_key]
    │             → build_schema_entity_values()
    │             → klientská/admin hodnota
    │             → WIM-01 / aktivní místo schématu
    │
native Wi‑Fi discovery
    ↓
SEB-01A — Entity Source Normalization Adapter
    ↓
normalizovaná Wi‑Fi entita
    ┘
```

Platí:

```text
WIM-01 = kam se hodnota vykreslí.
SEB-01 = na kterou entitu je místo schématu navázané.
SEB-01A = jak se nativní zdrojová entita převede do společného formátu SEB-01.
```

**SEB-01A není nový uživatelský mechanismus ani nové Wi‑Fi UI.** Je to podřízená adaptační vrstva SEB-01.

### 16.2 Transportní nezávislost

Od okamžiku, kdy je entita normalizovaná, nesmí další část schématu řešit, zda hodnota pochází ze Zigbee, Wi‑Fi, Modbus RTU, MQTT nebo jiné budoucí komunikační vrstvy.

Závazný společný tok:

```text
SOURCE TRANSPORT
→ normalizační adaptér
→ společný entity option
→ SEB-01 binding
→ value resolver
→ value-only API
→ aktivní React/WIM-01 slot
```

Transport smí zůstat uložený jako technická metadata vazby, ale nesmí vytvářet samostatnou zobrazovací architekturu.

---

## 17. SEB-01A — Entity Source Normalization Adapter

### 17.1 Účel

SEB-01A řeší situaci, kdy infrastruktura již zná správnou Wi‑Fi entitu a její aktuální hodnotu, ale tato entita ještě není součástí generického seznamu `schema-entity-options`.

V takovém případě je **zakázáno**:

```text
vytvořit druhý Wi‑Fi zobrazovací mechanismus,
číst speciální Shelly endpoint přímo z konkrétní karty,
natvrdo zapsat hodnotu do React komponenty,
obcházet schema_entity_bindings_v1,
použít globální cloudový scan,
vytvořit statický JSON jako trvalý zdroj hodnoty,
posílat příkaz do Wi‑Fi zařízení.
```

Správný postup:

```text
1. Najít nativní Wi‑Fi entitu pouze v kontextu aktuálního Fandy.
2. Převést ji přes SEB-01A do společného entity option.
3. Přidat ji do stejného seznamu SEB-01 options jako Zigbee entity.
4. Uložit standardní SEB-01 binding.
5. Předat stejnou normalizovanou entitu také value resolveru.
6. Klient/admin čte hodnotu přes společný schema-entity-values endpoint.
7. WIM-01 ji vykreslí do správného aktivního místa schématu.
```

### 17.2 Minimální normalizovaný tvar Wi‑Fi entity

Normalizační adaptér má vytvořit nejméně tato pole:

```json
{
  "id": "transport-specific-stable-id",
  "transport": "wifi",
  "entity_id": "stable-normalized-entity-id",
  "device_id": "physical-device-id",
  "device_name": "instalátorský název zařízení",
  "entity_name": "srozumitelný název entity",
  "capability": "power",
  "capabilities": ["power"],
  "unit": "kW",
  "label": "instalátorský název · název entity",
  "path": "stable-source-path-or-entity-id",
  "value": 0.0,
  "state": 0.0,
  "available": true,
  "source": "native-source-name"
}
```

Nativní zdroj může mít vlastní pole, například `power_kw`, ale SEB-01A ho musí převést minimálně na společné `value` / `state` + `unit`, aby další vrstvy nemusely znát specifika výrobce.

---

## 18. Druhé referenční ověření SEB-01 — Wi‑Fi Shelly / aktuální příkon TČ

### 18.1 Referenční zařízení

```text
model TNG IQ FANDA: 00012
device_uuid: 28fb44c3-4f8d-4ea4-90ab-f7e6a3b509fc
lokalita: SVJ Blansko
instalátorský název: Hlavní elektroměr TČ
výrobek: Shelly SPEM-003CEBEU
device id / MAC: 6825DDD24164
nativní entita: power_total:0
normalizovaný entity_id:
wifi:shelly_gen2_rpc:6825DDD24164:power_total:0
capability: power
unit: kW
```

### 18.2 Vazba do schématu

```text
target_card: tepelné čerpadlo
target_value: AKTUÁLNÍ ELEKTRICKÝ PŘÍKON
element_key: current-electric-power
transport: wifi
entity_id: wifi:shelly_gen2_rpc:6825DDD24164:power_total:0
client_visibility: value_only
admin_visibility: value / případně selector podle režimu konfigurace
```

Ověřený serverový tok:

```text
latest_successful_native_wifi_discovery
→ SEB-01A normalizace
→ schema-entity-options
→ schema_entity_bindings_v1["current-electric-power"]
→ build_schema_entity_values()
→ schema-entity-values
→ 0.2097 kW
```

Ověřený uživatelský výsledek v aktivním schématu:

```text
AKTUÁLNÍ ELEKTRICKÝ PŘÍKON   0,210 kW
```

Tento případ je druhým referenčním ověřením SEB-01 vedle Zigbee vazby `Teplota bojler → Teplota`.

---

## 19. Závazný pracovní postup pro další Wi‑Fi entitu

Při dalším požadavku typu „zobraz tuto Wi‑Fi hodnotu v tomto místě schématu“ postupovat přesně takto:

```text
1. WIM-01:
   Najít SKUTEČNOU aktuální produkční render větev a konkrétní slot.
   Nikdy nespoléhat na historický marker nebo starý worktree.

2. SCOPE:
   Určit device_uuid, site_id, individual_project a cílový element_key.
   Nehledat zařízení globálně.

3. NATIVE SOURCE:
   Ověřit, že Wi‑Fi infrastruktura zná požadované zařízení a entitu
   a má aktuální read-only hodnotu.

4. SEB-01 OPTIONS:
   Zkontrolovat, zda cílový entity_id již existuje v generických
   schema-entity-options.

5. SEB-01A:
   Pokud tam Wi‑Fi entita není, neopravovat UI.
   Nejdřív doplnit normalizační adaptér a zařadit nativní Wi‑Fi entitu
   do společného seznamu options.

6. BINDING:
   Uložit standardně:
   schema_entity_bindings_v1[element_key].

7. VALUE RESOLVER:
   Ověřit, že build_schema_entity_values() dostane stejnou
   normalizovanou Wi‑Fi entitu, kterou viděl binding layer.

8. VALUE API:
   Ověřit schema-entity-values pro správný účet a device_uuid.

9. ACCESS:
   Admin i superadmin musí být posuzováni konzistentně s hlavní
   administrátorskou politikou.
   Klient bez vlastnictví / SiteAccess musí zůstat odmítnut 403.

10. RENDER:
    Hodnotu vložit do aktivního React/WIM-01 slotu.
    Preferovat React stav / komponentu před globálním DOM overlayem
    nebo MutationObserver hotfixem.

11. VERIFY:
    Potvrdit skutečné zobrazení v prohlížeči.
```

---

## 20. Diagnostická rozhodovací tabulka pro Wi‑Fi zobrazení

```text
A) Nativní Wi‑Fi vrstva entitu nezná
   → řešit Wi‑Fi infrastrukturu / discovery, ne SEB-01 UI.

B) Nativní Wi‑Fi vrstva entitu zná, ale schema-entity-options ji neobsahuje
   → chybí SEB-01A normalizace.

C) Options entitu obsahují, binding nejde uložit
   → kontrolovat payload / normalise_schema_binding_payload / capability.

D) Binding je uložen, ale build_schema_entity_values nemá hodnotu
   → resolver nedostává stejný normalizovaný zdroj jako options.

E) Backend vrací správnou hodnotu, ale schéma ukazuje „—“
   → problém WIM-01 / aktivní render větev / konkrétní React slot.
   Nevracet se k discovery ani nepřepisovat binding bez důkazu.

F) Prohlížeč ukazuje HTTP 401
   → token / přihlášení / expirace.

G) Prohlížeč ukazuje HTTP 403
   → site access / role / owner / SiteAccess.
   Ověřit shodu role admin/superadmin s centrální auth politikou.

H) Endpoint spadne na NameError
   → chybějící runtime dependency/import v route modulu.
   Neopravovat to novým frontendovým obchvatem.

I) Backend E2E vrací číslo a browser ho vykreslí v cílovém poli
   → SEB-01 Wi‑Fi zobrazení je funkční.
```

---

## 21. Implementační poznámky z referenčního Wi‑Fi ověření F30.3E

Při modelu 00012 byly potvrzeny tyto konkrétní chyby a správná řešení:

```text
CHYBA:
native Wi‑Fi discovery znal Shelly power_total:0,
ale generické SEB-01 options ji neobsahovaly.

ŘEŠENÍ:
normalizovat nativní Wi‑Fi option a předat ji options i value resolveru.


CHYBA:
frontendový hotfix hledal starý data-phase28... target,
který v aktivní produkční větvi neexistoval.

ŘEŠENÍ:
použít WIM-01 a najít skutečný produkční React slot.


CHYBA:
schema-entity-values vracel pro superadmin HTTP 403.

ŘEŠENÍ:
sjednotit site guard s existující administrátorskou politikou
(admin + superadmin), bez otevření přístupu cizím klientům.


CHYBA:
route používala DeviceStatus a DeviceConfig bez dostupných
runtime dependencies.

ŘEŠENÍ:
doplnit dependency/import v backendové route, nikoli obejít endpoint.
```

Referenční produkční slot nalezený ve F30.3E:

```text
.f29-00012-heatpump
→ .f29-00012-metric-row
→ strong.f29-00012-metric-value [kW]
```

Tento konkrétní selector je pouze **referenční stopa modelu 00012**. Není dovoleno jej automaticky předpokládat pro jiný projekt. Vždy nejprve ověřit aktivní produkční render větev.

---

## 22. Stupně ověření SEB-01 Wi‑Fi — kdy lze říci „funguje“

Úspěch se nesmí vyhlásit jen podle toho, že jedna vrstva vrací hodnotu.

Povinné stupně:

```text
LEVEL 1 — SOURCE
Nativní Wi‑Fi infrastruktura zná správnou entitu a hodnotu.

LEVEL 2 — NORMALIZATION
Entita je dostupná ve společných SEB-01 options.

LEVEL 3 — BINDING
Správný entity_id je uložen do správného element_key.

LEVEL 4 — VALUE RESOLVER
build_schema_entity_values() vrátí číselnou hodnotu + jednotku.

LEVEL 5 — AUTHENTICATED VALUE API
Přihlášený oprávněný uživatel dostane value API bez 401/403/500.

LEVEL 6 — BROWSER / WIM-01
Hodnota je skutečně viditelná ve správném poli aktivního schématu.
```

Za plně ověřené zobrazení se považuje až `LEVEL 6`.

---

## 23. Závazné pravidlo pro budoucí transporty

Stejný princip platí i pro další budoucí zdroje:

```text
Modbus RTU
MQTT
REST zařízení
BACnet
Modbus TCP
jiné lokální nebo cloudové entity
```

Každý nový transport musí vytvořit normalizovaný entity option a teprve potom vstoupit do SEB-01.

**Schéma nesmí dostávat nový zobrazovací mechanismus podle typu komunikace.**

Závazný cíl:

```text
many transports
→ one normalized entity contract
→ one SEB-01 binding mechanism
→ one value resolver
→ WIM-01 / correct visual slot
```

---

## 24. Traceability — Wi‑Fi referenční ověření

Referenční kroky:

```text
F30.3E R33
- native Wi‑Fi Shelly option nalezen
- binding current-electric-power uložen
- build_schema_entity_values() = 0.2097 kW
- binding/value E2E PASS

F30.3E R34
- nalezen skutečný aktivní produkční React slot příkonu
- hodnota byla napojena přímo do tohoto slotu
- browser diagnostika odhalila HTTP 403

F30.3E R36
- potvrzen a opraven superadmin site access
- potvrzeny a doplněny endpoint dependencies DeviceStatus + DeviceConfig
- foreign client zůstal DENIED_403
- backend value = 0.209700 kW
- browser po obnově zobrazil 0,210 kW
```

**Výsledek:** SEB-01 je od tohoto ověření závazně považován za transportně nezávislý mechanismus a Wi‑Fi entity se mají napojovat přes SEB-01A normalizaci, nikoli novým Wi‑Fi zobrazovacím mechanismem.
