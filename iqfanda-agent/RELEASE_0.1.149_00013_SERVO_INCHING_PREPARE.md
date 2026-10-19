# F47 R117R1 / Agent 0.1.149 — corrected SONOFF protocolData setup

This replaces 0.1.148. **Do NOT deploy 0.1.148**: its 0xFC11 command ID 0 is not the vendor command.

Correct `protocolData` manufacturer-specific command on cluster `0xFC11` has ID `0x01`, manufacturer code `0x1286`, as published by zigbee-herdsman-converters. The frame enables a 1-second local OFF timer for the selected endpoint, one request per channel, with no relay ON.

The customer's ZHA quirk may not expose command 0x01; failure must be reported as unsupported, not bypassed. ZHA command returned is not physical attestation: no motor movement until proven device-local OFF, independent electrical interlock, and independent underfloor-temperature safety protection. Exact model 00013 only; no changes to protected 00012 and existing heating.
