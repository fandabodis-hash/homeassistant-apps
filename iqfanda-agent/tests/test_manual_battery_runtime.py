"""Stateful regressions for local ownership, STOP and recovery; no hardware."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(APP))
from manual_battery_runtime import LocalBatteryRuntime
from communication.battery_control_safety import BatteryControlSafetyStop


@dataclass
class Result:
    action: str
    applied_power_w: int
    requested_power_w: int = 500
    verified: bool = True
    write_performed: bool = True


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Inverter:
    def __init__(self):
        self.calls = []
        self.start_error = None
        self.stop_error = None
        self.verified = True
    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        action = kwargs["action"]
        if action == "auto" and self.stop_error:
            raise self.stop_error
        if action != "auto" and self.start_error:
            raise self.start_error
        power = kwargs["allowed_charge_power_w"] if action == "charge_grid" else kwargs["allowed_discharge_power_w"]
        return Result(action, power, verified=self.verified if action != "auto" else True)


class LocalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "runtime.json"
        self.clock = Clock()
        self.inverter = Inverter()
        self.config = {"module_runtime_configurations": [{"module_key": "photovoltaic",
            "manufacturer": "goodwe", "model": "GW5K-ET", "communicator_id": "usb-one",
            "modbus_device_id": 247, "communication_type": "rs485"}]}
        self.runtime = self.new_runtime()

    def new_runtime(self):
        return LocalBatteryRuntime(path=self.path, clock=self.clock, execute=self.inverter,
            config_loader=lambda: self.config, feature_guard=lambda: "feature-1")

    def payload(self, action="discharge_grid", manual=True, **extra):
        return {"action": action, "reason": "test", "manual_immediate_charge": manual,
            "control_source": "manual_immediate_charge" if manual else "spot",
            "requested_charge_power_w": 500 if action == "charge_grid" else 0,
            "allowed_charge_power_w": 500 if action == "charge_grid" else 0,
            "requested_discharge_power_w": 500 if action == "discharge_grid" else 0,
            "allowed_discharge_power_w": 500 if action == "discharge_grid" else 0,
            "target_soc_percent": 80 if action == "charge_grid" else 40,
            "minimum_discharge_soc_percent": 40, "current_soc_percent": 60,
            "battery_capacity_kwh": 20,
            "valid_until": (self.clock() + timedelta(seconds=180)).isoformat(), **extra}

    def submit(self, action="discharge_grid", **extra):
        return self.runtime.submit(payload=self.payload(action, **extra),
                                   command_id="cmd-1", cloud_config=self.config)

    def test_both_directions_use_the_common_local_owner(self):
        for action in ("discharge_grid", "charge_grid"):
            self.submit(action)
            self.assertEqual(self.runtime.status()["state"], "active")
            self.assertEqual(self.inverter.calls[-1]["action"], action)
            self.submit("auto")
            self.assertEqual(self.runtime.status()["state"], "stopped")
            self.clock.advance(1)

    def test_tick_renews_locally_without_a_cloud_request(self):
        self.submit()
        self.clock.advance(10)
        self.runtime.tick()
        self.assertEqual([c["action"] for c in self.inverter.calls], ["discharge_grid"] * 2)

    def test_background_auto_cannot_overwrite_manual(self):
        self.submit()
        result = self.submit("auto", manual=False)
        self.assertEqual(result["phase"], "local_manual_owner_preserved")
        self.assertEqual(len(self.inverter.calls), 1)
        self.assertEqual(self.runtime.status()["state"], "active")

    def test_explicit_manual_stop_overrides_active_manual(self):
        self.submit()
        self.submit("auto")
        self.assertEqual(self.inverter.calls[-1]["action"], "auto")
        self.assertTrue(self.runtime.status()["verified"])

    def test_offline_legacy_lease_expires_to_auto(self):
        self.submit()
        self.clock.advance(181)
        self.runtime.tick()
        self.assertEqual(self.runtime.status()["reason"], "authorization_expired")
        self.assertEqual(self.inverter.calls[-1]["action"], "auto")

    def test_explicit_local_session_can_outlive_transport_lease(self):
        self.submit(manual_session_id="session-1", manual_session_expires_at=(self.clock() + timedelta(minutes=20)).isoformat())
        self.clock.advance(181)
        self.runtime.tick()
        self.assertEqual(self.runtime.status()["state"], "active")
        self.clock.advance(1020)
        self.runtime.tick()
        self.assertEqual(self.runtime.status()["state"], "stopped")

    def test_cloud_renewal_does_not_reset_twelve_hour_session_start(self):
        self.submit()
        started = self.runtime.status()["started_at"]
        self.clock.advance(100)
        self.submit()
        self.assertEqual(self.runtime.status()["started_at"], started)
        self.assertEqual(self.runtime.status()["expires_at"], (self.clock() + timedelta(seconds=180)).isoformat())

    def test_safety_stop_is_terminal_and_is_not_restarted_by_refresh(self):
        self.submit()
        self.inverter.start_error = BatteryControlSafetyStop("minimum_soc_reached", 42)
        self.clock.advance(10)
        self.runtime.tick()
        self.assertEqual(self.runtime.status()["state"], "stopped")
        self.assertEqual(self.runtime.status()["observed_soc_percent"], 42)
        self.inverter.start_error = None
        with self.assertRaisesRegex(RuntimeError, "already ended locally"):
            self.submit()
        self.assertEqual(self.inverter.calls[-1]["action"], "auto")

    def test_explicit_stop_rearms_a_new_user_request(self):
        self.submit()
        self.clock.advance(181)
        self.runtime.tick()
        self.submit("auto")
        self.clock.advance(1)
        self.submit()
        self.assertEqual(self.runtime.status()["state"], "active")

    def test_f58_background_auto_after_explicit_stop_does_not_reblock_same_power(self):
        """Regression: old 0.1.171 re-latched same request after next Spot AUTO."""
        self.submit("discharge_grid")
        self.submit("auto")
        self.assertEqual(self.runtime.status()["state"], "stopped")
        self.assertEqual(self.runtime._load().get("blocked_signature"), None)
        self.submit("auto", manual=False)
        self.assertEqual(self.runtime.status()["state"], "stopped")
        self.assertEqual(self.runtime._load().get("blocked_signature"), None)
        self.clock.advance(1)
        self.submit("discharge_grid")
        self.assertEqual(self.runtime.status()["state"], "active")
        self.assertEqual(self.inverter.calls[-1]["action"], "discharge_grid")

    def test_f58_repeated_background_auto_preserves_explicit_stop_clear(self):
        self.submit("charge_grid")
        self.submit("auto")
        for _ in range(4):
            self.clock.advance(20)
            self.submit("auto", manual=False)
            self.assertEqual(self.runtime._load().get("blocked_manual_signatures"), [])
        self.submit("charge_grid")
        self.assertEqual(self.runtime.status()["state"], "active")

    def test_f58_safety_stopped_session_stays_blocked_through_spot_auto(self):
        self.submit("discharge_grid")
        self.inverter.start_error = BatteryControlSafetyStop("minimum_soc_reached", 42)
        self.clock.advance(10)
        self.runtime.tick()
        self.inverter.start_error = None
        self.submit("auto", manual=False)
        with self.assertRaisesRegex(RuntimeError, "already ended locally"):
            self.submit("discharge_grid")
        self.submit("auto")  # explicit user STOP is the only rearm
        self.clock.advance(1)
        self.submit("discharge_grid")
        self.assertEqual(self.runtime.status()["state"], "active")

    def test_f58_process_restart_stays_blocked_until_explicit_stop(self):
        self.submit("charge_grid")
        another = self.new_runtime()
        another.tick()
        self.assertEqual(another.status()["state"], "stopped")
        another.submit(payload=self.payload("auto", manual=False),
                       command_id="spot-auto", cloud_config=self.config)
        with self.assertRaisesRegex(RuntimeError, "already ended locally"):
            another.submit(payload=self.payload("charge_grid"),
                           command_id="replayed-start", cloud_config=self.config)
        another.submit(payload=self.payload("auto"),
                       command_id="user-auto", cloud_config=self.config)
        self.clock.advance(1)  # a fresh command is created after the stop barrier
        another.submit(payload=self.payload("charge_grid"),
                       command_id="fresh-start", cloud_config=self.config)
        self.assertEqual(another.status()["state"], "active")

    def test_failed_apply_cleans_up_then_latches_the_request(self):
        self.inverter.start_error = RuntimeError("mode write rejected")
        with self.assertRaisesRegex(RuntimeError, "mode write rejected"):
            self.submit()
        self.assertEqual(self.inverter.calls[-1]["action"], "auto")
        self.assertEqual(self.runtime.status()["state"], "stopped")
        self.inverter.start_error = None
        with self.assertRaisesRegex(RuntimeError, "already ended locally"):
            self.submit()

    def test_failed_stop_keeps_owner_and_retries_only_auto(self):
        self.submit()
        self.inverter.stop_error = RuntimeError("remote still enabled")
        with self.assertRaisesRegex(RuntimeError, "STOP is unverified"):
            self.submit("auto")
        self.assertEqual(self.runtime.status()["state"], "stop_pending")
        self.assertFalse(self.runtime.status()["verified"])
        before = len(self.inverter.calls)
        self.runtime.tick()
        self.assertEqual(len(self.inverter.calls), before)
        self.inverter.stop_error = None
        self.clock.advance(21)
        self.runtime.tick()
        self.assertEqual(self.runtime.status()["state"], "stopped")
        self.assertEqual(self.inverter.calls[-1]["action"], "auto")

    def test_reboot_restores_auto_without_resuming_old_power(self):
        self.submit()
        restarted = self.new_runtime()
        restarted.tick()
        self.assertEqual(self.inverter.calls[-1]["action"], "auto")
        self.assertEqual(restarted.status()["reason"], "process_restart")
        with self.assertRaisesRegex(RuntimeError, "already ended locally"):
            restarted.submit(payload=self.payload(), command_id="retry", cloud_config=self.config)

    def test_changed_device_route_stops_the_original_device(self):
        self.submit()
        self.config["module_runtime_configurations"][0]["communicator_id"] = "usb-two"
        self.runtime.tick()
        self.assertEqual(self.inverter.calls[-1]["action"], "auto")
        self.assertEqual(self.inverter.calls[-1]["cloud_config"]["module_runtime_configurations"][0]["communicator_id"], "usb-one")

    def test_invalid_and_expired_payloads_never_write(self):
        for extra in ({"battery_capacity_kwh": float("nan")}, {"target_soc_percent": float("inf")},
                      {"valid_until": (self.clock() - timedelta(seconds=1)).isoformat()}):
            with self.assertRaises(ValueError):
                self.submit(**extra)
        self.assertEqual(self.inverter.calls, [])

    def test_unverified_driver_result_is_a_failure_and_auto_cleanup(self):
        self.inverter.verified = False
        with self.assertRaisesRegex(RuntimeError, "did not verify"):
            self.submit()
        self.assertEqual(self.inverter.calls[-1]["action"], "auto")

    def test_source_drift_stops_an_active_request(self):
        self.submit()
        self.runtime.feature_guard = lambda: "changed-feature"
        with self.assertRaisesRegex(RuntimeError, "implementation changed"):
            self.runtime.tick()
        self.assertEqual(self.inverter.calls[-1]["action"], "auto")
        self.assertEqual(self.runtime.status()["state"], "stopped")

    def test_stop_is_available_even_if_feature_guard_rejects_code(self):
        self.submit()
        def reject():
            raise RuntimeError("hash mismatch")
        self.runtime.feature_guard = reject
        self.submit("auto")
        self.assertEqual(self.runtime.status()["state"], "stopped")

    def test_automatic_interval_can_start_after_previous_lease_expires(self):
        self.submit(manual=False)
        self.clock.advance(181)
        self.runtime.tick()
        self.submit(manual=False)
        self.assertEqual(self.runtime.status()["state"], "active")

    def test_clock_rollback_stops_instead_of_extending_lease(self):
        self.submit()
        self.clock.advance(-60)
        self.runtime.tick()
        self.assertEqual(self.runtime.status()["reason"], "clock_moved_backwards")

    def test_persisted_control_config_contains_no_device_token(self):
        self.config["device_token"] = "should-not-persist"
        self.submit()
        self.assertNotIn("should-not-persist", self.path.read_text())
        self.assertEqual(json.loads(self.path.read_text())["schema_version"], 1)

    def test_spot_interval_cannot_erase_a_terminated_manual_session(self):
        old = self.payload()
        self.submit()
        self.inverter.start_error = BatteryControlSafetyStop("minimum_soc_reached", 42)
        self.runtime.tick()
        self.inverter.start_error = None
        self.submit("charge_grid", manual=False)
        with self.assertRaisesRegex(RuntimeError, "already ended locally"):
            self.runtime.submit(payload=old, command_id="delayed", cloud_config=self.config)

    def test_delayed_manual_refresh_cannot_rearm_after_user_stop(self):
        old = self.payload()
        self.submit()
        self.clock.advance(1)
        self.submit("auto")
        with self.assertRaisesRegex(RuntimeError, "before the last explicit STOP"):
            self.runtime.submit(payload=old, command_id="delayed", cloud_config=self.config)

    def test_failed_persistence_after_start_immediately_stops(self):
        real_save = self.runtime._save
        def save(state):
            if state.get("state") == "active":
                raise OSError("disk full")
            real_save(state)
        with patch.object(self.runtime, "_save", side_effect=save):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.submit()
        self.assertEqual(self.inverter.calls[-1]["action"], "auto")

    def test_disk_failure_during_restart_never_resumes_previous_power(self):
        self.submit()
        restarted = self.new_runtime()
        with patch.object(restarted, "_save", side_effect=OSError("read-only disk")):
            with self.assertRaisesRegex(RuntimeError, "could not be saved"):
                restarted.tick()
            restarted.tick()
        self.assertEqual([c["action"] for c in self.inverter.calls], ["discharge_grid", "auto"])

    def test_start_hash_rejection_does_not_touch_an_unowned_inverter(self):
        def reject():
            raise RuntimeError("hash mismatch")
        self.runtime.feature_guard = reject
        with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
            self.submit()
        self.assertEqual(self.inverter.calls, [])

    def test_normal_refresh_respects_stop_retry_backoff(self):
        self.submit()
        self.inverter.stop_error = RuntimeError("stop rejected")
        with self.assertRaises(RuntimeError):
            self.submit("auto")
        calls = len(self.inverter.calls)
        with self.assertRaisesRegex(RuntimeError, "backoff"):
            self.submit()
        self.assertEqual(len(self.inverter.calls), calls)

    def foxess_config(self):
        return {"module_runtime_configurations": [{"module_key": "photovoltaic",
            "manufacturer": "foxess", "model": "H3-10.0-E", "communicator_id": "fox-usb",
            "modbus_device_id": 247, "profile_id": "foxess_h3_10_0_e", "communication_type": "rs485"}]}

    def canary_result(self):
        auto = {"verified": True, "registers": {"remote_enable": 0, "remote_active_power_w": 0}}
        phases = []
        for action, sign in (("discharge_grid", 1), ("charge_grid", -1)):
            phases.append({"action": action, "status": "passed", "requested_setpoint_w": sign * 500,
                "samples": [{"readback_verified": True, "physical_power_within_tolerance": True,
                    "registers": {"battery_power_w": sign * 400, "remote_enable": 1,
                                  "remote_active_power_w": sign * 500, "work_mode": 2}} for _ in range(3)]})
        return {"status": "passed", "verified": True, "profile_id": "foxess_h3_10_0_e",
                "verification_scope": "bidirectional_500w_and_stop", "phases": phases,
                "preflight": {"auto": auto}, "between_phases_auto": auto, "final_auto": auto, "error": None}

    def test_canary_does_not_pin_an_incomplete_pass_claim(self):
        result = self.runtime.canary(callback=lambda **kw: {"status": "passed", "verified": False},
                                     cloud_config=self.foxess_config(), command_id="canary-1", expected_runtime_fingerprint="feature-1")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["verified"])
        self.assertFalse((self.path.parent / "manual_battery_verified_fingerprint.json").exists())

    def test_complete_physical_canary_pins_only_its_profile_and_scope(self):
        pins = {"schema_version": 1, "profiles": {"goodwe_et": {"status": "pending"}}}
        pin_path = self.path.parent / "manual_battery_verified_fingerprint.json"
        pin_path.write_text(json.dumps(pins))
        result = self.runtime.canary(callback=lambda **kw: self.canary_result(),
                                     cloud_config=self.foxess_config(), command_id="canary-1", expected_runtime_fingerprint="feature-1")
        self.assertEqual(result["protection"]["status"], "locked")
        saved = json.loads(pin_path.read_text())["profiles"]
        self.assertEqual(saved["goodwe_et"], {"status": "pending"})
        self.assertEqual(saved["foxess_h3_10_0_e"]["verification_scope"], "bidirectional_500w_and_stop")

    def test_canary_fingerprint_mismatch_is_rejected_before_physical_io(self):
        calls = []
        with self.assertRaisesRegex(RuntimeError, "selected runtime fingerprint"):
            self.runtime.canary(callback=lambda **kw: calls.append(kw),
                cloud_config=self.foxess_config(), command_id="wrong-build-canary",
                expected_runtime_fingerprint="different-build")
        self.assertEqual(calls, [])
        self.assertEqual(self.inverter.calls, [])
        self.assertFalse(self.path.exists())
        self.assertFalse((self.path.parent / "manual_battery_verified_fingerprint.json").exists())

    def test_failed_final_auto_makes_canary_failed_and_unverified(self):
        self.inverter.stop_error = RuntimeError("stop rejected")
        result = self.runtime.canary(callback=lambda **kw: self.canary_result(),
                                     cloud_config=self.foxess_config(), command_id="canary-1", expected_runtime_fingerprint="feature-1")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["verified"])
        self.assertFalse((self.path.parent / "manual_battery_verified_fingerprint.json").exists())

    def test_failed_canary_preserves_previous_manual_safety_latches(self):
        self.submit()
        self.inverter.start_error = BatteryControlSafetyStop("minimum_soc_reached", 42)
        self.runtime.tick()
        self.inverter.start_error = None
        self.runtime.canary(callback=lambda **kw: {"status": "failed"},
                            cloud_config=self.foxess_config(), command_id="canary-1", expected_runtime_fingerprint="feature-1")
        with self.assertRaisesRegex(RuntimeError, "already ended locally"):
            self.submit()

    def test_unsupported_deye_start_does_not_persist_or_send_auto(self):
        self.config["module_runtime_configurations"][0].update(manufacturer="deye", model="Deye 50 hybrid HV")
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            self.submit()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.inverter.calls, [])


if __name__ == "__main__":
    unittest.main()
