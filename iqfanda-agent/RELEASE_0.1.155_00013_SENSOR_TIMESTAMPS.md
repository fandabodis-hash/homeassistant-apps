# F47 R131 — source of HA sensor freshness mismatch, model 00013 only

The user confirmed OWON floor temperature visibly changes as accumulator temperature rises. A stale last_reported timestamp in the servo automation does not establish a broken sensor.

This 0.1.155 Agent candidate adds a read-only, exact-model cloud command zigbee_servo_sensor_timestamps_v1. It returns HA state temperature plus last_reported, last_updated and last_changed ages separately. No Zigbee write, relay ON/OFF, bypass or production mixing change.

The existing direct-read path currently raises HomeAssistantApiError. The error classifier now reports only safe categories such as WebSocket unknown command, authorization denied, unavailable device, Zigbee timeout and unsupported attribute, without raw user content or credentials.

The 300 s freshness guard and existing lockout remain unchanged. The immediate goal is to distinguish periodic sensor state updates from an unsupported HA direct ZHA API call. Model 00012, pumps, R113, and servo bindings must remain untouched.