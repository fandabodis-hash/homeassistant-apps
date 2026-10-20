# TNG IQ FANDA Agent 0.1.164 – F47 R171, model 00013
## Release
- Local 5 s ON / 15 s OFF servo cadence for **exact** device UUID 014a0d23-d440-4131-abb4-28a860595043 (F800711-TNG-00013).
- Existing on-disk 3 s config is recognized without rewriting it; 0.1.164 runtime uses 5 s. Saved target (38 C currently) is not modified.
- 12 pulses maximum per unchanged OWON temperature report (60 s cumulative motor-ON budget); stop initiating movements on a report older than 180 s. These are upper bounds, not mandates to move.
- Relay ON watchdog now covers a complete 5-second pulse; both relays OFF commands and readback are checked.
- Retains 43 C sensor guard, floor-pump interlock, model UUID/SONOFF IEEE checks, sensor stale stop, direction-change hold, restart lockout and local-only operation without cloud timing.
- Never makes the installer issue servo ON or OFF commands. Previous VPS R127 servo timer remains disabled.
- Does not modify frontend, models 00012/00014, R87 pump control, R90, or R113 control.
## Rollout
Release may be published after CI passes, but physical installation is **not confirmed by CI**. Use the single-SSH R171 guard script, verify protected UI hash, version 00012, exact device UUID, both relays OFF, 38 C portal and local targets, running protected services, no conflicting pending servo commands, then enqueue agent_update only to model 00013. Confirm 0.1.164 and fresh heartbeat with 5 s ON / 15 s OFF and no lockout. A failed guard must leave all other devices unchanged. Do not bypass the guard.
## Safety limits
Zigbee software OFF is not an independent motor cutoff. Mechanically verified end stops, electrical mutual exclusion and an independent maximum-temperature thermostat are necessary for unattended physical operation. Mock tests do not prove actuator position. One OWON sample at most authorizes limited movement and stale sensor data blocks motion. Do not claim full production physical validation until observed.
