# Local manual battery control: SolaX and Deye

Implementation review: 2026-11-01. Offline tests exercise software with a fake
Modbus peer; they do not constitute verification on a physical inverter.

## Support represented by the profiles

| Profile | Manual charge/discharge/AUTO | Reason or verification scope |
| --- | --- | --- |
| `solax_x3_hybrid_g4` | Implemented, hardware canary pending | Acknowledged FC16 VPP block plus FC04 remote-mode readback; actual battery response must be checked separately. |
| `deye_3x_lv_off_grid` | Unavailable | The manufacturer states that remote mode does not operate off-grid. The installed profile has two AC buses and a shared DC battery. |
| `deye_50_hybrid_hv` | Unavailable pending an exact protocol selection | Manufacturer documents disagree on the meaning of register 1100. The supplied HV-specific map does not specify the remote-control block. |

The Deye profiles explicitly have no supported control actions and no control
driver. Their existing read-only telemetry remains separate from manual control.
Their public reasons are `off_grid_fixed_battery_power_unavailable` and
`deye_hv_control_protocol_unverified`. Neither profile is marked control-verified.

## SolaX X3-Hybrid G4

The manufacturer's [VPP definition](https://kb.solaxpower.com/it/solution/detail/40289fdf9279482a01927a1743c40230)
specifies mode 4 for direct battery power. It is distinct from mode 1, which
sets inverter AC power and therefore does not imply the same battery power
when PV is generating.

The driver checks a physical X3-Hybrid G4 identity, slave 1, and the documented
firmware family (ARM 1.40 or newer and DSP 1.41 or newer within major version 1).
It sends the complete FC16 block 0x007C–0x008A. Battery power is signed 32-bit,
low word first: negative charges and positive discharges. Unused command
fields are zero. It never writes schedules, current settings, reserve SOC or
other work-mode configuration in EEPROM.

FC03 at these numeric addresses is a different register space. For example,
FC03 0x007D is firmware information, not the readback of the VPP command.
The driver verifies FC04 0x0100 for the selected remote mode and checks the
write acknowledgement. `verified` means that specific check passed. It does
not assert the physical battery already reached the commanded watts. The
separate `observed_battery_power_w` uses the common sign convention, positive
for discharge, and is explicitly a **before-command** observation.

Fresh local measurements are required on every active tick: BMS connection,
on-grid state, SOC, inverter/BMS faults, battery voltage and direction-specific
BMS current limits. Applied watts cannot exceed the requested power, the
fresh BMS voltage-times-current limit or the local 15 kW ceiling. Discharge
also respects the original work mode's reserve and the inverter's live SOC
limits, with a two-percentage-point stop margin. Reaching a SOC bound or a
BMS safety condition raises `BatteryControlSafetyStop`; the local session
supervisor terminates the request and restores AUTO.

A durable owner marker is written before unlocking or sending any command.
It binds the session to serial number, transport and device ID and records
the original work mode and parameter lock. AUTO disables the remote overlay,
verifies the original work mode, restores the original parameter-lock state
and only then removes the marker. It does not take over an unowned remote
session. A failed command attempts AUTO immediately; a failed restoration
keeps ownership for the local supervisor's recovery.

**There is no verified unconditional mode-4 hardware timeout in this release.**
The manufacturer describes `RemoteCtrlTimeOut` as starting after a target is
completed. It must not be represented as a communication-loss watchdog for
mode 4. The local session supervisor provides deadlines, periodic checks and
restart recovery. Host power loss and prolonged physical bus failure can
prevent the host from issuing AUTO; inverter/BMS protections continue to
apply, but an independent watchdog fallback has not been demonstrated.

The vendor's minimum one-second interval between commands is observed.
Consequently a full safety/identity refresh and command can take more than
ten seconds. The caller must serialize requests and account for execution
time instead of launching overlapping workers.

