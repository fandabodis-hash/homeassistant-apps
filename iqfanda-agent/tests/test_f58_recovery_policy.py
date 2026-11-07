"""F58 R5 – gate scenarios for safe automatic stale-command retirement."""
import dataclasses
import unittest

from local_modules.battery_recovery_policy import BatteryRecoverySnapshot, decide


BASE = BatteryRecoverySnapshot(
    device_uuid_verified=True, local_state="stopped", local_verified=True,
    local_reason="process_restart", local_session_id="s-1", local_status_age_seconds=8,
    cloud_manual_state="requested", cloud_session_id="s-1", cloud_action="discharge_grid",
    remote_enable=0, remote_setpoint_w=0, owner_marker="missing", remote_readback_age_seconds=4,
    export_forbidden=False, local_grid_import_w=1000, meter_age_seconds=3,
    bms_ok=True, soc_percent=41, min_soc_percent=10,
)


class RecoveryPolicyTests(unittest.TestCase):
    def check(self, outcome, **updates):
        result = decide(dataclasses.replace(BASE, **updates))
        self.assertEqual(result.outcome, outcome)
        self.assertFalse(result.automatic_start_allowed)
        self.assertFalse(result.agent_restart_allowed)
        return result

    def test_matched_verified_session_can_be_retired_without_restart(self):
        self.assertTrue(self.check("stale_manual_replay").cloud_session_retirement_allowed)

    def test_mismatched_session_never_retired(self):
        self.assertFalse(self.check("stale_manual_replay",cloud_session_id="different").cloud_session_retirement_allowed)

    def test_missing_legacy_session_not_bypassed(self):
        self.assertFalse(self.check("stale_manual_replay",local_session_id=None).cloud_session_retirement_allowed)

    def test_readback_missing_blocks_actions(self):
        self.check("telemetry_unavailable",remote_enable=None)

    def test_remote_enabled_foreign_owner_is_untouchable(self):
        x=self.check("foreign_remote_owner",remote_enable=1,owner_marker="foreign")
        self.assertFalse(x.stop_auto_retry_allowed)

    def test_remote_enabled_by_fanda_calls_only_existing_stop_owner(self):
        x=self.check("remote_auto_required",remote_enable=1,owner_marker="owned")
        self.assertTrue(x.stop_auto_retry_allowed)
        self.assertFalse(x.cloud_session_retirement_allowed)

    def test_stop_pending_with_owner_retries_stop_not_restart(self):
        x=self.check("unverified_stop",local_state="stop_pending",local_verified=False,owner_marker="owned")
        self.assertTrue(x.stop_auto_retry_allowed)

    def test_zero_export_blocks_manual_grid_discharge(self):
        x=self.check("export_risk",export_forbidden=True)
        self.assertFalse(x.cloud_session_retirement_allowed)

    def test_zero_export_meter_stale_blocks_manual_grid_discharge(self):
        self.check("export_risk",export_forbidden=True,meter_age_seconds=100)

    def test_bms_fault_cannot_be_bypassed(self):
        self.check("safety_lockout",bms_ok=False)

    def test_soc_minimum_cannot_be_bypassed(self):
        self.check("safety_lockout",soc_percent=11)

    def test_active_local_session_not_retired(self):
        self.check("active_session",local_state="active")

    def test_uuid_mismatch_disables_cleanup(self):
        self.check("telemetry_unavailable",device_uuid_verified=False)

    def test_stale_local_telemetry_disables_cleanup(self):
        self.check("telemetry_unavailable",local_status_age_seconds=181)

    def test_healthy_idle(self):
        self.check("healthy_idle",cloud_manual_state="idle")

    def test_unknown_owner_marker_disables_cleanup(self):
        self.check("telemetry_unavailable",owner_marker="bad")

    def test_invalid_owner_marker_disables_cleanup(self):
        self.check("foreign_remote_owner",owner_marker="invalid")


if __name__ == "__main__":
    unittest.main()
