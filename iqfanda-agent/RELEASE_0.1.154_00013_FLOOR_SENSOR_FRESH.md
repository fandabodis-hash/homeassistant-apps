# F47 R127 — 00013 fresh OWON floor sensor without weakening safety

The latest live R126 attempt was blocked at `floor_sensor_stale` before any relay command. Cloud Zigbee telemetry was fresh, but that alone cannot validate the last individual floor sensor measurement.

New Agent 0.1.154 reads the exact floor sensor HA entity. If the local HA last_reported timestamp is older than 300 seconds, the Agent locates its same-device ZHA identity and reads ZCL Temperature Measurement 0x0402 attribute 0x0000 with allow_cache=False via ZHA's read-only WebSocket command. Returned integer centidegrees are checked for finite and plausible 0–43 °C. On network timeout or unknown identity/cluster, servo remains blocked.

New cloud command: `zigbee_servo_floor_refresh_v1` with payload `{ "serial": "F800711-TNG-00013", "mode": "read_only" }` returns measured source and age without switching relays.

The existing `zigbee_servo_preflight_v1` and ordinary switch pulse receive exactly the same freshly sampled value if a real direct Zigbee read succeeds; no extension of the 300-second deadline. HA states are not modified; the fresh timestamp is constructed inside the local call only after uncached ZHA confirmation.

No modifications to model 00012, R87 pumps, R90 position, R113 shadow, existing frontend or unrelated devices. Must be deployed only to exact UUID 00013 with backup. No motor command in this release process. Physical commissioning and single-owner scheduler remain a separate supervised phase.