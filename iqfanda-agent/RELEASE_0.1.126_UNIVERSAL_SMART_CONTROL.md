# TNG IQ FANDA Agent 0.1.126 — Universal Smart Control

This release promotes the multi-target PV-surplus runtime proven on 0.1.125
to the universal Smart Control baseline for all TNG IQ FANDA installations.

The runtime remains installation-independent: target IDs, sensor entities,
output entities and priorities are read from each device's cloud configuration.
No serial number or device UUID is hard-coded.

Universal Smart Control supports:

- multiple ordered energy targets;
- domestic-hot-water and generic temperature-controlled loads;
- independent target sensor/output binding;
- target temperature and hysteresis;
- per-target telemetry;
- shared battery-SOC gating;
- target fault isolation;
- simultaneous eligible target operation;
- the proven telemetry grace/fail-safe behavior.

F800711-TNG-00007 is the canary/reference installation only.

Cloud/client R3R38 provides the corresponding universal client editor for
target temperature, maximum temperature, hysteresis, SOC thresholds and
measured target temperature.

Existing installations receive the 0.1.126 runtime after add-on update.
Future installations should use 0.1.126 or later as the minimum universal
Smart Control Agent baseline.
