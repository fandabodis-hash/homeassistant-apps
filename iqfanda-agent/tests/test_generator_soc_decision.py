"""Offline testy bez Zigbee, Modbus a zmeny fyzickeho stavu."""
import importlib.util
import math
from pathlib import Path
import sys
import unittest

spec=importlib.util.spec_from_file_location('generator_soc_decision',Path(__file__).with_name('generator_soc_decision.py'))
x=importlib.util.module_from_spec(spec);sys.modules[spec.name]=x;spec.loader.exec_module(x)
POLICY=x.SocPolicy(30,80,120,900,300)
def state(**replacements):
    d=dict(soc_percent=29,soc_age_seconds=5,soc_quality_good=True,
        generator_running=False,generator_feedback_age_seconds=6,
        contact_physically_verified=True,contact_failsafe_verified=True,
        remote_mode_verified=True,automatic_runtime_armed=True,
        policy_requested_enabled=True,elapsed_since_last_transition_seconds=99999,
        condition_held_seconds=140)
    return x.LocalState(**{**d,**replacements})
class TestSOC(unittest.TestCase):
    def test_low_soc_start(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state()).action,'REQUEST_START')
    def test_high_soc_stop(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(generator_running=True,soc_percent=80)).action,'REQUEST_STOP')
    def test_hysteresis_running(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(generator_running=True,soc_percent=65)).action,'NONE')
    def test_hysteresis_stopped(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(soc_percent=50)).action,'NONE')
    def test_request_not_enabled(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(policy_requested_enabled=False)).reason,'not_armed')
    def test_runtime_not_armed(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(automatic_runtime_armed=False)).reason,'not_armed')
    def test_contact_not_verified(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(contact_physically_verified=False)).action,'NONE')
    def test_failsafe_not_verified(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(contact_failsafe_verified=False)).action,'NONE')
    def test_remote_not_verified(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(remote_mode_verified=False)).action,'NONE')
    def test_soc_stale(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(soc_age_seconds=800)).reason,'invalid_or_stale_soc')
    def test_soc_none(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(soc_percent=None)).reason,'invalid_or_stale_soc')
    def test_soc_nan(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(soc_percent=math.nan)).reason,'invalid_or_stale_soc')
    def test_quality_invalid(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(soc_quality_good=False)).action,'NONE')
    def test_generator_feedback_missing(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(generator_running=None)).reason,'generator_state_unknown')
    def test_generator_feedback_stale(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(generator_feedback_age_seconds=121)).reason,'generator_state_unknown')
    def test_min_run(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(generator_running=True,soc_percent=85,elapsed_since_last_transition_seconds=400)).reason,'minimum_run_time')
    def test_min_stop(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(elapsed_since_last_transition_seconds=100)).reason,'minimum_stop_time')
    def test_wait_confirmation(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(condition_held_seconds=119)).reason,'awaiting_soc_confirmation')
    def test_unknown_clock(self):self.assertEqual(x.evaluate_soc_automation(POLICY,state(elapsed_since_last_transition_seconds=None)).reason,'transition_clock_unknown')
    def test_thresholds_equal_invalid(self):
        with self.assertRaises(ValueError):x.SocPolicy(30,31).validate()
    def test_true_is_not_hardcoded(self):
        src=Path(x.__file__).read_text()
        for forbidden in ['write_register(', 'call_home_assistant_service(', 'switch.turn_on','switch.turn_off','DeviceCommand(']:self.assertNotIn(forbidden,src)
if __name__=='__main__':unittest.main()
