"""Offline tests for the scoped recovery flow; no backend, network or Modbus IO."""
import ast
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
from types import SimpleNamespace
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "f53_r13r1_finish_foxess.py"


class LockSession:
    def __init__(self):
        self.operations = []
        self.invalidated = False
        self.closed = False

    def execute(self, statement):
        self.operations.append(statement)
        return SimpleNamespace(scalar=lambda: True)

    def invalidate(self):
        self.invalidated = True

    def close(self):
        self.closed = True


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.outputs = []
        self.version = "0.1.167"
        self.recovery = None
        self.live_canary = None
        self.db = LockSession()

        def require(condition, message):
            if not condition:
                raise RuntimeError(message)

        def queue(kind, payload, seconds):
            self.events.append(("queue", kind, payload))
            return "queued-" + kind

        self.base = SimpleNamespace(
            VERSION="0.1.168", SERIAL="F800711-TNG-00018", PROTECTED=("F800711-TNG-00012", "F800711-TNG-00013"),
            ACTIVE=("pending", "claimed", "running"), UPDATE_TIMEOUT=1500, POLL_SECONDS=5,
            require=require, obj=lambda x: x if isinstance(x, dict) else {}, text=lambda x: x,
            now=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc),
            out=lambda tag, value: self.outputs.append((tag, value)),
            SessionLocal=lambda: self.db, release_preflight=lambda: self.events.append(("release",)),
            preflight=lambda **kw: {"agent_version": self.version, "soc_percent": 50, "battery_capacity_kwh": 20},
            wait_version=lambda: require(self.version == "0.1.168", "wrong version"),
            queue=queue, auto_payload=lambda state: {"action": "auto"},
            canary_payload=lambda: {"expected_runtime_fingerprint": "approved-fingerprint"},
            wait_command=lambda command_id, timeout: {"verified": True, "action": "auto", "status": "passed"},
            report_canary_success=lambda result, command_id: self.events.append(("pass", command_id)),
        )
        namespace = dict(base=self.base, datetime=datetime, timedelta=timedelta, hashlib=hashlib,
            hmac=hmac, json=json, Path=Path, secrets=secrets, time=time, uuid=uuid)
        source = ast.parse(SCRIPT.read_text())
        definitions = ast.Module(body=[node for node in source.body
            if isinstance(node, (ast.Assign, ast.FunctionDef))], type_ignores=[])
        exec(compile(definitions, str(SCRIPT), "exec"), namespace)
        self.ns = namespace
        self.original_finish = namespace["finish_installed_agent"]
        namespace.update(
            verify_original_attempt=lambda: (self.base.now(), self.base.now()),
            capture_protected=lambda: {"devices": {serial: {"present": False} for serial in self.base.PROTECTED}},
            backend_source_hashes=lambda: {}, command_audit=lambda start, end: {},
            find_recovery_update=lambda: self.recovery,
            current_version=lambda: self.version,
            find_recovery_canary=lambda: self.live_canary,
            probe=lambda stage: self.events.append(("probe", stage)) or self.probe_result(),
            finish_installed_agent=lambda: self.events.append(("finish",)) or 0,
        )

        def wait_update(command_id):
            self.events.append(("wait-update", command_id))
            self.version = "0.1.168"

        namespace["wait_update"] = wait_update

    def probe_result(self, soc=50, max_soc=100, remote=0, setpoint=0, owner="missing"):
        return {"read_only": True, "write_performed": False, "profile_id": "foxess_h3_10_0_e",
            "protocol_version": 115, "device_id": 247, "owner_marker": {"status": owner},
            "registers": {"remote_enable_44000": remote, "remote_active_power_w_44002": setpoint,
                "battery_soc_percent_31038": soc, "max_soc_41010": max_soc,
                "battery_power_w_31036": -1200}}

    def test_single_upgrade_requires_read_only_worker_readiness_first(self):
        self.assertEqual(self.ns["main"](), 0)
        writes = [event for event in self.events if event[0] == "queue"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1], "agent_update")
        self.assertEqual(writes[0][2], {"target_version": "0.1.168", "backup": True,
            "recovery_of": "7c5cfc5d-2325-49c6-b23a-c25f8c4f9b63"})
        self.assertLess(self.events.index(("probe", "old_0167_worker_ready_read_only")), self.events.index(writes[0]))
        self.assertTrue(self.db.closed)
        self.assertIn("SELECT pg_advisory_unlock(53016818)", self.db.operations)

    def test_failed_readiness_never_queues_an_update(self):
        def fail(stage):
            raise RuntimeError("worker still blocked")
        self.ns["probe"] = fail
        self.assertEqual(self.ns["main"](), 2)
        self.assertFalse(any(event[0] == "queue" for event in self.events))

    def test_terminal_failed_recovery_is_not_repeated(self):
        self.recovery = {"id": "existing", "status": "failed", "result": {"phase": "update_validation_failed"}}
        self.assertEqual(self.ns["main"](), 2)
        self.assertFalse(any(event[0] in {"queue", "finish"} for event in self.events))

    def test_active_recovery_resumes_same_id_without_new_update(self):
        self.recovery = {"id": "existing", "status": "running"}
        self.assertEqual(self.ns["main"](), 0)
        self.assertIn(("wait-update", "existing"), self.events)
        self.assertFalse(any(event[0] == "queue" for event in self.events))

    def test_already_installed_version_can_continue_after_old_failed_recovery(self):
        self.version = "0.1.168"
        self.recovery = {"id": "old-failed", "status": "failed"}
        self.assertEqual(self.ns["main"](), 0)
        self.assertIn(("finish",), self.events)
        self.assertFalse(any(event[0] == "queue" for event in self.events))

    def test_unexpected_version_never_queues_an_update_or_test(self):
        self.version = "0.1.169"
        self.assertEqual(self.ns["main"](), 2)
        self.assertFalse(any(event[0] in {"queue", "finish"} for event in self.events))

    def test_live_canary_resumes_without_another_auto_or_test(self):
        self.version = "0.1.168"
        self.live_canary = {"id": "running-canary", "status": "running"}
        self.assertEqual(self.ns["main"](), 0)
        self.assertIn(("pass", "running-canary"), self.events)
        self.assertFalse(any(event[0] in {"queue", "finish"} for event in self.events))

    def test_completed_canary_is_reused_without_repeating_physical_test(self):
        self.version = "0.1.168"
        self.live_canary = {"id": "passed-canary", "status": "succeeded", "result": {"verified": True}}
        self.assertEqual(self.ns["main"](), 0)
        self.assertIn(("pass", "passed-canary"), self.events)
        self.assertFalse(any(event[0] in {"queue", "finish"} for event in self.events))

    def test_failed_canary_is_not_automatically_repeated(self):
        self.version = "0.1.168"
        self.live_canary = {"id": "failed-canary", "status": "failed", "result": {"verified": False}}
        self.assertEqual(self.ns["main"](), 2)
        self.assertFalse(any(event[0] in {"queue", "finish", "pass"} for event in self.events))

    def test_orphaned_nonzero_setpoint_blocks_update(self):
        self.ns["probe"] = lambda stage: self.probe_result(remote=0, setpoint=500)
        self.assertEqual(self.ns["main"](), 2)
        self.assertFalse(any(event[0] in {"queue", "finish"} for event in self.events))

    def test_zero_setpoint_remote_enabled_allows_driver_recovery_after_update(self):
        self.ns["require_update_power_idle"](self.probe_result(remote=1, setpoint=0))

    def test_missing_or_foreign_owner_cannot_be_taken_over(self):
        for owner, remote in (("missing", 1), ("foreign", 0), ("invalid", 1), ("unreadable", 0)):
            with self.subTest(owner=owner, remote=remote), self.assertRaises(RuntimeError):
                self.ns["require_auto_owner"](self.probe_result(owner=owner, remote=remote))

    def test_auto_readback_is_remote_state_and_not_zero_actual_battery_power(self):
        self.ns["require_auto_readback"](self.probe_result())
        for modified in (self.probe_result(setpoint=500), self.probe_result(remote=1), self.probe_result(owner="owned")):
            with self.assertRaises(RuntimeError):
                self.ns["require_auto_readback"](modified)

    def test_soc_window_and_charge_room(self):
        for soc, max_soc, pending in ((29, 100, True), (30, 100, False), (90, 100, False),
                                     (91, 100, True), (90, 90, True), (80, 80, True)):
            with self.subTest(soc=soc, max_soc=max_soc):
                result = self.ns["canary_pending_reason"](self.probe_result(soc=soc, max_soc=max_soc))
                self.assertEqual(result is not None, pending)

    def test_soc_above_window_still_verifies_auto_but_never_starts_canary(self):
        self.version = "0.1.168"
        self.ns["probe"] = lambda stage: self.probe_result(soc=96)
        self.assertEqual(self.original_finish(), 3)
        queued = [event[1] for event in self.events if event[0] == "queue"]
        self.assertEqual(queued, ["spot_battery_intent"])
        self.assertEqual(self.outputs[-1][1]["status"], "INSTALLED_AUTO_VERIFIED_CANARY_PENDING_SOC")

    def test_bad_auto_readback_blocks_canary_even_after_acknowledged_auto(self):
        self.version = "0.1.168"
        self.ns["probe"] = lambda stage: self.probe_result(setpoint=500) if stage.endswith("after_auto") else self.probe_result()
        with self.assertRaisesRegex(RuntimeError, "AUTO_FINAL_READBACK"):
            self.original_finish()
        self.assertNotIn("manual_battery_canary_v1", [event[1] for event in self.events if event[0] == "queue"])


if __name__ == "__main__":
    unittest.main()
