"""F47 R111: command entrypoint guard regression tests (no real HA/Zigbee)."""
import unittest
from servo_pulse_command import execute, no_attestation, FLOOR_SENSOR
from servo_pulse_guard import DEVICE_UUID, IEEE, SERIAL, OPEN, CLOSE, UnsafeServo, LocalSafetyEvidence

REG = "servo-mini-zb2gs-00013"
IDENTITY = {"device_uuid": DEVICE_UUID}
PAYLOAD = {"command_type": "zigbee_servo_pulse_v1", "serial": SERIAL,
           "device_uuid": DEVICE_UUID, "ieee": IEEE,
           "device_reg_id": REG, "target_c": 25.0, "seconds": 1.0}

def state(entity):
    if entity in (OPEN, CLOSE): return {"state": "off"}
    if entity == FLOOR_SENSOR: return {"state": "34.9"}
    raise AssertionError("unexpected entity")

def get_id(entity):
    if entity in (OPEN, CLOSE): return REG
    raise AssertionError("wrong entity")

def evidence(_):
    return LocalSafetyEvidence(True, True, IEEE, 1.5)


class CommandEntryTests(unittest.TestCase):
    def arguments(self, **overrides):
        calls = []
        p = dict(PAYLOAD)
        p.update(overrides.pop("payload_update", {}))
        return dict(identity=overrides.pop("identity", IDENTITY),
                    payload=p,
                    get_entity_state=overrides.pop("get_entity_state", state),
                    get_entity_device_id=overrides.pop("get_entity_device_id", get_id),
                    freshness_seconds=overrides.pop("freshness_seconds", lambda _: 1.0),
                    set_output=lambda eid, on: calls.append((eid, on)),
                    device_safety_attestor=overrides.pop("device_safety_attestor", no_attestation),
                    arm_device_auto_off=overrides.pop("arm_device_auto_off", None),
                    wait=lambda _: None), calls

    def rejected(self, **overrides):
        args, calls = self.arguments(**overrides)
        with self.assertRaises(UnsafeServo):
            execute(**args)
        self.assertEqual(calls, [])

    def test_no_default_local_adapter(self): self.rejected()
    def test_no_hardware_attestation(self):
        self.rejected(arm_device_auto_off=lambda *_: True)
    def test_cloud_cannot_claim_attestation(self):
        self.rejected(payload_update={"device_auto_off_confirmed": True,
                                      "mutual_exclusion_confirmed": True},
                      arm_device_auto_off=lambda *_: True)
    def test_wrong_device(self):
        self.rejected(identity={"device_uuid": "F800711-TNG-00012"},
                      arm_device_auto_off=lambda *_:True)
    def test_wrong_uuid(self):
        self.rejected(payload_update={"device_uuid": "OTHER"},
                      arm_device_auto_off=lambda *_:True)
    def test_wrong_ieee(self):
        self.rejected(payload_update={"ieee": "a:b"},
                      arm_device_auto_off=lambda *_:True)
    def test_wrong_command_type(self):
        self.rejected(payload_update={"command_type": "zigbee_switch_set"},
                      arm_device_auto_off=lambda *_:True)
    def test_wrong_registry(self):
        self.rejected(payload_update={"device_reg_id": "other"},
                      arm_device_auto_off=lambda *_:True)
    def test_wrong_duration(self):
        self.rejected(payload_update={"seconds": 120},
                      arm_device_auto_off=lambda *_:True)
    def test_different_relay_devices(self):
        self.rejected(get_entity_device_id=lambda e:"a" if e == OPEN else "b",
                      arm_device_auto_off=lambda *_:True)
    def test_stale_temperature(self):
        self.rejected(freshness_seconds=lambda _: 60,
                      arm_device_auto_off=lambda *_:True)
    def test_unknown_state(self):
        self.rejected(get_entity_state=lambda _: {"state":"unknown"},
                      arm_device_auto_off=lambda *_:True)
    def test_within_tolerance_does_not_pulse(self):
        args,calls = self.arguments(
            get_entity_state=lambda e:{"state":"25.0"} if e==FLOOR_SENSOR else {"state":"off"},
            arm_device_auto_off=lambda *_:True)
        self.assertEqual(execute(**args)["action"],"hold")
        self.assertEqual(calls,[])
    def test_unarmed_cannot_pulse(self):
        self.rejected(device_safety_attestor=evidence,
                      arm_device_auto_off=lambda *_:False)
    def test_trusted_test_adapter_only(self):
        args,calls=self.arguments(device_safety_attestor=evidence,
                                  arm_device_auto_off=lambda *_:True)
        result=execute(**args)
        self.assertEqual(result["direction"],"close")
        self.assertEqual(calls,[(CLOSE,True),(OPEN,False),(CLOSE,False)])


if __name__ == "__main__":unittest.main()
