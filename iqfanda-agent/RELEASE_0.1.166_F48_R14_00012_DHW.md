# F48 R14 — 00012 Agent v0.1.166, all three TUV OFF readbacks

* Fixed scope: device_uuid=28fb44c3-4f8d-4ea4-90ab-f7e6a3b509fc and only bivalence switch plus both pinned heat-pump TUV light entities, exact Zigbee IEEE.
* Distinctly supports OFF on HP1 and HP2 when weather falls to/below bivalence point, and bivalence OFF when weather rises above the point.
* Only after a real actuator OFF attempt and exact 15s Home Assistant TimeoutError('timed out'); at most two HA GET state checks must confirm a newly changed ON->OFF state within 50s.
* Without fresh proof: FAIL remains latched. Never sends duplicate physical ON/OFF, never automatically rearms `command_failed`, does not write server DB, does not modify model 00013.
* The +8 C switching point and original break-before-make cloud sequence remain unchanged.
* New 37 directed tests plus existing 24 checks and servo/Zigbee gates. GitHub offline tests do **not** establish physical reliability. Root cause of 18:21 incident must be confirmed by scoped 00012 cloud command audit before OTA.
* This is Agent 0.1.166, derived from existing 0.1.165. No backend restart allowed for 00012 because backend is shared with model 00013 and its fireplace system.
