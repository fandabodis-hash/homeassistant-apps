# F47 R132 — automatic servo sensor-only HOLD; 00013 Agent 0.1.156

Root cause from customer's F47 R129: first automatic cycle failed with exception type SensorNotFresh; the servo ON/OFF commissioning succeeded earlier. R127 permanently locked out on every failed pulse, including pre-actuation sensor-only failures. This release fixes the distinction.

On a recurring ordinary pulse only: the Agent resolves the floor reading FIRST. If sensor fresh evidence cannot be obtained, it proves the servo registry and both relay entity states OFF, returns successful action hold_sensor_stale, and does not call the motor executor or any ON/OFF service. The regular scheduler can check again on its next cycle.

Commissioning must not be bypassed. Relay/state/off failure, device identity mismatch, missing interlock assumption and every other failure continue to fail and latch. No 300-second temperature freshness limit is extended; no direct Zigbee read failure is mistaken for a fresh reading.

The existing R127 VPS scheduler also needs an exact-model guarded, version-pinned replacement that recognizes ONLY the already-recorded pre-actuation SensorNotFresh failure and never unlocks any motor-command or OFF failure. That one-time scheduler migration is a separate guarded PC1 step after Agent release 0.1.156 is installed and checked. Preserve 00012, 00013 UI, R87/R90/R113 services, and the validated 120-second stroke configuration.

No physical relay command is required for the release verification. Automatic pulses will only run when an actual floor temperature value is fresh and the existing physical protections and ZHA model checks pass.