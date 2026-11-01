# Lokální manuální baterie: kontrola změn a fyzické ověření

Manuální nabíjení a vybíjení vlastní místní `manual_battery_runtime.py`.
Cloud doručí záměr; obnovování výkonového povelu, konec oprávnění, reakce na
lokální SOC a opakování neověřeného STOP probíhají v addonu. Dostupnost zápisu
je vždy vlastností konkrétního profilu a protokolu. Samotná značka měniče není
dostatečný důkaz kompatibility.

Nový runtime začíná ve stavu **physical pending**. Starší úspěšný povel na
GoodWe, protokolové readbacky, unit testy ani zelené CI nejsou fyzické ověření
této nové verze. `verified` vrácené ovladačem označuje rozsah jeho kontroly
povelu; skutečný směr a výkon baterie je nutné doložit odděleným měřením.

## 0.1.169: jednorázové uvolnění nulového remote požadavku na 00007

R15 dne 1. 11. 2026 doložil na 00007 zapnutý FoxESS remote enable 44000,
nulový vzdálený výkon 44002 a chybějící lokální owner marker. Běžný ovladač
takové řízení správně nepřebírá. Stav `stop_pending` blokuje další START.
Původ zapnutého registru report neprokazuje; firmware ani jiný řadič se
nesmějí označit za příčinu pouze podle chybějícího markeru.

Samostatný příkaz `foxess_zero_remote_release_v1` umožňuje výslovně zadaný
servisní zásah pouze na výrobním čísle `F800711-TNG-00007`. Je svázaný
s lokální identitou zařízení, krátkou platností požadavku, přesnou verzí
a otiskem implementace a s konkrétním stavem zjištěným v R15. Není to
automatická migrace ani obecné převzetí vzdáleného řízení.

Operace používá stejný místní runtime zámek a Modbus zámek jako běžný addon.
Ověří protokol 115, adresu 247, identifikační slova firmware 223/103/403,
work mode 0, timeout 500, max SOC 100, nulový vzdálený setpoint a nepřítomnost
owner markeru. Jediný povolený servisní zápis vypíná 44000. Nemění work mode,
timeout, SOC meze ani výkonový setpoint a nevytváří náhradní owner marker.
Záměr zápisu musí být trvale zaznamenaný před zápisem do měniče; nejasný
výsledek se nesmí automaticky znovu změnit na další pokus o převzetí.

Po ověření registrů běžný manuální STOP dokončí lokální stav a vyčistí
blokování staré relace. Podklady před i po opravě zůstávají v lokálním auditu.
Úspěch servisní operace dokládá pouze uvolnění řízení a ověřený STOP.
Fyzické nabíjení, vybíjení a profilový pin musí nadále potvrdit samostatný
omezený test. Skutečný výkon baterie může být v AUTO nenulový kvůli FV
výrobě nebo spotřebě domu.

Ostatní instalace tento servisní příkaz odmítnou. Normální ownership guard,
běžné nabíjení/vybíjení/STOP a dřívější fyzické protokoly se nemění.

## Tři oddělené kontroly

| Kontrola | Co kontroluje | Co z ní neplyne |
| --- | --- | --- |
| CI release kontrakt | Konkrétní soubory, integraci dispatcheru/startupu/heartbeat, deklarované ovladače, testy a historii změn | Úspěšný test na skutečném měniči |
| Místní integrity guard | SHA256 produkčních souborů v `/app`, verzi a otisk produkčního balíčku | Nezměnitelnost vůči správci, který přepíše kód i manifest |
| Fyzický protokol a profilový pin | Popsanou instalaci, firmware, baterii a přesný testovaný runtime | Ověření ostatních profilů, modelů nebo neprovedených scénářů |

`contracts/manual_battery_release.json` obsahuje širší `source_fingerprint`
pro review i `runtime_fingerprint` pro produkci. Druhý otisk zahrnuje skutečné
produkční soubory a SHA256 `Dockerfile`/`config.yaml` jako `build_inputs`.
Změna samotných testů nebo CI neodvolává důkaz o nezměněném produkčním runtime.
Změna produkce, verze nebo build vstupů vyžaduje nové posouzení.

