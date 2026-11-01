"""Offline control-flow tests; no database, updates, restart, or inverter IO."""
import ast
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from types import SimpleNamespace
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "f53_r14_test_foxess_00007.py"


class Field:
    def __eq__(self, other):
        return ("eq", other)

    def __getitem__(self, key):
        return self

    def as_string(self):
        return self


class Query:
    def where(self, *conditions):
        return self


class Session:
    def __init__(self, rig):
        self.rig = rig
        self.operations = []
        self.closed = self.invalidated = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement):
        self.operations.append(statement)
        result = self.rig.lock_allowed if "pg_try_advisory_lock" in statement else self.rig.unlock_allowed
        return SimpleNamespace(scalar=lambda: result)

    def scalar(self, query):
        return self.rig.module

    def scalars(self, query):
        return SimpleNamespace(all=lambda: self.rig.canary_rows)

    def invalidate(self):
        self.invalidated = True

    def close(self):
        self.closed = True


class R14LabTests(unittest.TestCase):
    def setUp(self):
        self.clock = datetime(2026, 11, 1, 15, tzinfo=timezone.utc)
        self.events, self.outputs, self.queued = [], [], {}
        self.previous, self.canary_rows, self.live_commands = None, [], []
        self.lock_allowed = self.unlock_allowed = True
        self.soc, self.max_soc = 50, 100
        self.before_owner, self.before_remote = "missing", 0
        self.after_owner, self.after_remote, self.after_setpoint = "missing", 0, 0
        self.probe_wrong_identity = self.auto_unverified = self.canary_failed = False
        self.block_preflight = None
        self.protected_own_count = 0
        self.device = SimpleNamespace(id="00007-id", device_uuid="00007-uuid", site_id="home-site",
            serial_number="F800711-TNG-00007", software_version="0.1.168", last_seen_at=self.clock)
        self.module = SimpleNamespace(manufacturer="foxess", model="H3-10.0-E", configuration={})
        self.db = Session(self)

        def require(condition, message):
            if not condition:
                raise RuntimeError(message)

        def preflight(**kwargs):
            self.events.append(("preflight", kwargs))
            require(self.device.software_version == "0.1.168", "wrong version")
            require(self.module.manufacturer == "foxess" and self.module.model == "H3-10.0-E", "wrong inverter")
            if self.block_preflight:
                raise RuntimeError(self.block_preflight)
            return {"agent_version": self.device.software_version, "soc_percent": self.soc, "battery_capacity_kwh": 20}

        def queue(kind, payload, seconds):
            device = self.base.selected(self.db)
            command_id = "queued-" + str(len(self.queued) + 1)
            self.queued[command_id] = (kind, payload)
            self.events.append(("queue", kind, payload, device.serial_number, seconds))
            return command_id

        def wait_command(command_id, timeout):
            self.events.append(("wait", command_id, timeout))
            kind, payload = self.queued.get(command_id, ("manual_battery_canary_v1", {}))
            if kind == "foxess_remote_control_probe":
                return self.probe_result(after=payload["r14_stage"] == "after_auto")
            if kind == "spot_battery_intent":
                return {"verified": not self.auto_unverified, "action": "auto"}
            if self.canary_failed:
                raise RuntimeError("physical canary failed")
            return self.passed(command_id)

        self.base = SimpleNamespace(
            VERSION="0.1.168", SERIAL="F800711-TNG-00018", SOURCE="old", PROTECTED=(),
            EXPECTED_RUNTIME_FINGERPRINT="e" * 64, ACTIVE=("pending", "claimed", "running"),
            selected=lambda db: self.device, require=require, obj=lambda value: value if isinstance(value, dict) else {},
            now=lambda: self.clock, fresh=lambda value: value is not None and -30 <= (self.clock - value).total_seconds() <= 180,
            out=lambda tag, value: self.outputs.append((tag, value)), text=lambda value: value,
            SessionLocal=lambda: self.db, select=lambda *args: Query(),
            InstallationModule=SimpleNamespace(site_id=Field(), module_key=Field()),
            DeviceCommand=SimpleNamespace(device_id=Field(), command_type=Field(), payload=Field()),
            release_preflight=lambda: self.events.append(("release",)), preflight=preflight,
            queue=queue, wait_command=wait_command, live_commands=lambda db, device_id: self.live_commands,
            auto_payload=lambda state: {"action": "auto", "manual_immediate_charge": True,
                "control_source": "manual_immediate_charge", "reason": "old",
                "requested_charge_power_w": 0, "allowed_charge_power_w": 0,
                "requested_discharge_power_w": 0, "allowed_discharge_power_w": 0},
            canary_payload=lambda: {"commissioning": True, "profile_id": "foxess_h3_10_0_e", "power_w": 500,
                "expected_agent_version": "0.1.168", "expected_runtime_fingerprint": "e" * 64},
        )
        source = ast.parse(SCRIPT.read_text())
        definitions = ast.Module(body=[node for node in source.body if isinstance(node, (ast.Assign, ast.FunctionDef))],
                                 type_ignores=[])
        namespace = dict(base=self.base, hashlib=hashlib, hmac=hmac, json=json, secrets=secrets)
        exec(compile(definitions, str(SCRIPT), "exec"), namespace)
        self.ns = namespace
        self.real_find_canary = namespace["find_canary"]
        self.real_protected_differences = namespace["protected_differences"]
        namespace.update(
            find_canary=lambda: self.previous,
            capture_protected=lambda: {"devices": {s: {"present": False} for s in namespace["PROTECTED"]}},
            command_audit=lambda start, end: {"devices": {"F800711-TNG-00018": {
                "r14_tagged_commands_in_window": self.protected_own_count}}},
            energy_snapshot=lambda stage: self.events.append(("context", stage)),
        )

    def probe_result(self, *, after=False):
        return {"read_only": True, "write_performed": False, "profile_id": "foxess_h3_10_0_e",
            "protocol_version": 114 if self.probe_wrong_identity else 115, "device_id": 247, "serial_path_verified": True,
            "owner_marker": {"status": self.after_owner if after else self.before_owner},
            "registers": {"remote_enable_44000": self.after_remote if after else self.before_remote,
                "remote_active_power_w_44002": self.after_setpoint if after else 0,
                "battery_soc_percent_31038": self.soc, "max_soc_41010": self.max_soc,
                "battery_power_w_31036": -1200}}

    def passed(self, command_id):
        return {"status": "passed", "verified": True, "local_final_auto_verified": True,
            "profile_id": "foxess_h3_10_0_e", "agent_version": "0.1.168", "command_id": str(command_id),
            "runtime_fingerprint": "e" * 64, "verification_scope": "bidirectional_500w_and_stop",
            "finished_at": self.clock.isoformat(), "protection": {"status": "locked",
                "profile_id": "foxess_h3_10_0_e", "runtime_fingerprint": "e" * 64,
                "verification_scope": "bidirectional_500w_and_stop", "report_sha256": "a" * 64}}

    def writes(self):
        return [e for e in self.events if e[0] == "queue" and e[1] != "foxess_remote_control_probe"]

    def results(self):
        return [value for tag, value in self.outputs if tag == "RESULT"]

    def test_installed_168_runs_probe_auto_probe_and_one_fixed_canary_only_on_00007(self):
        self.assertEqual(self.ns["main"](), 0)
        queued = [e for e in self.events if e[0] == "queue"]
        self.assertEqual([e[1] for e in queued], ["foxess_remote_control_probe", "spot_battery_intent",
                                                "foxess_remote_control_probe", "manual_battery_canary_v1"])
        self.assertTrue(all(e[3] == "F800711-TNG-00007" for e in queued))
        self.assertEqual(queued[-1][2], self.base.canary_payload())
        self.assertEqual(queued[-1][4], 420)
        self.assertEqual(queued[1][2]["action"], "auto")
        self.assertTrue(self.results()[-1]["physical_canary_performed_in_this_run"])
        self.assertFalse(self.results()[-1]["completed_evidence_reused"])
        self.assertIn("SELECT pg_advisory_unlock(53016807)", self.db.operations)
        self.assertTrue(self.db.closed)

    def test_release_url_is_pinned_and_does_not_follow_main(self):
        self.assertIn("/68281fa63ced8d5093dce6fb452149b0dea18120/", self.base.RAW_BASE)
        self.assertNotIn("/main/", self.base.RAW_BASE)

    def test_wrong_or_stale_version_never_queues_any_command(self):
        for version, age in (("0.1.167", 0), ("0.1.169", 0), ("0.1.168", 181)):
            with self.subTest(version=version, age=age):
                self.device.software_version = version
                self.device.last_seen_at = self.clock - timedelta(seconds=age)
                self.assertEqual(self.ns["main"](), 2)
                self.assertFalse(self.queued)

    def test_wrong_model_is_logged_before_preflight_fails_and_no_command_is_queued(self):
        self.module.model = "Different FoxESS Model"
        self.assertEqual(self.ns["main"](), 2)
        logged = next(value for tag, value in self.outputs if tag == "TARGET_IDENTITY")
        self.assertEqual(logged["model"], "Different FoxESS Model")
        self.assertFalse(self.queued)

    def test_queue_refuses_updates_restarts_resets_power_and_bad_probe_payloads(self):
        cases = [("agent_update", {}), ("restart", {}), ("reboot", {}), ("reset_os", {}),
                 ("spot_battery_intent", {"action": "charge_grid"}),
                 ("foxess_remote_control_probe", {"read_only": False, "r14_stage": "before_auto"}),
                 ("manual_battery_canary_v1", {**self.base.canary_payload(), "power_w": 1000}),
                 ("manual_battery_canary_v1", {**self.base.canary_payload(), "expected_runtime_fingerprint": "f" * 64})]
        for kind, payload in cases:
            with self.subTest(kind=kind, payload=payload), self.assertRaisesRegex(RuntimeError, "COMMAND_NOT_ALLOWED"):
                self.ns["queue"](kind, payload, 180)
        self.assertFalse(self.queued)

    def test_target_rebinding_or_protected_serial_is_rejected_before_queue(self):
        self.ns["selected"](self.db)
        self.device.site_id = "different-site"
        with self.assertRaisesRegex(RuntimeError, "BINDING_CHANGED"):
            self.ns["queue"]("manual_battery_canary_v1", self.base.canary_payload(), 420)
        self.device.site_id = "home-site"
        self.device.serial_number = "F800711-TNG-00018"
        with self.assertRaisesRegex(RuntimeError, "NOT_00007"):
            self.ns["selected"](self.db)
        self.assertFalse(self.queued)

    def test_all_three_other_installations_are_protected(self):
        self.assertEqual(set(self.ns["PROTECTED"]), {"F800711-TNG-00012", "F800711-TNG-00013", "F800711-TNG-00018"})

    def test_failed_physical_identity_never_sends_auto_or_canary(self):
        self.probe_wrong_identity = True
        self.assertEqual(self.ns["main"](), 2)
        self.assertEqual(self.writes(), [])

    def test_missing_foreign_or_invalid_owner_blocks_any_write(self):
        for owner, remote in (("missing", 1), ("foreign", 0), ("invalid", 1), ("unreadable", 0)):
            with self.subTest(owner=owner):
                self.before_owner, self.before_remote = owner, remote
                self.assertEqual(self.ns["main"](), 2)
                self.assertEqual(self.writes(), [])

    def test_owned_remote_state_can_be_restored_and_natural_battery_power_is_allowed(self):
        self.before_owner, self.before_remote = "owned", 1
        self.assertEqual(self.ns["main"](), 0)
        self.ns["require_auto_readback"](self.probe_result(after=True))
        self.assertEqual(self.probe_result(after=True)["registers"]["battery_power_w_31036"], -1200)

    def test_failed_auto_or_nonzero_remote_readback_prevents_canary(self):
        self.auto_unverified = True
        self.assertEqual(self.ns["main"](), 2)
        self.assertEqual([w[1] for w in self.writes()], ["spot_battery_intent"])
        self.auto_unverified = False
        self.after_setpoint = 500
        self.assertEqual(self.ns["main"](), 2)
        self.assertNotIn("manual_battery_canary_v1", [w[1] for w in self.writes()])

    def test_soc_outside_window_still_verifies_auto_then_returns_pending_without_canary(self):
        for soc in (29, 91, 100):
            with self.subTest(soc=soc):
                self.soc = soc
                self.assertEqual(self.ns["main"](), 3)
                self.assertNotIn("manual_battery_canary_v1", [w[1] for w in self.writes()])
                self.assertFalse(self.results()[-1]["physical_canary_performed_in_this_run"])

    def test_soc_boundaries_and_charge_headroom(self):
        for soc, maximum, blocked in ((30, 100, False), (90, 100, False), (90, 90, True), (50, 50, True)):
            with self.subTest(soc=soc, maximum=maximum):
                self.soc, self.max_soc = soc, maximum
                self.assertEqual(self.ns["canary_pending_reason"](self.probe_result()) is not None, blocked)
        self.soc = True
        with self.assertRaisesRegex(RuntimeError, "SOC_INVALID"):
            self.ns["canary_pending_reason"](self.probe_result())

    def test_active_canary_resumes_same_id_without_auto_or_new_test(self):
        self.previous = {"id": "prior-running", "status": "running", "result": {}}
        self.assertEqual(self.ns["main"](), 0)
        self.assertFalse(self.queued)
        self.assertIn(("wait", "prior-running", 420), self.events)
        self.assertTrue(self.results()[-1]["existing_canary_completion_observed"])
        self.assertFalse(self.results()[-1]["new_physical_test_queued"])

    def test_completed_canary_returns_explicit_historical_evidence_without_new_commands(self):
        self.previous = {"id": "prior-pass", "status": "succeeded", "result": self.passed("prior-pass")}
        self.assertEqual(self.ns["main"](), 0)
        self.assertFalse(self.queued)
        result = self.results()[-1]
        self.assertTrue(result["completed_evidence_reused"])
        self.assertFalse(result["physical_canary_performed_in_this_run"])
        self.assertFalse(result["current_auto_reverified_by_this_invocation"])

    def test_terminal_failed_cancelled_expired_canary_is_never_repeated(self):
        for status in ("failed", "cancelled", "expired"):
            with self.subTest(status=status):
                self.previous = {"id": "prior-terminal", "status": status, "result": {}}
                self.assertEqual(self.ns["main"](), 2)
                self.assertFalse(self.queued)

    def test_previous_pass_requires_matching_profile_build_and_complete_pin(self):
        result = self.passed("prior-pass")
        result["protection"]["runtime_fingerprint"] = "f" * 64
        self.previous = {"id": "prior-pass", "status": "succeeded", "result": result}
        self.assertEqual(self.ns["main"](), 2)
        self.assertFalse(self.queued)

    def test_find_canary_rejects_foreign_inflight_duplicate_and_changed_payload(self):
        self.live_commands = [SimpleNamespace(command_type="manual_battery_canary_v1", payload={"source": "someone_else"})]
        with self.assertRaisesRegex(RuntimeError, "ANOTHER_CANARY"):
            self.real_find_canary()
        self.live_commands = []
        row = SimpleNamespace(id="existing", device_id=self.device.id, status="succeeded",
            payload={**self.base.canary_payload(), "source": self.ns["SOURCE"]}, result={})
        self.canary_rows = [row, row]
        with self.assertRaisesRegex(RuntimeError, "MULTIPLE_R14"):
            self.real_find_canary()
        self.canary_rows = [row]
        row.payload["power_w"] = 600
        with self.assertRaisesRegex(RuntimeError, "PAYLOAD_CHANGED"):
            self.real_find_canary()

    def test_active_user_session_or_automatic_control_blocks_before_probe(self):
        self.block_preflight = "ACTIVE_USER_MANUAL_SESSION"
        self.assertEqual(self.ns["main"](), 2)
        self.assertFalse(self.queued)

    def test_failed_canary_never_queues_a_blind_followup_auto(self):
        self.canary_failed = True
        self.assertEqual(self.ns["main"](), 2)
        self.assertEqual([w[1] for w in self.writes()], ["spot_battery_intent", "manual_battery_canary_v1"])
        self.assertIn(("context", "after_canary"), self.events)
        self.assertTrue(self.results()[-1]["new_physical_test_queued"])
        self.assertIsNone(self.results()[-1]["physical_canary_performed_in_this_run"])

    def test_resumed_canary_failure_still_captures_context_and_never_queues_another(self):
        self.previous = {"id": "prior-running", "status": "running", "result": {}}
        self.canary_failed = True
        self.assertEqual(self.ns["main"](), 2)
        self.assertIn(("context", "after_canary"), self.events)
        self.assertFalse(self.queued)

    def test_protected_site_overlap_is_rejected_before_any_command(self):
        self.ns["capture_protected"] = lambda: {"devices": {"F800711-TNG-00018": {
            "present": True, "site_id": self.device.site_id}}}
        self.assertEqual(self.ns["main"](), 2)
        self.assertFalse(self.queued)
        self.assertIn("PROTECTED_SITE", self.results()[-1]["error"])

    def test_lock_failure_blocks_commands_and_unlock_failure_changes_exit_status(self):
        self.lock_allowed = False
        self.assertEqual(self.ns["main"](), 2)
        self.assertFalse(self.queued)
        self.lock_allowed = True
        self.unlock_allowed = False
        self.assertEqual(self.ns["main"](), 2)
        self.assertTrue(self.db.invalidated)

    def test_actual_protected_source_count_prevents_overall_pass(self):
        self.protected_own_count = 1
        self.assertEqual(self.ns["main"](), 2)
        self.assertTrue(any(tag == "PROTECTED_SCOPE_VIOLATION" for tag, _ in self.outputs))
        self.assertEqual(self.outputs[-1][1]["status"], "NOT_CONFIRMED")

    def test_protected_audit_failure_is_not_silently_reported_as_success(self):
        self.ns["command_audit"] = lambda *args: (_ for _ in ()).throw(RuntimeError("unavailable"))
        self.assertEqual(self.ns["main"](), 2)
        self.assertTrue(any(tag == "PROTECTED_AUDIT_ERROR" for tag, _ in self.outputs))


if __name__ == "__main__":
    unittest.main()
