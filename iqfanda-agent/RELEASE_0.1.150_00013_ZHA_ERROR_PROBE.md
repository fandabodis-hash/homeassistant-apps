# F47 R118 – 00013 diagnostic-only Agent 0.1.150

The 00013 Agent 0.1.149 reported ZHA HTTP 500 when it attempted vendor-specific SONOFF MINI-ZB2GS `0xFC11` command 0x01. **This does not establish that inching is configured.** The previous R113 shadow regulator stays active, both servo relays OFF, no servo automatic motion.

This candidate adds cloud command `zigbee_servo_zha_error_probe_v1` with exact payload:
`{"serial":"F800711-TNG-00013","mode":"read_only_zha_failure"}`.

It uses the existing local HA Agent Supervisor credentials to query ZHA service field names and optionally Home Assistant's `/api/error_log`. It returns only bounded error-category signatures and exception-class names; **no complete log lines or sensitive tokens** are sent to the cloud.

If HA blocks the error log, return an explicit diagnostic status rather than attempting to write to Zigbee. No physical commands, no Zigbee writes, no core restarts, no model 00012 changes.

No device-local timeout or electrical interlock is attested. Do not activate the servo. A targeted add-on update to 0.1.150 with backup is required solely for collecting failure cause without a customer HA login.