Manifest `/app/protection/manual_battery_manifest.json` nehashuje sám sebe.
Je odvozený z CI kontraktu a zabalený společně s kódem. Guard jej čte, ověřuje
reálné `/app` soubory i entrypoint `/etc/services.d/iqfanda/run` a vrací
`runtime_fingerprint`. Build vstupy kontroluje CI
proti repozitáři; jejich kopie nejsou za běhu v `/app` dostupné. To je ochrana
proti neúmyslnému přepsání nebo neúplné aktualizaci, nikoli podpis image.
Současný `Dockerfile` používá `base:latest` a balíčky apk. Při pozdějším
rebuildu stejných zdrojů se proto mohou lišit závislosti, které tento otisk
neměří. Do fyzického protokolu zaznamenat i dostupnou identitu image a
prostředí; plnou reprodukovatelnost vyžaduje samostatně připnutý build.

## Kontrolovaná oprava zůstává možná

Po vědomé změně zdrojů nejprve upravit smysluplný regresní test a vysvětlit
důvod změny v PR. Následně spustit z kořene repozitáře:

```sh
python iqfanda-agent/scripts/check_manual_battery_release.py --refresh "Reviewed reason explaining the concrete behavior change"
python iqfanda-agent/scripts/check_manual_battery_release.py
PYTHONPATH=iqfanda-agent/rootfs/app python -m unittest discover -v -s iqfanda-agent/tests -p 'test_*battery*.py'
PYTHONPATH=iqfanda-agent/rootfs/app python -m unittest discover -v -s iqfanda-agent/tests -p 'test_foxess_remote_control_probe.py'
```

Refresh aktualizuje oba manifesty a přidá revizi historie, pokud se změnil
zdrojový otisk. Nevytváří fyzický důkaz a nepřepisuje profilové piny zařízení.
Opakovaný refresh beze změny nevytváří další revizi. CI navíc porovnává historii
a fyzické záznamy s výchozím commitem PR/pushe: starší záznamy nelze přepsat
nebo smazat. Zneplatnění důkazu se zapisuje do `revocations` s důvodem.

Guard nedovolí potichu zahodit již podporovaný profil ani odebrat AUTO při
zachování směrových povelů. Nutné úmyslné odebrání nebezpečné podpory proto
musí mít samostatnou, viditelnou změnu kontraktu a jeho testů v review. Běžná
oprava algoritmu je přípustná přes nový manifest a regresní testy.

GitHub workflow `manual-battery-release-contract` běží na PR a pushi.
V nastavení repozitáře má být jeho úspěch vyžadován před sloučením a mají být
kontrolovány změny samotného guardu. Přítomnost YAML souboru sama nezapíná
branch protection; toto nastavení není tímto commitem změněno.
Původní CI ochrany 00012/00013 zůstávají. Verze musí být konzistentní mezi
`VERSION`, `config.yaml` a `software_version`; staré testy už neblokují každý
nový release pouze kvůli pevnému seznamu verzí.

## Místní profilový pin

`verify_battery_feature(profile_id)` čte pouze soubory. Volání bez profilu
kontroluje integritu. Volání s přesným vybraným profilem navíc respektuje jeho
pin v `/config/manual_battery_verified_fingerprint.json`. Chyba integrity
nebo neshodný ověřený pin musí zastavit nový START/obnovení výkonu.
**Bezpečný STOP/AUTO nesmí být závislý na úspěchu guardu.**

Přirozený počáteční stav je chybějící pin nebo `status: pending` pro daný
profil. Umožňuje řízený fyzický test přesně daného kandidáta. Po skutečném
testu se ukládá pouze profil, který byl na dané instalaci změřen. Struktura:

```json
{
  "schema_version": 1,
  "profiles": {
    "foxess_h3_10_0_e": {
      "status": "pending"
    }
  }
}
```

Položka `physically_verified` vyžaduje skutečný `runtime_fingerprint`,
`agent_version`, `evidence_id` a `report_sha256`. Při zápisu nové položky
zachovat ostatní profily. FoxESS pin neoznačuje GoodWe, SolaX ani Deye za
ověřené. Guard nikdy nepovýší `pending` na `physically_verified` sám.

Existující ověřený pin blokuje nový výkonový povel po změně produkčního
otisku. Výběr pinu je profilový, samotný produkční otisk však chrání celou
bateriovou funkci: i změna jiného ovladače nebo verze addon balíčku vyžaduje
nové posouzení. Není to oddělený dependency fingerprint pro každou značku.
Při legitimní aktualizaci nejprve ukončit starou relaci a ověřit STOP,
uchovat předchozí protokol a pin, zapsat výslovný přechod konkrétního profilu
na pending s důvodem a provést nový test. Neopravovat neshodu prostým
přepsáním ověřeného otisku bez měření. Tento postup ponechává možnost
opravy a současně brání tichému převzetí starého označení ověřeno.

