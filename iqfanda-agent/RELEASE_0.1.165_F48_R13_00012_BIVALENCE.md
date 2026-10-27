# F48 R13 — targeted Agent 0.1.165 (model 00012 only)

Release candidate: **DO NOT INTERRUPT AN ACTIVE TUV HEATING CYCLE**.
Base source: stable 0.1.164 plus previously audited F48 R10 OFF-readback patch.

## Scope

* Only `device_uuid=28fb44c3-4f8d-4ea4-90ab-f7e6a3b509fc`, ZHA
  `switch.sonoff_zbminil2_3`, `ieee=b0:e8:e8:ff:fe:66:2f:fd`,
  `source=phase32_dhw_thermostat`, `target_state=off`.
* Only an actual HA API `TimeoutError('timed out')` following an
  attempted switch OFF, with original HA state ON and verified device registry.
* Additional HA **GET** readback after timeout, up to two reads, requiring
  a fresh ON→OFF transition before reporting success.
* If any validation fails, preserves the previous failed state; no
  automatic rearm, no new relay ON/OFF, no changes for model 00013.
* This Agent update is per-device and must only be queued via
  `agent_update` for model 00012, with `backup=true`, after the TUV
  heating cycle completes and all 3 TUV outputs are verified OFF.
* No shared cloud backend restart, no 00013 dependency.

## Validation

24 isolated offline helper tests plus standard Zigbee/servo regression
tests are enforced in `f48-r13-00012-off-readback.yml`.
Installation and a full physical TUV cycle are **not** validated by CI.
After OTA, the installed version must be independently confirmed and
the next OFF transition must be observed through command readback.
Existing 0.1.126 to 0.1.165 update includes intervening versions;
this is an integration risk; do not bypass preflight guards.
