# F47 R161 — model 00013, local mixing in Home Assistant Agent 0.1.161

- Only F800711-TNG-00013 with exact device UUID and SONOFF IEEE/registry.
- Local actuator loop: ON for 3 seconds, send OFF to both channels and verify HA relay readback, wait at least 15 seconds; allow next movement only after a NEW HA last_reported OWON sample.
- Target is a locally stored 30.0 C configuration, not a cloud setpoint. Dead band +/- 0.3 C.
- Floor temperature from sensor.owon_ths317_et_4 via local HA state endpoint; local pump switch.sonoff_mini_zb2gs_spinac_5 must be ON. Validity maximum age 300 seconds.
- Local OPEN switch.sonoff_mini_zb2gs_spinac_3 and CLOSE switch.sonoff_mini_zb2gs_spinac_4 are mutually exclusive; exact Zigbee binding checked before movement.
- On OFF failure, watchdog requirement, missing registry, or any ambiguous motor state, the persistent local status is LOCKED. A restart will not clear a previous interrupted pulse.
- On stale sensor, no movement; resume only upon valid fresh HA temperature. Internet is NOT in the feedback loop. The HA machine, Zigbee coordinator and SONOFF still need local power and connectivity.
- The cloud has ONLY one-time installation/commissioning permission before activation. When locally armed, cloud Zigbee manual ON/OFF and servo commands are rejected, and the existing R127 cloud scheduler MUST stay disabled.
- On heartbeat, local controller status is published outward under communication_state.f47_local_mixing_00013; it is never used as a control input.
- Local persisted config: /config/f47_00013_local_mixing_3s15s.json
- Local persisted status: /config/f47_00013_local_mixing_status.json
- Local last pulse reading: /config/f47_00013_local_mixing_last_pulse.json
- The local status is advisory. A radio OFF request cannot independently guarantee physical de-energization. Hardware limit switches and independent high-temperature thermostat are required. Keep the underfloor circuit supervised during commissioning.
- Model 00012, R87 pump service, R90 position estimator, R113 shadow service, old one-second servo commands, protected UI and backend business logic are unchanged.

**Safety note:** The user has validated physical direction with 120-240 second manual actuator tests, but firmware-level auto-off has NOT been demonstrated. Normal local 3-second operation must never be represented as a certified safety cutoff. 