Místní pin odkazuje na rozsah konkrétního fyzického protokolu. Krátký canary
nabíjení/vybíjení/STOP nedokazuje výpadek WAN, chování při restartu ani všechny
SOC hranice. Úplný release záznam níže proto může zůstat pending i po
úspěšném krátkém canary.

## Úplný záznam fyzického ověření

`contracts/manual_battery_physical_evidence.json` začíná prázdnými seznamy
`records` a `revocations`. Úspěšný dokončený záznam musí identifikovat přesný
profil, seriové číslo zařízení, model měniče, firmware, baterii, UTC čas,
posuzovatele a uchovaný protokol s SHA256. Obsahuje oba otisky a verzi z
odpovídající revize release kontraktu.

Požadované položky `checks` jsou:

| Klíč | Co musí být skutečně doloženo |
| --- | --- |
| `charge_measured` | Požadovaný, povolený a zapsaný výkon odděleně od měřeného nabíjení, čas a SOC |
| `discharge_measured` | Opačný skutečný směr baterie, čas, SOC a respektování minima |
| `manual_stop_confirmed` | STOP vrátí řízení do AUTO; nulový vzdálený setpoint a změna fyzického toku jsou vyhodnoceny ve správném kontextu |
| `target_soc_stop` | Nabíjení končí při cílovém SOC a opakování starého požadavku jej znovu nespustí |
| `minimum_soc_stop` | Vybíjení končí na nastaveném minimu s bezpečnostní rezervou |
| `expiry_stop` | Vypršení místního oprávnění ukončí relaci bez nového cloudového povelu |
| `wan_loss_local_control` | Během povolené relace funguje lokální dohled i při výpadku WAN a na konci relace proběhne STOP |
| `agent_restart_safe` | Restart obnoví bezpečný stav a sám znovu neaktivuje starý ruční povel |
| `bms_limits_respected` | BMS limity, SOC a teplotní omezení zůstávají zachována |

Každá položka má `passed: true` a neprázdný `report_section` odkazující na
skutečnou část protokolu. `verification_origin` musí být `physical_installation`.
Kontrola JSON ověřuje úplnost a návaznost záznamu, nikoli pravdivost měření;
posuzovatel musí skutečný protokol přečíst. Nedokončené nebo neúspěšné testy
se nesmějí zapisovat jako dokončený úspěšný záznam.

Nižší skutečný výkon než požadovaný nemusí být chyba ovladače: oddělit limit
BMS, teplotu baterie, provoz měniče a podmínky sítě od transportu povelu.
Kvůli testu nepřekračovat limity výrobce a nevypínat BMS ochrany. Pozorování
z jednoho kusu ani z jiné řady stejné značky nepřenášet na všechny profily.

Úplnou fyzickou připravenost konkrétního profilu lze vyžadovat samostatně:

```sh
python iqfanda-agent/scripts/check_manual_battery_release.py --require-physical foxess_h3_10_0_e
```

Výchozí CI dovoluje řízený kandidátní release s pending stavem. Příkaz výše
se při pending ukončí chybou; není používán jako zákaz prvního canary testu.

## Publikace a cílená aktualizace 00018

V tomto repozitáři addon neuvádí `image:` a používá `Dockerfile` s buildem na
cílovém HA. Publikace nové verze do výchozí větve zpřístupní metadata všem
instalacím připojeným k této větvi. Jediný cílený command pro 00018 není
záruka, že jiná instalace s vlastním `auto_update: true` současně nezahájí
aktualizaci. Rozlišovat **rozsah našeho příkazu** a **viditelnost release**.

Pro existující updater je cílený záměr jeden `DeviceCommand` pro přesné
`device_id` ověřené jako 00018:

```json
{
  "command_type": "agent_update",
  "payload": {"target_version": "0.1.168", "backup": true}
}
```

