# F58 FoxESS-only 0.1.175 – 00007 from physically verified Vaculík 00018
Date 2026-11-07. Status: **unreleased canary / NOT installed / FIELD PASS PENDING**.

## Release scope – do not merge F59, 0.1.174 or unrelated code
This branch starts from exact Agent 0.1.171 source baseline:
`34c1441f44542f442623a4c595efebf6f898813e`.

The FoxESS H3 control driver is copied **byte-for-byte** from the source branch representing the 00018 Vaculík physically working Agent 0.1.168:
`baseline/f59-00018-agent-0-1-168-source-20261107`,
Git blob `680f9c89221dc9fc20d0408bf28c8637a367c7c5`.
It is ALSO byte-identical with the FoxESS driver in the 00007 0.1.169 release whose physical pin is preserved.

The only existing runtime files allowed to change are:
- `communication/foxess_ems_controller.py`: exact working 00018 driver, remove unverified F57 60-second remote setpoint suppression introduced in 0.1.171. The verified driver reasserts the requested power for local lease renewal.
- `manual_battery_runtime.py`: FoxESS-specific repeated AUTO/STOP latch fix, byte-exact append-only backup of the old physically verified pin before any FoxESS canary power request and saving new FoxESS profile control proof only after measured 500 W bidirectional + final AUTO PASS. Non-FoxESS driver paths and semantics stay unchanged.
- `communication/battery_feature_guard.py`: new **FoxESS-only** physical control fingerprint after an actual successful canary, which permits future VERSION-only changes but still detects changed FoxESS/owner sources. Pre-0.1.175 pins and all other inverter profiles continue to use strict version-bound comparison.
- `communication/foxess_175_00007_commissioning.py`: pure FoxESS 00007 identity, exact original pin hash and explicit technician acceptance policy. `command_worker.py` only calls this specialized validation from `manual_battery_canary_v1`; the universal dispatcher contains no hardcoded serial or UUID, and all other commands are unchanged.

Other changed paths permitted: Agent VERSION/config.yaml (0.1.175), release SHA manifest and append-only release history, unit tests, release CI and this documentation. NO unrelated source changes (thermostats, Zigbee, heat pumps, 00012/00013, GoodWe, SolaX, DEYE, generator, UI, cloud, frontend). This is enforced by an explicit Git diff whitelist against the original 0.1.171 SHA.

## Confirmed physical issue 00007
- 00007 is on 0.1.171; old FoxESS physical pin is `physically_verified` for 0.1.169, runtime fingerprint `4baabda610229bb7b0ac55f0990ef464b3db3deb86e7fed98fc3473929530f9c`, evidence id `82f9bc6f-0ea9-4dd2-ac58-0526bf5a739b`.
- Exact old PIN registry file SHA256 confirmed locally: `7717125241c7b4903f9f7f183d3929e49481a4b2ead77e3f5ae33e07fc7a6cdc`.
- Local FoxESS Modbus readback previously reported 44000=0, 44002=0 W, 41000=0 (Self Use), marker missing, SOC 41%; cloud manual status idle. Therefore no stuck remote command had been established.
- The guard returns `GUARD_SOURCE=PASS` but `GUARD_FOXESS=BLOCKED` because full 0.1.171 runtime fingerprint differs from the old physically verified 0.1.169. Do not manually overwrite/delete old PIN.
- Home Assistant sensor-name search returned `GRID_SENSOR_CANDIDATES = 0`. The user confirms that the **existing FoxESS smartmeter is physically connected**. The previous F59 0.1.174 extra HA sensor/remote-import dependency was an unnecessary new development path and **is not included** in 0.1.175.

## Commissioning strictly scoped to 00007
1. Snapshot and preserve existing 00007 local add-on data, current Docker image, physical PIN and config before any update. Do not overwrite past evidence. Do not disturb 00018.
2. Publish new candidate only via a **per-device isolated installation** without making the branch's new version available to all FANDAs. The repository default branch must not be changed as a shortcut.
3. After local update, `verify_battery_feature()` passes only if actual 0.1.175 runtime source SHA matches the release manifest. The still-old 0.1.169 pin **must initially block** fresh manual START by design.
4. Technician checks FoxESS Smartmeter/CT actual import/export sign, BMS limits and zero-export settings via existing hardware; no fake HA sensor and **no disabling export prohibition**. Confirm real physical safety/consumption, including sufficient load when intentionally testing discharge. The canary itself remains bounded at 500 W per direction and always returns to AUTO.
5. One explicitly approved `manual_battery_canary_v1`: `commissioning=true`, `profile_id=foxess_h3_10_0_e`, `power_w=500`, `expected_agent_version=0.1.175`, `expected_device_uuid=1c25a8d4-b986-43ee-b2fc-eaa152e9bdf8`, `explicit_technician_approval=true`, `expected_runtime_fingerprint=<exact_manifest_hash>`, `expected_previous_pin_sha256=7717125241c7b4903f9f7f183d3929e49481a4b2ead77e3f5ae33e07fc7a6cdc`. Any mismatch stops before hardware. No unattended canary, no automatic release or manual START before canary.
6. Before physical I/O the old 0.1.169 pin is copied byte-identically with `O_EXCL` to `/config/manual_battery_acceptance/prior_pins/pin-registry-<old_SHA>.json`. The original stays intact during testing. A failed test does not overwrite it.
7. Only if battery discharge, charge, independent repeated readback and final AUTO/STOP all physically pass is a new `physically_verified` FoxESS pin persisted with both historical full runtime fingerprint and a new **FoxESS-only control fingerprint**. Subsequent mere VERSION updates need not invalidate this profile, but any protected FoxESS control-source edit must.
8. Finally test actual UI manual charge 500 W -> physical response -> STOP/AUTO and discharge 500 W with verified absence of forbidden export -> STOP/AUTO, plus power-off/restart safety. Test new 00007 version against current operator-approved client UI; preserve old 00018 0.1.168, 00003 GoodWe, 00012/00013, 00014, 00011, and all other models.

## Release decision
- `SOURCE PASS` and ARM64 compile/build of 0.1.175 candidate must be evidenced by GitHub Actions.
- `00007 FIELD PASS` is not present until physically verified.
- Do not publish 0.1.175 to the global add-on repository, do not perform automatic upgrades, do not claim 00007 is fixed until charging and discharging have both been physically verified with proper stop.
- No cloud/backend/frontend deployment or DB mutation is necessary for this standalone FoxESS driver fix.

## Legacy F57 test compatibility
The old F57 CI expected an **experimental 60s power throttle** and therefore cannot require that text from the proven Vaculík driver. The `f57-foxess-renewal-candidate.yml` test still enforces all original checks on all non-0.1.175 versions. Only when VERSION is exactly 0.1.175 does it require byte-for-byte Git blob `680f9c89221dc9fc20d0408bf28c8637a367c7c5`, unchanged AUTO and no 60s suppression. This changes **CI test expectations**, never other hardware or software functions. A separate `f58-foxess-only-175.yml` ensures all other production source files are unchanged from the main 0.1.171 baseline.
