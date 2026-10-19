"""F47 R131 no-actuation timestamp probe tests."""
import unittest
from datetime import datetime,timedelta,timezone
from servo_ha_sensor_timestamp_probe import inspect,UUID,SERIAL,ENTITY

class TimestampTests(unittest.TestCase):
    def test_different_timestamps_visible(self):
        now=datetime.now(timezone.utc)
        def get(name):
            self.assertEqual(name,ENTITY)
            return {"state":"37.5",
                "last_changed":(now-timedelta(seconds=480)).isoformat(),
                "last_updated":(now-timedelta(seconds=480)).isoformat(),
                "last_reported":(now-timedelta(seconds=32)).isoformat()}
        r=inspect(identity={"device_uuid":UUID},
            payload={"serial":SERIAL,"mode":"ha_sensor_timestamps_read_only"},
            get_state=get)
        self.assertTrue(r["ok"])
        self.assertEqual(r["temperature_c"],37.5)
        self.assertTrue(r["reported_within_300_seconds"])
        self.assertFalse(r["zigbee_direct_read_attempted"])
        self.assertEqual(r["relay_commands_sent"],0)
        self.assertGreater(r["last_changed_age_seconds"],r["last_reported_age_seconds"])
    def test_old_report_stays_old(self):
        stamp=(datetime.now(timezone.utc)-timedelta(seconds=360)).isoformat()
        r=inspect(identity={"device_uuid":UUID},
            payload={"serial":SERIAL,"mode":"ha_sensor_timestamps_read_only"},
            get_state=lambda _:{"state":"36.0","last_changed":stamp,"last_reported":stamp})
        self.assertTrue(r["ok"])
        self.assertFalse(r["reported_within_300_seconds"])
        self.assertEqual(r["relay_commands_sent"],0)
    def test_wrong_model_never_reads(self):
        calls=[]
        r=inspect(identity={"device_uuid":"00012"},
            payload={"serial":SERIAL,"mode":"ha_sensor_timestamps_read_only"},
            get_state=lambda x:calls.append(x))
        self.assertEqual(r["reason"],"wrong_device")
        self.assertEqual(calls,[])
    def test_unexpected_payload_never_reads(self):
        calls=[]
        r=inspect(identity={"device_uuid":UUID},
            payload={"serial":SERIAL,"mode":"ha_sensor_timestamps_read_only","actuate":True},
            get_state=lambda x:calls.append(x))
        self.assertEqual(r["reason"],"invalid_payload")
        self.assertEqual(calls,[])
    def test_unknown_value_stops(self):
        r=inspect(identity={"device_uuid":UUID},
            payload={"serial":SERIAL,"mode":"ha_sensor_timestamps_read_only"},
            get_state=lambda _:{"state":"unavailable"})
        self.assertEqual(r["reason"],"ha_sensor_state_ValueError")
        self.assertEqual(r["relay_commands_sent"],0)

if __name__=="__main__":
    unittest.main()
