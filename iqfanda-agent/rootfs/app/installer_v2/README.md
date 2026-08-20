# TNG IQ FANDA Installer V2

Izolovany instalacni subsistem hlavniho TNG IQ FANDA Agentu.

## Rezimy

- FIRST_INSTALL
  - nove zarizeni bez platne cloudove identity
  - AP bez casoveho limitu

- INSTALLED_BOOT_WINDOW
  - jiz nainstalovane zarizeni
  - servisni AP po dobu 60 sekund

- WIFI_MAINTENANCE
  - instalator zahajil zmenu Wi-Fi
  - servisni timeout je zastaven

- INSTALLED_RUN
  - bezny provoz

- RECOVERY
  - neplatna nebo poskozena cloudova identita
  - nesmi dojit k automatickemu vytvoreni noveho SN

## Ethernet

Installer V2 nesmi predpokladat nazev eth0.
Kabelovy uplink se vybira podle typu rozhrani a stavu,
napriklad end0, eth0 nebo enp*.

## Bezpecnost teto faze

Tato prvni faze neni pripojena k runtime.

Nemeni:

- startup.py
- puvodni installer/
- host/
- provisioning.py
- heartbeat
- komunikacni centrum
- ZHA
- telemetrii
- NetworkManager
- Access Point