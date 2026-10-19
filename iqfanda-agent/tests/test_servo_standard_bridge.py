"""F47 R126 bridge isolation: no device I/O on wrong identity or uncommissioned step."""
import unittest
from unittest.mock import patch
from servo_timed_command_definition import UUID,IEEE,SERIAL
from servo_standard_bridge import execute,COMMISSION_COMMAND,RUN_COMMAND
from servo_standard_pulse import PulseStopped
from servo_floor_fresh import SensorNotFresh

PAYLOAD={
    "serial":SERIAL,"device_uuid":UUID,"ieee":IEEE,
    "device_reg_id":"reg","target_c":25.0,"seconds":1.0,
}

class BridgeTests(unittest.TestCase):
    def test_00012_refused_without_hardware(self):
        with self.assertRaises(PulseStopped):
            execute(identity={"device_uuid":"00012"},
                    command_type=COMMISSION_COMMAND,payload=PAYLOAD)
    def test_bad_payload_refused_without_hardware(self):
        with self.assertRaises(PulseStopped):
            execute(identity={"device_uuid":UUID},
                    command_type=COMMISSION_COMMAND,payload={**PAYLOAD,"force":True})
    def test_uncommissioned_auto_rejected(self):
        with patch("servo_standard_bridge.read_commission",return_value=False):
            with self.assertRaises(PulseStopped):
                execute(identity={"device_uuid":UUID},
                        command_type=RUN_COMMAND,payload=PAYLOAD)
    def test_commission_records_only_returned_off(self):
        out={"action":"ordinary_local_pulse","relays_after":"off_off",
             "direction":"close"}
        with patch("servo_standard_bridge.read_commission",return_value=False), \
             patch("servo_standard_bridge.floor_aware_state",return_value=lambda _:{"state":"35.0"}) , \
             patch("servo_standard_bridge.local_pulse",return_value=out) as pulse, \
             patch("servo_standard_bridge.write_commission") as marker:
            result=execute(identity={"device_uuid":UUID},
                           command_type=COMMISSION_COMMAND,payload=PAYLOAD)
            marker.assert_called_once()
            self.assertFalse(result["physical_direction_observed_by_installer"])
            args=pulse.call_args.kwargs
            self.assertTrue(args["physical_protections_confirmed"])
            self.assertEqual(args["payload"]["command_type"],RUN_COMMAND)
    def test_no_marker_without_off(self):
        with patch("servo_standard_bridge.read_commission",return_value=False), \
             patch("servo_standard_bridge.local_pulse",
                   return_value={"action":"ordinary_local_pulse","relays_after":"on_off"}), \
             patch("servo_standard_bridge.write_commission") as marker:
            with self.assertRaises(PulseStopped):
                execute(identity={"device_uuid":UUID},
                        command_type=COMMISSION_COMMAND,payload=PAYLOAD)
            marker.assert_not_called()

    def test_stale_sensor_skips_cycle_and_no_motor_call(self):
        def stale(_):
            raise SensorNotFresh("floor_zha_direct_read_HomeAssistantApiError")
        with patch("servo_standard_bridge.read_commission",return_value=True), \
             patch("servo_standard_bridge.floor_aware_state",return_value=stale), \
             patch("servo_standard_bridge._read_registry",return_value="reg"), \
             patch("servo_standard_bridge._read_entity",
                   return_value={"state":"off"}), \
             patch("servo_standard_bridge.local_pulse") as motor:
            result=execute(identity={"device_uuid":UUID},command_type=RUN_COMMAND,payload=PAYLOAD)
            self.assertEqual(result["action"],"hold_sensor_stale")
            self.assertFalse(result["actuation"])
            self.assertEqual(result["relays_after"],"off_off")
            motor.assert_not_called()
    def test_stale_sensor_but_output_on_still_locks(self):
        def stale(_):
            raise SensorNotFresh("floor_zha_direct_read_TimeoutError")
        with patch("servo_standard_bridge.read_commission",return_value=True), \
             patch("servo_standard_bridge.floor_aware_state",return_value=stale), \
             patch("servo_standard_bridge._read_registry",return_value="reg"), \
             patch("servo_standard_bridge._read_entity",
                   side_effect=[{"state":"off"},{"state":"on"}]), \
             patch("servo_standard_bridge.local_pulse") as motor:
            with self.assertRaisesRegex(PulseStopped,"stale_sensor_relay_off_unconfirmed"):
                execute(identity={"device_uuid":UUID},command_type=RUN_COMMAND,payload=PAYLOAD)
            motor.assert_not_called()
    def test_stale_sensor_cannot_skip_commissioning(self):
        def stale(_):
            raise SensorNotFresh("floor_sensor_stale")
        with patch("servo_standard_bridge.read_commission",return_value=False), \
             patch("servo_standard_bridge.floor_aware_state",return_value=stale), \
             patch("servo_standard_bridge.local_pulse") as motor:
            with self.assertRaises(SensorNotFresh):
                execute(identity={"device_uuid":UUID},
                        command_type=COMMISSION_COMMAND,payload=PAYLOAD)
            motor.assert_not_called()
    def test_stale_sensor_wrong_registry_maintains_lockout(self):
        def stale(_):
            raise SensorNotFresh("floor_sensor_stale")
        with patch("servo_standard_bridge.read_commission",return_value=True), \
             patch("servo_standard_bridge.floor_aware_state",return_value=stale), \
             patch("servo_standard_bridge._read_registry",return_value="other"), \
             patch("servo_standard_bridge.local_pulse") as motor:
            with self.assertRaisesRegex(PulseStopped,"stale_sensor_registry_unconfirmed"):
                execute(identity={"device_uuid":UUID},command_type=RUN_COMMAND,payload=PAYLOAD)
            motor.assert_not_called()
    def test_unrelated_failure_never_skipped(self):
        def bad(_):
            raise RuntimeError("HA backend problem")
        with patch("servo_standard_bridge.read_commission",return_value=True), \
             patch("servo_standard_bridge.floor_aware_state",return_value=bad), \
             patch("servo_standard_bridge.local_pulse") as motor:
            with self.assertRaises(RuntimeError):
                execute(identity={"device_uuid":UUID},command_type=RUN_COMMAND,payload=PAYLOAD)
            motor.assert_not_called()
    
if __name__=="__main__":
    unittest.main()
