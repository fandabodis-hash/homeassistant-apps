import unittest
from servo_pulse_guard import (
    CLOSE, OPEN, DEVICE_UUID, IEEE, SERIAL, Snapshot, LocalSafetyEvidence,
    UnsafeServo, decide, validate_pulse, run_pulse,
)


def snap(*, floor=34.9, target=25.0, op="off", cl="off", age=1, serial=SERIAL, uuid=DEVICE_UUID):
    return Snapshot(serial, uuid, IEEE, op, cl, floor, target, age)


def proof(**kwargs):
    data = dict(device_auto_off_confirmed=True, mutual_exclusion_confirmed=True,
                expected_ieee=IEEE, maximum_device_on_seconds=1.5)
    data.update(kwargs)
    return LocalSafetyEvidence(**data)


class GuardTests(unittest.TestCase):
    def test_hot_close(self): self.assertEqual(decide(snap()), "close")
    def test_cold_open(self): self.assertEqual(decide(snap(floor=24)), "open")
    def test_target_hold(self): self.assertEqual(decide(snap(floor=25)), "hold")
    def test_upper_band(self): self.assertEqual(decide(snap(floor=25.2)), "hold")
    def test_lower_band(self): self.assertEqual(decide(snap(floor=24.8)), "hold")
    def test_hot_limit(self): self.assertEqual(decide(snap(floor=46)), "close")
    def test_stale(self):
        with self.assertRaises(UnsafeServo): decide(snap(age=60))
    def test_bad_identity(self):
        with self.assertRaises(UnsafeServo): decide(snap(serial="F800711-TNG-00012"))
    def test_bad_uuid(self):
        with self.assertRaises(UnsafeServo): decide(snap(uuid="other"))
    def test_unverified_auto_off(self):
        with self.assertRaises(UnsafeServo): validate_pulse(snap(), proof(device_auto_off_confirmed=False), "close", 1)
    def test_unverified_interlock(self):
        with self.assertRaises(UnsafeServo): validate_pulse(snap(), proof(mutual_exclusion_confirmed=False), "close", 1)
    def test_wrong_direction(self):
        with self.assertRaises(UnsafeServo): validate_pulse(snap(), proof(), "open", 1)
    def test_long_pulse(self):
        with self.assertRaises(UnsafeServo): validate_pulse(snap(), proof(), "close", 3)
    def test_bad_states(self):
        with self.assertRaises(UnsafeServo): validate_pulse(snap(op="on"), proof(), "close", 1)
    def test_unknown_hardware(self):
        with self.assertRaises(UnsafeServo): validate_pulse(snap(), proof(maximum_device_on_seconds=3), "close", 1)
    def test_no_on_when_arm_fails(self):
        calls=[]
        with self.assertRaises(UnsafeServo):
            run_pulse(snapshot=lambda:snap(),local_safety_probe=proof,
                      arm_device_auto_off=lambda *_:False,
                      set_output=lambda *args:calls.append(args),wait=lambda _:None)
        self.assertEqual(calls, [])
    def test_verified_pulse(self):
        calls=[]
        def curr():
            states=dict(calls)
            return snap(op="on" if states.get(OPEN) else "off",
                        cl="on" if states.get(CLOSE) else "off")
        r=run_pulse(snapshot=curr,local_safety_probe=proof,
                    arm_device_auto_off=lambda *_:True,
                    set_output=lambda *args:calls.append(args),wait=lambda _:None)
        self.assertEqual(r["direction"], "close")
        self.assertEqual(calls,[(CLOSE,True),(OPEN,False),(CLOSE,False)])
    def test_exception_finally_off(self):
        calls=[]
        with self.assertRaises(ValueError):
            run_pulse(snapshot=lambda:snap(),local_safety_probe=proof,
                      arm_device_auto_off=lambda *_:True,
                      set_output=lambda *args:calls.append(args),
                      wait=lambda _: (_ for _ in ()).throw(ValueError("pulse interrupted")))
        self.assertEqual(calls[-2:],[(OPEN,False),(CLOSE,False)])
    def test_off_readback_not_confirmed(self):
        count=[0]
        def snapshot():
            count[0] += 1
            return snap(cl="on") if count[0] == 3 else snap()
        with self.assertRaisesRegex(UnsafeServo, "off_readback_unconfirmed"):
            run_pulse(snapshot=snapshot, local_safety_probe=proof,
                      arm_device_auto_off=lambda *_:True,
                      set_output=lambda *args:None, wait=lambda _:None)
    def test_no_on_if_hold(self):
        calls=[]
        r=run_pulse(snapshot=lambda:snap(floor=25),local_safety_probe=proof,
                    arm_device_auto_off=lambda *_:calls.append("arm") or True,
                    set_output=lambda *args:calls.append(args),wait=lambda _:None)
        self.assertEqual(r["action"], "hold")
        self.assertEqual(calls, [])


if __name__ == "__main__": unittest.main()
