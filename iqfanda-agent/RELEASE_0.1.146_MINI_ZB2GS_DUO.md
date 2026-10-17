# Release candidate 0.1.146 — universal SONOFF MINI-ZB2GS DUO

This is a **draft** release candidate, not a published Home Assistant add-on version.

- Add explicit read-only `relay_channels` metadata for a paired MINI-ZB2GS with exactly two verified `binary_power_output` entities.
- Preserve the original Zigbee entity inventory, IEEE and existing command behavior.
- Never infer physical wiring, or emit a guessed channel mapping when capability inventory is ambiguous.
- The cloud admin universal dual-channel UI is released **separately** from the add-on (F47 R18C); bumping the add-on alone does not install the UI.

Before stable release: run isolated unit tests, install on a canary Fanda, verify both channels in HA telemetry and cloud admin, then promote with rollback prepared. Protected models 00012/00013 must remain unchanged outside the planned telemetry addition.
