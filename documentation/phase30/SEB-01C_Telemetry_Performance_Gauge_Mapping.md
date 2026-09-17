# SEB-01C — Telemetry Performance Gauge Mapping Mechanism

**Český název:** Mechanismus převodu telemetrie na relativní výkon a ručičkový ukazatel  
**Projekt:** TNG IQ FANDA  
**Fáze:** 30 / F30.4D  
**Status:** závazný podmechanismus SEB-01  
**Datum:** 2026-09-17

## 1. Závazné pojmenování

Nový podmechanismus se jmenuje:

**SEB-01C — Telemetry Performance Gauge Mapping Mechanism**

Česky:

**Mechanismus převodu telemetrie na relativní výkon a ručičkový ukazatel**

Zkrácené pracovní označení:

**SEB-01C — převod telemetrie na výkon**

SEB-01C navazuje na existující architekturu:

- **SEB-01** = vazba konkrétní entity na konkrétní místo schématu.
- **SEB-01A** = normalizace zdroje / transportu na společnou entitu.
- **SEB-01B** = průběžné propsání aktuální runtime hodnoty nebo stavu entity do schématu.
- **SEB-01C** = deterministický převod jedné runtime hodnoty na normalizovanou relativní škálu a její synchronní grafické znázornění.
- **WIM-01** = určuje skutečné umístění vizuální komponenty v aktivním workspace.

SEB-01C není nový zdroj telemetrie, nový binding ani nový polling mechanismus. Vždy pracuje nad již dostupnou živou hodnotou SEB-01B.

## 2. Účel

SEB-01C se používá tam, kde jedna fyzikální veličina nemá být zobrazena pouze jako číslo, ale má být současně převedena na relativní rozsah, typicky 0–100 %, a tento relativní stav má řídit více vizuálních prvků zároveň.

Příklady:

```text
aktuální elektrický příkon → relativní výkon TČ 0–100 %
teplota → zaplnění teplotní stupnice
tlak → relativní ukazatel rozsahu
průtok → relativní výkon / zatížení
otáčky → procentní zatížení
SOC baterie → procentní graf / ručička
poloha ventilu → 0–100 % otevření
jiná numerická telemetrie → kalibrovaná relativní škála
```

## 3. Základní matematický kontrakt

SEB-01C používá obecný lineární převod:

```text
percent = clamp(
    ((value - value_at_0_percent) /
     (value_at_100_percent - value_at_0_percent)) * 100,
    0,
    100
)
```

Závazná pravidla:

```text
value <= value_at_0_percent     → 0 %
value >= value_at_100_percent   → 100 %
mezi mezemi                     → lineární interpolace
NaN / nekonečno                 → žádné platné procento
unavailable / unknown           → nesmí se převést na 0 %
```

Meze jsou vlastnost konkrétní vizualizace / technologického významu a nesmějí být odvozovány náhodně z okamžitého měření.

## 4. Referenční implementace — tepelné čerpadlo model 00012

Referenční zdroj SEB-01B:

```text
element_key: current-electric-power
entity_id: wifi:shelly_gen2_rpc:6825DDD24164:power_total:0
instalátorský název: Hlavní elektroměr TČ
transport: wifi
runtime zdroj: Agent 0.1.124 / wifi_runtime_poll
```

Kalibrace relativního výkonu tepelného čerpadla:

```text
0 %   = 0,3 kW
100 % = 15,0 kW
```

Výpočet:

```text
percent = clamp(((power_kw - 0.3) / (15.0 - 0.3)) * 100, 0, 100)
```

Referenční hodnoty:

```text
0,208 kW → 0 %
0,300 kW → 0 %
0,407 kW → 0,728... % → zobrazeno 1 %
3,117 kW → 19,16... % → zobrazeno 19 %
4,806 kW → 30,65... % → zobrazeno 31 %
7,650 kW → 50 %
15,000 kW → 100 %
```

Dne 2026-09-17 bylo uživatelsky potvrzeno v produkčním schématu modelu 00012, že při runtime příkonu přibližně `0,407 kW` se zobrazilo `1 %` a ručička se posunula na odpovídající začátek stupnice. Toto je referenční ověření mechanismu SEB-01C.

## 5. Synchronní výstupy

Všechny vizuální výstupy jedné SEB-01C vizualizace musí být odvozené ze stejné vypočtené hodnoty `percent`.

Pro referenční budík TČ:

```text
SEB-01B power_kw
    ↓
SEB-01C percent
    ├── číselný údaj pod ručičkou, např. 31 %
    └── geometrická poloha ručičky na stupnici 0–100 %
```

Je zakázané počítat procentní číslo a polohu ručičky dvěma různými vzorci nebo z různých telemetrických zdrojů.

## 6. Geometrie ručičky

SEB-01C odděluje matematickou škálu od konkrétní geometrie SVG / grafiky.

Závazně platí:

```text
0 %   → fyzický začátek použitelné stupnice
100 % → fyzický konec použitelné stupnice
```

Poloha ručičky se musí mapovat podle skutečné geometrie aktivní komponenty, nikoli podle historického nebo jiného budíku.

Referenční produkční komponenta modelu 00012 používá aktivní gauge ve větvi F29 / aktuálním workspace. Při úpravě je nutné nejprve identifikovat skutečnou aktivní komponentu a až potom připojit procento na její ručičku.

Poučení z F30.4D:

