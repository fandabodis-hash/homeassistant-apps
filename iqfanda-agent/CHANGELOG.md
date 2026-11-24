# TNG IQ FANDA Agent – poznámky k vydání

## 0.1.177 – Midea 00019, pouze ruční aktualizace

**Základ:** fyzicky ověřený Agent 0.1.176 (F99, commit `6a722bac445cb571e26048a88df75a01f34f8c16`).

**Jediná přidaná schopnost:** jednorázová čtecí diagnostika Modbus RTU pro přesné zařízení `F800711-TNG-00019` (UUID `648d4229-7c17-400f-bbed-ffe5ab04b844`, lokalita `980e5e5e-76b4-4cac-9b6c-1f31fa5fd6e1`).

- Samostatné moduly `midea_00019_readonly.py`, `midea_00019_cloud_probe.py` a `midea_00019_ftdi_inventory.py`; původní regulační zdrojový kód 0.1.176 je beze změn.
- Pouze na 00019 se aktivuje kontrola fyzického FTDI BG02YF7T; žádné automatické Modbus sondování.
- Nový příkaz `midea_00019_modbus_readonly_v1` vyžaduje přesné UUID a site; jiný FANDA původní chování 0.1.176 neopouští.
- Sběr kandidátních RTU registrů: FC04 a volitelně FC03 **pouze čtení**; žádné zápisy FC06/FC16, změny setpointů, relé, spínání čerpadel nebo narušení autonomního řízení Midea.
- Dosavadní GoodWe, FoxESS, SolaX, DEYE, generátor, tepelné čerpadlo, Zigbee a bateriové řízení mají identické původní zdrojové soubory 0.1.176 (test F133: byte-exact po odebrání jednoznačně vymezených vložení pro 00019).
- **`breaking_versions: ["0.1.177"]` v `config.yaml`** – Home Assistant nikdy nepřekročí tuto verzi automatickou aktualizací. Instalaci musí člověk výslovně potvrdit. Mění se pouze publikace balíčku, žádné zařízení se samotným zveřejněním neaktualizuje.
- Zdrojový a bateriový ochranný manifest změněn pouze pro verzi a dvě izolovaná přidání. Historické fyzické důkazy 0.1.176 zůstávají nepřepsané; fyzický canary test Modbus na 00019 čeká na ruční instalaci.
- Produkční Midea-only cloudové čtecí API (F134 R2) je nasazeno, ale nebude číst RTU bez dalšího výslovného jednorázového admin příkazu.

**Pozor:** Uživatel plánuje ručně aktualizovat výhradně FANDU 00019. Ostatní zařízení na verzi 0.1.176 nepřepínat ani neaktualizovat. Po instalaci 00019 nejprve ověřit heartbeat a FTDI, potom jedno čtecí Modbus měření. 
