# F114 Agent 00019 – čtení stavu z Holding 0/1

Přidán pouze read-only FC03 holding registrů 0–1 na již otevřeném připojení Modbus FANDA 00019 pod stejným zámkem sběrnice (ostatní stávající FC04 telemetry beze změny). Bit 1 holding registru 0 indikuje zónu 1 zapnuto/vypnuto, registr 1 nastavený režim (3 = topení).

Pokud FC03 neodpovídá, hodnoty jsou None a nesmí se domýšlet stav OFF. Bez FC06/FC16, bez zapisování, bez automatického sondování a bez globálního nasazení. Agent 0.1.177 zůstává draft. Vývojová integrace s frontend PR #42 (RTU stav, výstup 105, zpátečka 104, příkon 150, celková spotřeba 143+144).

Kandidátní registrmapa: https://github.com/mkroiss/midea-thermal-artic-r290-modbus/
