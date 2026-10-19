"""F47 R118 no-actuation local HA error logging contract tests."""
import unittest
from servo_zha_error_probe import inspect,summarize,ProbeError,SERIAL,UUID,IEEE

def devices():
    return [{"ieee":IEEE,"model":"MINI-ZB2GS"}]

def services():
    return [{"domain":"zha","services":{
        "issue_zigbee_cluster_command":{"fields":{"ieee":{},"params":{},"command":{}}}}}]

class ProbeTests(unittest.TestCase):
    def args(self):
        calls=[]
        def get_log():
            calls.append("GET")
            return ("Traceback:\\n  at homeassistant/components/zha command \\n"
                    "ValueError: command not found for protocolData cluster 0xfc11")
        return dict(identity={"device_uuid":UUID},
                    payload={"serial":SERIAL,"mode":"read_only_zha_failure"},
                    read_zha_devices=devices,read_ha_services=services,
                    read_error_log=get_log),calls

    def test_result_contains_no_raw_log(self):
        x,calls=self.args()
        result=inspect(**x)
        self.assertEqual(calls,["GET"])
        self.assertEqual(result["status"],"READ_ONLY_LOG_SIGNATURES_COLLECTED")
        self.assertFalse(result["actuation_allowed"])
        self.assertEqual(result["relay_commands_sent"],0)
        self.assertEqual(result["zha_service_field_names"],["command","ieee","params"])
        self.assertNotIn("Traceback",str(result))
        self.assertNotIn("secret",str(result))
    def test_wrong_model_never_reads(self):
        x,calls=self.args()
        x["identity"]={"device_uuid":"different"}
        with self.assertRaises(ProbeError):inspect(**x)
        self.assertEqual(calls,[])
    def test_invalid_payload_never_reads(self):
        x,calls=self.args()
        x["payload"]={"serial":SERIAL,"mode":"read_only_zha_failure","force":True}
        with self.assertRaises(ProbeError):inspect(**x)
        self.assertEqual(calls,[])
    def test_wrong_ieee_never_reads(self):
        x,calls=self.args()
        x["read_zha_devices"]=lambda:[{"ieee":"other","model":"MINI-ZB2GS"}]
        with self.assertRaises(ProbeError):inspect(**x)
        self.assertEqual(calls,[])
    def test_missing_service_no_reads(self):
        x,calls=self.args()
        x["read_ha_services"]=lambda:[{"domain":"zha","services":{}}]
        with self.assertRaises(ProbeError):inspect(**x)
        self.assertEqual(calls,[])
    def test_unavailable_log_is_no_action(self):
        x,calls=self.args()
        def fail():
            raise TimeoutError("internal private path/token")
        x["read_error_log"]=fail
        result=inspect(**x)
        self.assertFalse(result["log_signature"]["error_log_available"])
        self.assertNotIn("private",str(result))
        self.assertFalse(result["actuation_allowed"])
    def test_raw_secrets_discarded(self):
        output=summarize("some.private@example.com TOKEN=abc123\\nprotocolData unknown command")
        self.assertNotIn("abc123",str(output))
        self.assertNotIn("private@",str(output))
        self.assertEqual(output["raw_lines_returned"],0)

if __name__=="__main__":unittest.main()