To je kontrakt obsahu příkazu, nikoli přenositelný přímý SQL INSERT: použitý
backend musí doplnit skutečné modelové atributy, identifikátor a stav fronty.
Nevytvářet hromadný příkaz, smyčku přes zařízení ani aktualizaci pro 00012/00013.
Opakované spuštění nasazovacího skriptu musí najít existující čekající/aktivní
příkaz a nepřidat jeho duplikát. Před aktualizací musí být ukončen aktivní
ruční test a případný `stop_pending`.

Aktuální `agent_updater.py` hledá konkrétní HA update entity, vyžaduje podporu
backup a volá `update.install` s `backup: true`. Službě neposílá pevné číslo
verze. Jeho validátor může připustit meziverzi nižší než požadovaný cíl.
R13 ověří veřejně publikovaný release a stávající updater obnoví lokální
metadata HA. Samostatné čtení lokálního HA `latest` dosavadní worker
neposkytuje. Fyzické canary je povoleno až po potvrzení skutečné instalované
verze **0.1.168** a po porovnání `expected_runtime_fingerprint` přímo
v addonu před zahájením testu. Pokud HA nasadí meziverzi, R13 skončí bez
fyzického testu a bez automatického dalšího update. Samotné pole
`target_version` ani veřejně viditelná verze nejsou pin instalovaného image.

Po restartu worker uzná dokončení až při shodě skutečného `/app/VERSION`
s uloženým cílem. Pending marker je `/config/pending_agent_update.json`;
timeout workeru je 900 sekund. Během pending markeru worker nepřebírá další
cloudové příkazy. Úspěch se proto dokládá výsledkem příkazu, následným
heartbeat/verzí, lokálním runtime stavem a teprve potom fyzickým canary.
Dokončený build nebo restart sám nedokazuje funkční baterii. Backup request
neznamená, že proběhl rollback; případná obnova musí být zvlášť doložena.

Oddělená kandidátní větev `repo#branch` je podporovaná cesta distribuce.
Není však automaticky bezpečnou migrací stávajícího addonu: ověřit výsledný
slug, přenos konfigurace/dat a jediného vlastníka Modbus řízení. Neinstalovat
druhého současně zapisujícího vlastníka měniče jen kvůli canary. Samotné
vytvoření PR/beta větve neaktualizuje existující instalace na výchozí větvi.

## Read-only preflight na daném HA

`scripts/read_addon_update_preflight.py` lze spustit uvnitř vybraného addonu
s jeho existujícím `SUPERVISOR_TOKEN`. Používá pouze dva lokální GET requesty:

| Endpoint | Použité údaje |
| --- | --- |
| `/addons/self/info` | slug, repository slug, version, version_latest, build, auto_update a stav |
| `/store/repositories/<repository>` | shoda očekávaného repozitáře a případná `#branch` |

Výstup dále ukáže místní bateriový stav, deklarovaný otisk a profilové piny,
pokud tyto soubory existují. Nevypisuje options, token, hesla ani celý runtime
intent. Nic neinstaluje, nerestartuje, nezapisuje do fronty a neposílá měniči.
Neznámý místní stav je ve výstupu `null`, nikoli tvrzení, že je zařízení
nečinné. Nezná nastavení ostatních HA.

Interní adresa `http://supervisor` není dostupná automaticky z VPS.
Dosavadní command worker nemá obecnou vzdálenou funkci pro provedení tohoto
preflightu. Tento skript je pomůcka pro již zajištěný místní přístup, nikoli
nový tunel nebo způsob, jak z VPS číst `auto_update` všech zařízení.

## Zdroje k chování Home Assistant

- [Supervisor: installed apps](https://contributors.home-assistant.io/apps/supervisor-api/apps/) — `/addons/self/info` a pole `auto_update`, `repository`, `version`, `version_latest`, `build`.
- [Supervisor: store](https://contributors.home-assistant.io/apps/supervisor-api/store/) — repozitáře a update na latest; připojení verze do URL nepinuje starší vydání.
- [Publishing apps](https://contributors.home-assistant.io/apps/guides/publishing/) — build na zařízení, pokud chybí `image`, a distribuce předem postavených image.
- [Presenting apps](https://contributors.home-assistant.io/apps/guides/presentation/) — oddělená beta větev přes `#branch`.

Konkrétní chování tohoto projektu vychází ze souborů `agent_updater.py`,
`command_worker.py`, `startup.py`, `heartbeat.py`, `manual_battery_runtime.py`,
profilů a testů v této revizi. Před dalším releasem zkontrolovat, zda se
updater nebo rozhraní Supervisor nezměnily.