Additional manufacturer-authored reference:
[Hybrid X1/X3 G4 Modbus TCP/RTU V3.21](https://www.gbc-solino.cz/files/uploads/FAQ%20SolaX/Hybrid-X1X3-G4-ModbusTCPRTU-V3.21-English_0622-public-version.pdf).
The FC03 identity/settings and FC04 battery, fault and VPP readback tables
are used. Newer mode-4 command definitions come from the official VPP article.

## Deye: physical and protocol limitations

### Three LV inverters in the existing off-grid profile

The manufacturer's [EMS control description](https://github.com/user-attachments/files/33301777/ems.control.English.version.docx)
states that remote mode does not work off-grid and the battery follows the
power required by the loads. The file was supplied with the manufacturer's
other protocol documents in [this source discussion](https://github.com/davidrapan/ha-solarman/pull/978#issuecomment-6108428451).
The manufacturer-authored attachment, not forum guesses, establishes this
limitation.

The existing installation profile explicitly declares `grid_connected: false`,
one parallel master/slave AC bus, a second independent AC bus and a shared
DC battery, whose telemetry comes from an external Pylontech module. Its
battery power is constrained by the balance of loads, PV, generator power
and conversion losses. It cannot independently follow arbitrary fixed
charge/discharge watts while also maintaining both islanded AC supplies
without coordinating an additional controllable source or sink.

Therefore `charge_grid`/`discharge_grid` cannot be implemented by copying a
grid-connected remote register sequence. A generator-charge request, PV
curtailment or controllable-load request would be a separate control
function with its own source/sink limits and coordinated topology contract.
It is not implemented here, and no remote writer has been added to this
off-grid profile.

### 50 kW high-voltage hybrid

The manufacturer-authored [three-phase V105.4 protocol](https://github.com/user-attachments/files/22990113/MODBUS.RTU.V105.4-20240814.pdf),
pages 41–42, documents remote registers 1100–1121. It says 1100=0 disables
and 1100=1 enables remote mode. The manufacturer-authored
[RS485 demonstration](https://github.com/user-attachments/files/33301774/RS-485-Remote.mode.1.docx)
also shows that convention. In contrast, the EMS description linked above
says 1100=0 enters and 1100=1 exits. It confirms 1109 is signed battery power,
negative charging and positive discharging, in 0.1% of rated power, and that
1104=1 selects battery-side control. The conflicting enable/disable values
cannot be resolved by reading a zero or one from the device.

The available [HV-specific V104.3.1 SG01-HP3-AM2 protocol](https://github.com/user-attachments/files/16597916/MODBUSRTU.V104.3.1.1111_SG01-HP3-AM2.pdf)
does not define that remote block. A working LV firmware example cannot be
used to infer the correct contract for the installed 50 kW HV device.

The current [ha-solarman Deye three-phase implementation](https://github.com/davidrapan/ha-solarman/blob/main/custom_components/solarman/inverter_definitions/deye_p3.yaml)
does not expose this remote block. Its unmerged [PR 978](https://github.com/davidrapan/ha-solarman/pull/978)
is not a reliable substitute: for example, it labels 1110 reactive control
where the manufacturer's map specifies target SOC, and also shifts the
1106–1108 meanings. [SolarAssistant's own HV control documentation](https://solar-assistant.io/help/inverters/deye/SG01HP3/power-management)
describes work-mode timer settings, not a protocol that resolves this
remote-control ambiguity.

Enabling HV control requires an exact model/firmware-specific manufacturer
contract for the installed inverter, an unambiguous runtime selector for
that contract, and a supervised low-power check of command direction,
readback, local limits, original-setting restoration and watchdog fallback.
The current profile has no such verified selector, so this change does not
guess one or expose writable support.

## Offline verification

Run from the repository root:

```sh
python -m unittest discover -s iqfanda-agent/tests -p test_solax_battery_control.py -v
python -m unittest discover -s iqfanda-agent/tests -p test_deye_control_support.py -v
```

The SolaX tests cover real command encoding, distinct FC03/FC04 spaces,
live BMS power limiting, SOC bounds, ownership, restoring the original
parameter lock, failed acknowledgements/readback, and recovery. Deye tests
verify that both profiles remain explicit unsupported capabilities and
cannot import or invoke a write driver for any manual action.
