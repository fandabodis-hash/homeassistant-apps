# F47 R117 / Agent 0.1.148 — guarded native SONOFF inching preparation

Only intended for F800711-TNG-00013. There is **no HA UI login required**. This release adds a dedicated cloud-command handler that uses the Agent's already working local HA Core API.

* Exact SONOFF MINI-ZB2GS (IEEE 08:fd:52:ff:fe:07:87:d9).
* OnOff 0x0006 + manufacturer 0xFC11 are present on endpoints 1 and 2, both physical switch entities must read OFF.
* Calls ZHA's `zha.issue_zigbee_cluster_command` with command 0 (manufacturer 0x1286); device packet encodes vendor `protocolData` [0x01,0x17,0x07,0x80,0x80,channel,0x02,0,0,0,XOR] = ENABLE, OFF mode, 1.0 second.
* Configuration command **never** toggles either relay and must not activate servo motor, calibrate, or set any actuation permissions.
* ZHA's current public MINI-ZB2GS quirk only describes attributes on 0xFC11 and may reject protocolData command 0 entirely. Failure is expected/safe and must not be bypassed by a generic Zigbee write. No claim that inching works on ZHA.
* Even if the ZHA call succeeds, that only proves **request submission**, not persistent native OFF. A separate device-local timeout proof and independent electrical interlock / hardwired floor high-limit remain mandatory before physical motion.
* Never update model 00012, change R87 pump logic, R113 shadow service, existing backend/frontend, or change a protected model.
* An exact-model, backup-enabled canary update is required; do not auto-roll-out 0.1.148.

Source of vendor frame: Koenkk/zigbee-herdsman-converters, `src/devices/sonoff.ts`, inchingControlSet converter; manufacturer code 0x1286 from Koenkk/zigbee-herdsman.
