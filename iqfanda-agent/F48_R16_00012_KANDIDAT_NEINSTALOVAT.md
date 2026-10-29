# F48 R16 – model 00012: ON/OFF a pripravenost HA

**Stav: testovany zdrojovy kandidat, NE vydana verze, NE produkcni instalace.**

Zaklad: main commit `8c1a13bcf374655e88ccedfb91d78fc66a403b9a` (0.1.166). VERSION/config.yaml se v teto vetvi zamerne nemeni. Prikaz agent_update na 0.1.166 tento kandidat NEnasadi. Vetve se nesmeji zamenit za provozni obraz zarizeni.

## Prokazane R15, 27.10.2026 Europe/Prague

- Bivalence ON 18:16:43: `timed out` za 15.135 s.
- Bivalence OFF 18:18:15 a 19:28:40: `timed out` za 15.128/15.543 s.
- Davka vsech tri OFF 19:33:02: HTTP 400 `System is not ready with state: setup`. Nejde o stejnou chybu jako predchozi timeout.
- Posledni z 11 nactenych agent_update je potvrzeni 0.1.126 dne 1.10.2026. Zadny dnesni update v reportu. Heartbeat hlasi 0.1.126; aktualni lokalni obraz samostatne neoveren.
- Ve 20:35 je TUV enabled=true, fault=null, waiting, teplota 49.9 C, vsechny tri vystupy hlasene OFF, aktivni prikazy prazdne. Bez duvodu pro dalsi automaticky rearm.

## Implementace kandidata

`f48_r16_tuv.py`: samostatne testovatelny vykonavac pro presny UUID 00012, zdroj phase32_dhw_thermostat a tri presne zname TUV entity/IEEE. Jedna zmena importu v main.py zapoji obal pred puvodni startup vlakna. Ostatni identity, zdroje a entity volaji puvodni vykonavac beze zmeny argumentu. Puvodni command_worker.py/startup.py a cloudovy regulator R6 nejsou upravene.

- Kontrola HA /config RUNNING, omezene cekani na setup/cteci timeout do deadline prikazu, nejvyse 75 s celkem.
- Pred ON i pri potvrzeni ON overeni vypnuteho protilehleho zdroje a HA registry/IEEE. Neovlada dalsi vystupy.
- Jediny nejisty POST sluzby se nikdy neopakuje. Po timeoutu se jen cte HA, ON i OFF vyzaduji novy casove odpovidajici prechod a spravnou identitu.
- Vyjimkou je presna HTTP 400 setup odpoved s result=error: nejvyse dva opakovane pokusy po novych kontrolach. Jine HTTP chyby se nepokladaji za setup.
- Obnovene stavy restored=true ani setup objeveny po odeslani povelu nejsou dukazem vykonaneho prepnuti.
- Pri neoverenem stavu zustava chyba. Zadny automaticky reset poruchy, zadna zmena bodu bivalence, zadny zapis backend konfigurace nebo restart backendu.

## Testy a omezeni

34 lokalnich unittest testu PASS, vcetne renderu sablony Jinja, casovych limitu, ON/OFF po timeoutu, setup, registry drift, soubehu zdroju, odmítnuti restored stavu a registrace pred startup. Testy pouzivaji simulovanou HA komunikaci, zadnou sit, DB ani rele. Shodne Git blob SHA: modul `aaf7c75fded71eaa81c7a5c5bde85c5f842f5d86`, testy `ecf2e01f1d6cc35ff774e30becde789cbd133632`.

NEOVERENO: sablona a registry metadata na skutecnem HA 00012, presna HA operace zpusobujici puvodni timeout, pricina stavu setup, kompatibilita vsech meziverzi pri 0.1.126 -> nove vydani, fyzicky stykač, kompletni cyklus a oba smery prechodu bivalence. HA stav neni nezavisle fyzicke mereni kontaktu.

Do spolecneho katalogu nic nepublikovat bez vyr eseneho nasazeni pouze 00012: automaticke aktualizace dalsich instalaci by mohly restartovat jejich Agent. Model 00013 a sdileny backend zustavaji mimo zasah. Zadna procentualni zaruka opravy z offline testu.
