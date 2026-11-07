# F58 – univerzální STOP/AUTO po vydání 0.1.171

**Stav:** návrhový Agent 0.1.172, nikoliv produkční aktualizace. Úprava pouze v manual_battery_runtime.py; zachována 10s místní kontrola SOC, všechny ovladače a okamžitý STOP.

## Doložený incident

- 00018 / 0.1.168: explicitní STOP uspěl, ale pozdější nový discharge_grid 5000 W opakovaně odmítán: This battery request already ended locally; explicit STOP/new session is required. Cloud v době auditu již idle.
- 00007 / 0.1.171: update ve 14:00 UTC provedl bezpečný lokální STOP process_restart, ale cloud zachoval aktivní discharge_grid 5000 W a opakoval jej každou minutu; lokální Agent jej správně odmítá.
- Staré unfinished příkazy měly všechny expirovanou platnost; nebyly aktuálními blokátory.

## Prokázaný root cause v místním runtime

Po původním explicitním manuálním STOP (clear_latch=True) následující ne-manuální spotový AUTO zavolal _stop nad state=stopped, manual=true. Starý _stop znovu zapsal blocked_signature jen podle příznaku manual, i když už žádný aktivní manuální výkon nekončil. Proto další nový START se stejným podpisem selhal. Restart bezpečně zablokuje starou relaci záměrně; cloud ji však nesmí nadále vydávat za aktivní.

## Náprava kandidáta

- Před vstupem do stop_pending se zapamatuje ended_active_manual = manual && state in preparing/active/stop_pending.
- Latch se nově přidává jen pokud skutečně končí aktivní manuální relace. Staré blokace se samy nemažou. Pouze výslovný manuální STOP může latches vymazat po ověřeném fyzickém AUTO.
- Zůstává fyzická kontrola AUTO, limity SOC/BMS, fail-safe po restartu, timeouts, offline režim, jeden Modbus vlastník.
- Regresní testy na Pythonu 3.12 a 3.13 pro opakované spotové AUTO, bezpečnostní STOP, restart a anti-replay.

## Další provozní hranice

F58 R4 je jednorázový, pro jeden konkrétní serial zvlášť, a přes stávající backend vytváří nejvýše jeden manuální STOP/AUTO. Neodstraňuje bezpečnostní pin. Při změně zdrojových bytů a verze je potřeba nové vydání s validním runtime manifestem, a u fyzicky uzamčeného profilu příslušné ověření. Neobcházet pin mazáním.

Samostatně je nutné opravit cloud: ukončit po restartu lokálně zablokovanou manuální relaci, potlačit opakované replay START a zobrazit zákazníkovi verified vs requested.

**Status:** 0.1.172 ve vývojové větvi; žádná aktualizace GoodWe 00003 či chráněných 00012/00013. Fyzická účinnost 0.1.172 vyžaduje nový dohledovaný test a dokumentaci.