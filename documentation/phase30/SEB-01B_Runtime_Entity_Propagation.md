# SEB-01B — Runtime Entity Propagation Mechanism

**Český název:** Mechanismus průběžného propsání hodnoty entity do schématu  
**Projekt:** TNG IQ FANDA  
**Fáze:** 30 / F30.4C  
**Status:** závazný podmechanismus SEB-01  
**Datum:** 2026-09-17

## 1. Závazné pojmenování

Top-level mechanismus se **nemění**:

- **SEB-01 — Schema Entity Binding Mechanism** = vazba konkrétní entity na konkrétní místo schématu.
- **SEB-01A — Entity Source Normalization Adapter** = převod Zigbee / Wi-Fi / budoucího transportu do společného normalizovaného formátu.
- **SEB-01B — Runtime Entity Propagation Mechanism** = průběžné propsání aktuální hodnoty nebo stavu navázané entity do existujícího slotu schématu.
- **WIM-01 — Workspace Injection Mechanism** = určuje, kam je vizuální komponenta ve skutečné aktivní render větvi vložena.

Zkrácené české pracovní označení SEB-01B:

**živé propsání entity**

SEB-01B není nový binding systém a nevytváří nový typ konfigurace. Používá existující `schema_entity_bindings_v1[element_key]`, společný resolver `build_schema_entity_values()` a společný `schema-entity-values` API tok.

## 2. Závazná architektura

```text
ZDROJ / TRANSPORT
    ↓
SEB-01A — normalizovaná entita
    ↓
SEB-01 — schema_entity_bindings_v1[element_key]
    ↓
společný runtime value resolver
    ↓
schema-entity-values
    ↓
SEB-01B — společný browser batch reader
    ↓
WIM-01 / existující slot schématu
```

Transport po normalizaci nesmí vytvářet vlastní zobrazovací architekturu.

Platí pro:

```text
Zigbee
Wi-Fi
Modbus RTU
Modbus TCP
MQTT
REST
BACnet
budoucí podporované zdroje
```

## 3. Univerzální kontrakt hodnot

SEB-01B musí obsluhovat stejným mechanismem:

```text
number  → teplota, výkon, napětí, proud, energie, vlhkost, tlak, frekvence ...
boolean → skutečný stav switch / relé
state   → on / off / dostupnost / další normalizované stavy
```

Závazná pravidla:

```text
0 je platná hodnota.
false / off je platný stav.
unknown není off.
unavailable není 0 ani off.
NaN / nekonečno se nesmí publikovat jako měření.
VA se nesmí zaměnit za W.
Snapshot z discovery se nesmí vydávat za runtime telemetrii.
Instalátorský název je label, nikoliv identita.
Rozhoduje stabilní entity_id + fyzická identita zařízení.
```

Jeden společný batch hodnot má obsloužit celé aktivní schéma. Nesmí vznikat samostatný polling mechanismus pro každý symbol nebo kartu.

## 4. Zigbee runtime tok

```text
Zigbee zařízení
→ Home Assistant / ZHA runtime stav
→ cloudová telemetrie aktuálního Fandy
→ SEB-01A normalizace
→ SEB-01 resolver
→ schema-entity-values
→ SEB-01B batch reader
→ existující slot schématu
```

SEB-01B žádné Zigbee zařízení nezakládá ani nepáruje. Pouze čte již existující a pojmenovanou infrastrukturu.

## 5. Wi-Fi runtime tok od Agentu 0.1.124

```text
registrované Wi-Fi zařízení
→ periodický READ-ONLY poll v TNG IQ FANDA Agentu
→ existující profil / normalizátor zařízení
→ communication_state.wifi_telemetry
→ cloudový runtime stav konkrétního Fandy
→ SEB-01 resolver
→ schema-entity-values
→ SEB-01B batch reader
→ existující slot schématu
```

Discovery slouží k nalezení / registraci / katalogu. **Discovery není runtime telemetrie.**

Pokud existuje pouze poslední discovery snapshot, resolver má vrátit stav typu `snapshot_only` / bez živé hodnoty. Staré číslo se nesmí zobrazit jako aktuální.

## 6. Aktuální společná verze Agentu

K 2026-09-17 je společná stabilní release verze v `main`:

```text
TNG IQ FANDA Agent: 0.1.124
release commit: 8b093d905c40c0b17834eea86eb9f436b6d73f61
```

Release je sdílený pro TNG IQ FANDA Agent a není implementován jako výjimka modelu 00012.

Vlastnosti Wi-Fi runtime telemetry 0.1.124:

```text
- polluje pouze již registrovaná podporovaná Wi-Fi zařízení,
- používá existující normalizátory / profily,
- zachovává stabilní entity_id,
- publikuje runtime value + dostupnost + receive timestamp,
- odmítá discovery snapshot jako live hodnotu,
- neprovádí discovery v runtime cyklu,
- nemění Wi-Fi credentials,
- nepáruje zařízení,
- neposílá actuator command kvůli telemetrii,
- zařízení bez registrované Wi-Fi infrastruktury pokračuje bez této vrstvy.
```