- staré Phase 28 markery nemusí odpovídat právě vykreslenému budíku;
- úspěšné nasazení JavaScript assetu samo o sobě nepotvrzuje správnou vizualizaci;
- LEVEL BROWSER je nutný pro ověření skutečného pohybu ručičky;
- procentní text může být HTML prvek mimo SVG, zatímco ručička je SVG prvek;
- při práci s existující komponentou se nesmí předpokládat její struktura podle historického zdroje.

## 7. Architektura

```text
ZDROJ TELEMETRIE
    ↓
SEB-01A — normalizovaná entita
    ↓
SEB-01 — binding identity → element_key
    ↓
SEB-01B — živá runtime hodnota
    ↓
SEB-01C — kalibrace value → percent 0–100 %
    ↓
jedna společná hodnota percent
    ├── text %
    ├── ručička
    ├── progress / arc
    └── další vizuální reprezentace
    ↓
WIM-01 — správné místo ve workspace
```

SEB-01C nikdy nesmí obcházet SEB-01B přímým dotazováním konkrétního zařízení, pokud je hodnota již dostupná v runtime kontraktu.

## 8. Chování při nedostupnosti

Pokud SEB-01B vrátí nedostupnost, SEB-01C nesmí zobrazit `0 %`, protože nula je skutečný platný stav.

Doporučený kontrakt:

```text
runtime value dostupná   → vypočítat percent a zobrazit
runtime value = 0        → zobrazit skutečný výsledek podle kalibrace
runtime unavailable      → zobrazit „— %“ / nedostupnost
runtime unknown          → zobrazit „— %“ / nedostupnost
NaN / infinity           → zobrazit „— %“ / diagnostika
```

Ručička při nedostupnosti nemá předstírat 0 %. Má být skrytá, neutrální nebo zobrazená ve stavu nedostupnosti podle konkrétního grafického prvku.

## 9. Refresh / výkon

SEB-01C nesmí přidávat vlastní síťový polling pro každý budík.

Správný postup:

```text
SEB-01B společný batch reader získá novou hodnotu
→ React / společný runtime stav se aktualizuje
→ SEB-01C čistě lokálně přepočítá procento
→ všechny navázané vizuální výstupy se překreslí
```

Tím je zachována univerzální datová cesta a projektový požadavek na rychlé aktualizace bez násobení API požadavků.

## 10. Univerzální použití

Při požadavku typu:

```text
„Z tohoto čidla udělej procentní ukazatel.“
„Převeď výkon na 0–100 %.“
„Stejnou hodnotou řiď číslo i ručičku.“
„Uděláme z této telemetrie grafickou stupnici.“
```

má být použit SEB-01C.

Postup:

```text
1. WIM-01: najít skutečnou aktivní komponentu / slot.
2. SEB-01: ověřit binding entity.
3. SEB-01B: ověřit živou runtime hodnotu.
4. Definovat value_at_0_percent.
5. Definovat value_at_100_percent.
6. V SEB-01C vypočítat jeden percent.
7. Tentýž percent použít pro text, ručičku, arc nebo progress.
8. Ověřit minimum, střed, maximum a saturaci mimo rozsah.
9. Ověřit unavailable zvlášť od skutečné nuly.
10. Ověřit skutečný browser render.
```

## 11. Povinné testy

Každá nová implementace SEB-01C musí minimálně ověřit:

```text
A. hodnota pod minimem → 0 %
B. hodnota přesně na minimu → 0 %
C. hodnota uprostřed → 50 %
D. hodnota přesně na maximu → 100 %
E. hodnota nad maximem → 100 %
F. unavailable ≠ 0 %
G. procentní text a ručička vycházejí ze stejného percent
H. 0 % je skutečný začátek aktivní grafické stupnice
I. 100 % je skutečný konec aktivní grafické stupnice
J. změna runtime entity se projeví bez nového per-widget API pollingu
K. admin a klient používají stejný výpočet
L. produkční browser skutečně vykreslí očekávanou hodnotu a polohu
```

## 12. Diagnostika

```text
Číslo % je správné, ručička ne
→ špatná geometrie / jiná aktivní komponenta / ručička není napojena na stejný percent.

Ručička se hýbe, číslo zůstává 0 %
→ procentní text patří jinému DOM prvku nebo není odvozen ze stejného percent.

Příkon se mění, ale percent ne
→ SEB-01C není napojen na skutečný SEB-01B runtime stav.

Percent se počítá ze staré hodnoty
→ problém SEB-01B / cache / runtime freshness, ne matematika SEB-01C.

0 % není na začátku stupnice
→ geometrie budíku je kalibrovaná proti jiné komponentě.

Admin funguje, klient ne
→ client context / WIM-01 / SEB-01B; nevytvářet druhý výpočet pro klienta.
```

## 13. Závazný závěr

**SEB-01C je standardní projektový mechanismus pro převod živé telemetrie na relativní grafickou škálu.**

Používá se vždy, když potřebujeme:

```text
živou hodnotu SEB-01B
→ kalibrovat na 0–100 %
→ stejnou hodnotou řídit procentní text a grafický ukazatel
```

Pro tepelné čerpadlo SVJ Blansko je referenční kalibrace:

```text
0,3 kW = 0 %
15,0 kW = 100 %
```

Tento referenční případ je od 2026-09-17 považován za funkční vzor pro další procentní / ručičkové vizualizace v TNG IQ FANDA.
