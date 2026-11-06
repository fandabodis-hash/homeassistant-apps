import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import command_worker as cw
import thermostat_commission as c
import thermostat_preflight as pre
import thermostat_runtime as r
import zigbee_manager as zm

UID = "00000000-0000-0000-0000-000000000114"
TID = "00000000-0000-0000-0000-000000000014"
REF = "switch.test_heat"


def payload(**extra):
    value = {
        "device_uuid": UID,
        "request_id": "00000000-0000-0000-0000-000000000914",
        "target_ids": [TID],
        "expected_config_version": 7,
        "expected_sensor_devices": {TID: "verified_sensor_registry_00014"},
        "activate": True,
    }
    value.update(extra)
    return value


def preflight_report(off=True):
    return {
        "checks_passed": True,
        "request_id": payload()["request_id"],
        "device_uuid": UID,
        "config_version": 7,
        "targets": [{
            "target_id": TID,
            "all_outputs_currently_off": off,
            "contract_sha256": "a" * 64,
            "outputs": [{"reference": REF, "registry_matches": True, "state": "off" if off else "on"}],
        }],
    }


class Commission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patches = [
            patch.object(c, "COMMISSION_PATH", self.root / "commission.json"),
            patch.object(c, "HANDBACK_PATH", self.root / "handback.json"),
            patch.object(r, "ROOT", self.root),
            patch.object(r, "STATUS", {"mode": "disabled"}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_commission_saves_active_options_and_marker_before_restart(self):
        reports = []
        options_written = []
        restarts = []
        with patch.object(pre, "inspect", return_value=preflight_report()),              patch.object(c, "_current_options", return_value={"other": "preserved"}),              patch.object(c, "_set_options", side_effect=lambda value: options_written.append(value)),              patch.object(c, "_restart_after_report", side_effect=lambda: restarts.append(True)):
            c.execute_commission(
                identity={"device_uuid": UID}, command_id="cmd", command_payload=payload(),
                submit_result=lambda **kw: reports.append(kw))
        self.assertEqual(reports[-1]["status"], "succeeded")
        self.assertEqual(options_written[0]["local_thermostat_mode"], "active")
        self.assertEqual(options_written[0]["local_thermostat_targets"], [TID])
        self.assertEqual(options_written[0]["other"], "preserved")
        marker = json.loads(c.COMMISSION_PATH.read_text())
        self.assertEqual(marker["outputs"], [REF])
        self.assertEqual(marker["config_version"], 7)
        self.assertEqual(restarts, [True])

    def test_commission_requires_outputs_off(self):
        reports = []
        with patch.object(pre, "inspect", return_value=preflight_report(False)),              patch.object(c, "_restart_after_report") as restart:
            c.execute_commission(
                identity={"device_uuid": UID}, command_id="cmd", command_payload=payload(),
                submit_result=lambda **kw: reports.append(kw))
        self.assertEqual(reports[-1]["status"], "failed")
        self.assertFalse(c.COMMISSION_PATH.exists())
        restart.assert_not_called()

    def test_foreign_device_rejected(self):
        reports = []
        with patch.object(pre, "inspect") as inspect:
            c.execute_commission(
                identity={"device_uuid": UID}, command_id="cmd",
                command_payload=payload(device_uuid="00000000-0000-0000-0000-000000000999"),
                submit_result=lambda **kw: reports.append(kw))
        self.assertEqual(reports[-1]["status"], "failed")
        inspect.assert_not_called()

    def test_marker_fences_pv_and_direct_switch_only_for_owned_output(self):
        c.COMMISSION_PATH.write_text(json.dumps({"outputs": [REF]}))
        with patch.object(r, "OWNERS", {}):
            self.assertTrue(c.command_is_fenced("pv_surplus_target_intent", {"output_reference": REF}))
            self.assertTrue(c.command_is_fenced("zigbee_switch_set", {"entity_id": REF}))
            self.assertFalse(c.command_is_fenced("zigbee_switch_set", {"entity_id": "switch.other"}))
            self.assertFalse(c.command_is_fenced(c.HANDBACK_COMMAND, {"entity_id": REF}))

    def test_handback_keeps_fence_until_off_verified_and_restart(self):
        reports = []
        options_written = []
        restarts = []
        owners = {REF: {"target_id": TID, "device_reg_id": "reg"}}
        with patch.object(r, "OWNERS", owners),              patch.object(r, "options", return_value=("active", [TID], 1800)),              patch.object(r, "off_reserved", return_value=None),              patch.object(zm, "get_entity_state", return_value={"state": "off"}),              patch.object(c, "_current_options", return_value={"other": "preserved"}),              patch.object(c, "_set_options", side_effect=lambda value: options_written.append(value)),              patch.object(c, "_restart_after_report", side_effect=lambda: restarts.append(True)):
            c.execute_handback(
                identity={"device_uuid": UID}, command_id="cmd",
                command_payload={"device_uuid": UID, "target_ids": [TID], "confirm_handback": True},
                submit_result=lambda **kw: reports.append(kw))
        self.assertEqual(reports[-1]["status"], "succeeded")
        self.assertEqual(options_written[0]["local_thermostat_mode"], "disabled")
        self.assertEqual(options_written[0]["local_thermostat_targets"], [])
        self.assertEqual(json.loads(c.HANDBACK_PATH.read_text())["outputs"], [REF])
        self.assertEqual(restarts, [True])
        self.assertIn(REF, owners)

    def test_handback_refuses_release_without_off_feedback(self):
        reports = []
        owners = {REF: {"target_id": TID, "device_reg_id": "reg"}}
        with patch.object(r, "OWNERS", owners),              patch.object(r, "options", return_value=("active", [TID], 1800)),              patch.object(r, "off_reserved", return_value=None),              patch.object(zm, "get_entity_state", return_value={"state": "on"}),              patch.object(c, "_set_options") as set_options,              patch.object(c, "_restart_after_report") as restart:
            c.execute_handback(
                identity={"device_uuid": UID}, command_id="cmd",
                command_payload={"device_uuid": UID, "target_ids": [TID], "confirm_handback": True},
                submit_result=lambda **kw: reports.append(kw))
        self.assertEqual(reports[-1]["status"], "failed")
        self.assertIn(REF, owners)
        set_options.assert_not_called()
        restart.assert_not_called()

    def test_worker_routes_commission_executor(self):
        reports = []
        calls = []
        with patch.object(cw, "submit_command_result", side_effect=lambda **kw: reports.append(kw)),              patch.object(c, "command_is_fenced", return_value=False),              patch.object(c, "execute_commission", side_effect=lambda **kw: calls.append(kw)):
            cw.execute_command(
                identity={"device_uuid": UID},
                command={"id": "q", "command_type": c.COMMISSION_COMMAND, "payload": payload()})
        self.assertEqual(len(calls), 1)
        self.assertEqual(reports[0]["status"], "running")

    def test_worker_fence_rejects_legacy_command_before_executor(self):
        reports = []
        with patch.object(cw, "submit_command_result", side_effect=lambda **kw: reports.append(kw)),              patch.object(c, "command_is_fenced", return_value=True):
            cw.execute_command(
                identity={"device_uuid": UID},
                command={"id": "q", "command_type": "zigbee_switch_set",
                         "payload": {"entity_id": REF, "action": "on"}})
        self.assertEqual(reports[-1]["status"], "failed")
        self.assertEqual(reports[-1]["result"]["phase"], "local_thermostat_owns_output")


if __name__ == "__main__":
    unittest.main()
