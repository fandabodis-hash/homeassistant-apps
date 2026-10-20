# F47 R171 – model 00013 only – candidate, NOT deployed

Base: Agent 0.1.163. The change is isolated to `rootfs/app/f47_local_mixer_00013.py` on a separate branch.

## Change
- Keep 5-second motor ON, minimum 15-second OFF.
- Expand per-OWON-sample cap from four to twelve pulses. This is a ceiling, **not** a guarantee of 20 pulses: temperature report age stops new pulses after 180 seconds. Every loop rechecks target, model UUID, hardware binding, pump and both relays OFF.
- Preserves watchdog OFF, immutable open/close mutually exclusive sequencing, fault lockout, target deadband and 43C high-temperature guard.
- Does **not** implement absolute position: actuator has no verified reference position. No claims of 0–100% position accuracy are permitted.
- This change does not remove the need for fresh OWON readings: old samples cannot authorize indefinite travel.

## Must pass before release
1. Read-only verification on actual 00013: pump ON, BOTH relays OFF and correct IEEE/endpoint bindings.
2. Confirm physically that OPEN relay drives the valve open, and CLOSE drives it closed; limit switches function.
3. Independently verify hardwired overtemperature cutoff and electrical mutual interlock; HA OFF is not a hardware failsafe.
4. Unit tests: 5s ON / >=15s OFF cadence, failures of OFF readback, stale sensor, pump OFF, wrong device, direction reversal and crash recovery.
5. Demonstrate local HA control with WAN disabled and validate target 38 C.
6. Canary rollout, heartbeat inspection, rollback if lockout or divergence. Do not update model 00012, portal/frontend or unrelated runtime.

Status: SOURCE CANDIDATE ONLY; not built, released or deployed. Existing 0.1.163 is unchanged in production.

R171 update: requested 5 seconds ON with 15 seconds OFF, max 12 pulses per OWON report (60 seconds total). Existing config 3s requires explicit guarded migration; no production update has been performed. **Do not deploy by merely changing source without migrating exact local config safely.**
