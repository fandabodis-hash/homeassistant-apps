# F47 R122 Agent 0.1.152 exact-model read-only servo preflight

The preceding R120 commissioning failed with SafetyStop but the worker discarded the concrete reason.
This update adds a new command `zigbee_servo_preflight_v1` with payload `{ "serial": "F800711-TNG-00013", "target_c": 25.0 }`.

The command queries local HA states/registry and ZHA signatures and sends back only safe machine-readable failure codes. No relay service and no ZHA cluster command is called.
The existing commissioned-pulse failure handler also reports sanitized SafetyStop codes for future runs.

No second physical commissioning attempt is part of R122; its preflight cannot authorize servo movement.
Models 00012 and unrelated installations are unchanged. R87 pumps, R90 position, and R113 shadow retain their owners. Installer-confirmed hardware protections remain intact.