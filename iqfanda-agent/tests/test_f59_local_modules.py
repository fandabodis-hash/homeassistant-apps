"""F59: offline module boundary, isolation and zero legacy takeover."""
from __future__ import annotations

import unittest
from local_modules.host import run_once
from local_modules.registry import REGISTERED_LOCAL_TICKS


class LocalModuleHostTests(unittest.TestCase):
    def test_nothing_migrated_is_noop_and_does_not_read_network_or_config(self):
        self.assertEqual(REGISTERED_LOCAL_TICKS, {})
        result = run_once(
            config_loader=lambda: self.fail("must not load config"),
            identity_loader=lambda: self.fail("must not load identity"),
        )
        self.assertEqual(result["status"], "no_migrated_modules")

    def test_only_active_device_uuid_module_executes_from_cached_data(self):
        calls = []
        handlers = {"heat_pump": lambda config: calls.append(config["device_uuid"]),
                    "photovoltaic": lambda config: self.fail("inactive")}
        cfg = {"device_uuid": "uuid-a", "active_modules": ["heat_pump"]}
        result = run_once(registry=handlers, config_loader=lambda: cfg,
                          identity_loader=lambda: {"device_uuid": "uuid-a"})
        self.assertEqual(calls, ["uuid-a"])
        self.assertEqual(result["executed"], ["heat_pump"])

    def test_foreign_device_uuid_never_executes(self):
        called = []
        result = run_once(registry={"heat_pump": lambda _: called.append(1)},
                          config_loader=lambda: {"device_uuid": "uuid-b", "active_modules": ["heat_pump"]},
                          identity_loader=lambda: {"device_uuid": "uuid-a"})
        self.assertEqual(result["status"], "device_identity_or_cached_config_invalid")
        self.assertEqual(called, [])

    def test_absent_or_disabled_module_is_not_started(self):
        calls = []
        run_once(registry={"heat_pump": lambda _: calls.append(1)},
                 config_loader=lambda: {"device_uuid": "uuid-a", "active_modules": [
                     {"module_key": "heat_pump", "enabled": False}]},
                 identity_loader=lambda: {"device_uuid": "uuid-a"})
        self.assertEqual(calls, [])

    def test_cloud_module_name_is_not_code_execution(self):
        calls = []
        result = run_once(registry={"safe": lambda _: calls.append(1)},
                          config_loader=lambda: {"device_uuid": "uuid-a", "active_modules": [
                              "os.system('dangerous')", "safe"]},
                          identity_loader=lambda: {"device_uuid": "uuid-a"})
        self.assertEqual(result["executed"], ["safe"])
        self.assertEqual(calls, [1])

    def test_config_unavailable_no_handler_executes(self):
        called = []
        result = run_once(registry={"heat_pump": lambda _: called.append(1)},
                          config_loader=lambda: None,
                          identity_loader=lambda: {"device_uuid": "uuid-a"})
        self.assertEqual(result["status"], "device_identity_or_cached_config_invalid")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