Poznámka: 0.1.124 je aktuální verze **dostupná** všem Fandám používajícím tento add-on repozitář. Neznamená to, že ji všechny fyzické jednotky už mají nainstalovanou.

## 7. Referenční ověření modelu 00012

Referenční Wi-Fi zařízení:

```text
instalátorský název: Hlavní elektroměr TČ
výrobce / profil: Shelly Pro 3EM / SPEM-003CEBEU
entity_id: wifi:shelly_gen2_rpc:6825DDD24164:power_total:0
element_key: current-electric-power
transport: wifi
```

Po aktualizaci modelu 00012 na Agent 0.1.124 bylo uživatelsky potvrzeno, že v aktivním schématu je opět zobrazena runtime hodnota:

```text
AKTUÁLNÍ ELEKTRICKÝ PŘÍKON = 0,209 kW
```

Současně společný SEB-01 tok obsluhuje Zigbee teploty ve stejném schématu.

## 8. Časový požadavek

Projektový požadavek:

```text
nová dostupná runtime hodnota / stav
→ zobrazení v aktivním schématu
≤ 20 sekund
```

Aktuální návrhové intervaly F30.4C:

```text
Wi-Fi runtime cycle Agentu: cíl 10 s
browser batch poll: 5 s
zbytek: cloud / síť / resolver / render
```

Interval přenosu není totéž jako interval fyzického měření. Bateriové čidlo může reportovat méně často. SEB-01B nesmí vymýšlet nový čas měření; pouze rychle přenáší poslední skutečně přijatý runtime stav.

Pro Wi-Fi se čerstvost ověřuje opakovanou změnou `agent_received_at`. Stejná číselná hodnota může být nový runtime vzorek, pokud má nový receive timestamp.

## 9. Stupně ověření

```text
LEVEL 1 — SOURCE
Zdroj zná správnou entitu.

LEVEL 2 — NORMALIZATION
Entita existuje ve společném SEB-01 kontraktu.

LEVEL 3 — BINDING
Správný entity_id je uložen do správného element_key.

LEVEL 4 — VALUE RESOLVER
Resolver vrací value/state + unit + dostupnost.

LEVEL 5 — AUTHENTICATED VALUE API
Oprávněný admin / klient dostane správnou value-only odpověď.

LEVEL 6 — BROWSER / WIM-01
Hodnota je skutečně viditelná ve správném slotu schématu.

LEVEL 7 — RUNTIME FRESHNESS
Přicházejí opakovaně nové runtime vzorky, stará cache se nevydává za aktuální a změna se dostává do aktivního slotu v projektovém časovém limitu.
```

Za funkční binding stačí LEVEL 1–6. Za potvrzenou živou telemetrii se považuje až LEVEL 7.

## 10. Závazný postup pro další entity

Při rozšíření schématu o další čidlo, veličinu nebo stav switchu:

```text
1. WIM-01: určit skutečný aktivní slot.
2. SEB-01: vytvořit / použít element_key.
3. Admin selector: vybrat již existující zařízení a konkrétní entitu.
4. Uložit schema_entity_bindings_v1[element_key].
5. Pokud transport ještě není normalizovaný, rozšířit SEB-01A — ne UI.
6. Hodnotu číst přes společný resolver + SEB-01B batch reader.
7. Klientovi ukázat pouze value/state, ne selector ani technická metadata.
8. Ověřit 0 / false / on / off / unavailable.
9. Ověřit čerstvost; discovery snapshot nepoužít jako live hodnotu.
```

**Zakázané:** nový jednorázový endpoint, speciální Wi-Fi React komponenta nebo nový polling skript pro jeden bod, pokud stejnou úlohu umí SEB-01.

## 11. Diagnostika

```text
Selector entitu nevidí
→ scope / catalogue / SEB-01A.

Selector entitu vidí a binding je uložen, ale hodnota je "—"
→ runtime zdroj / resolver.

Wi-Fi status = snapshot_only
→ Agent je starší, runtime collector neběží, zařízení je offline nebo profil nepodporuje runtime čtení.
  Neřešit opakovaným discovery.

communication_state.wifi_telemetry obsahuje runtime entitu, SEB-01 ne
→ nesoulad identity / resolveru.

Admin hodnotu vidí, klient ne
→ auth / site access / client context / aktivní WIM-01 slot.
  Nevytvářet druhý klientský mechanismus.

Hodnota je vidět, ale nestárne / nemění se
→ kontrolovat agent_received_at, browser batch poll a cache.

Switch unavailable
→ zobrazit nedostupnost; nikdy nepřevést na off.
```

## 12. Závazný projektový závěr

**SEB-01 zůstává jediným hlavním mechanismem pro vazbu entity do grafického schématu.**

```text
SEB-01  — binding identity → slot
SEB-01A — transport/source → normalized entity
SEB-01B — normalized runtime value/state → live slot
WIM-01  — visual component → correct active workspace
```

Při dalším požadavku „zobraz tuto entitu zde v grafu“ se nemá navrhovat nový mechanismus podle typu zařízení. Nejdříve se použije SEB-01; případně se rozšíří SEB-01A o transport/profil a SEB-01B automaticky využije společný runtime value contract.
