# F47 R114 — Agent 0.1.147 (00013 remote ZHA canary)

## Reason
The technician cannot access the customer's Home Assistant user interface. FANDA Agent already communicates with local HA Core using Supervisor credentials and with TNG IQ FANDA cloud. Agent 0.1.147 publishes the already reviewed F47 R112 **read-only** ZHA inventory command from PR #9.

## Scope and invariants
- No generic remote shell or unauthenticated HA proxy.
- No Zigbee state change from the ZHA probe (`zigbee_servo_capability_probe_v1`).
- Servo pulse execution remains fail-closed while no device-native OFF and independent interlock are attested.
- Existing pumps, thermostat, AKU/TUV, frontend, and model 00012 are not modified.
- Targeted canary update must address ONLY F800711-TNG-00013, with normal HA update backup=true; not automatic rollout.
- Firmware manufacturer cluster 0xFC11 in ZHA inventory is NOT proof of inching / auto-off.
- Device-local auto-off is a prerequisite to movement, but independent thermal and electrical safety protections must also be commissioned before unattended operation.

## Canary release gates
- Verify R112 49 regression cases through CI and build validity.
- Confirm 0.1.147 metadata matches runtime VERSION.
- Verify 00013's existing Agent version and live heartbeat; freeze protected 00012 baseline.
- Trigger only the exact 00013 Agent update with backup=true; do not deploy if model or active command state differs.
- Run the read-only ZHA probe after confirming the new Agent version; no actuator commands in this phase.
- If a gate fails, do not create a command, restart any service, or alter existing automation.

This is a candidate only until independently approved; merging does not itself install the update at customer premises.
