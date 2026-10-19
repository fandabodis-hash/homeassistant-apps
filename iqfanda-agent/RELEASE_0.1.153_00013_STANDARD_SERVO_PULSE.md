# F47 R126 — Agent 0.1.153, exact 00013 conventional servo pulse

The installer confirmed physical manual operation of the MINI-ZB2GS in both directions. This candidate uses ordinary Home Assistant switch turn_on -> local 1 s wait -> both turn_off. It never calls the unsupported ZHA cluster command 0x42 or 0xFC11 protocolData.

The module is strictly bound to the model UUID, SONOFF IEEE, exact two relay entity IDs, live ZHA OnOff endpoints, identical HA registry and current floor sensor / pump states. The local Agent (not VPS latency) owns the elapsed pulse time. Every attempt sends OFF to BOTH relays from a finally block and verifies HA states afterward, with a second watchdog thread attempting OFF on a delayed operation. Failure disables commissioning or automation. The watchdog is NOT a hardware cutoff and cannot guarantee OFF if the HA or Zigbee link is lost.

Installer-confirmed protections (motor electrical direction interlock/end switches and an independent pipe thermostat interrupting the pump) are NOT tested by software. No relay-level device-native auto-off is asserted.

Supported commands: zigbee_servo_standard_commission_v1 (one supervised pulse, stores an exact model local software commissioning marker only if both relays read OFF afterward); zigbee_servo_standard_pulse_v1 (requires that marker). The existing R120 / R113 and other heating services are not modified by this Agent source change. Deployment must be canary-only to 00013, with backup and a separate uniquely-owned VPS scheduler gate. Model 00012 is protected.

Do not enable unattended repetitive motion until the first supervised commission output is confirmed and the installer verifies physically correct direction and timely motor stop. No cloud timer shall own the one-second ON interval.