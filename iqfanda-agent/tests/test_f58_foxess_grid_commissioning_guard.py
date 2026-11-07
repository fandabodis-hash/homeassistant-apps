"""F58/F59: local no-export acceptance tests, no real Home Assistant or Modbus."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from local_modules.foxess_commissioning_grid_guard import (
    LocalGridImportGuard, GridGuardUnavailable, PROTECTED_UUID_00007,
    require_protected_00007_discharge_safety,
)


class Stream(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class LocalGridSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = self.root / "device.json"
        self.mapping = self.root / "grid_guard.json"
        self.identity.write_text(json.dumps({"device_uuid":PROTECTED_UUID_00007,"device_token":"PRIVATE_TOKEN"}))
        self.config = {"schema_version":1,"device_uuid":PROTECTED_UUID_00007,
            "entity_id":"sensor.physically_verified_grid_import_w",
            "physically_verified":True,"independent_grid_meter":True,
            "hardware_zero_export_verified":True,"positive_import_watts":True,
            "evidence_id":"commissioning-meter-confirmation"}
        self.mapping.write_text(json.dumps(self.config))
        self.now = datetime(2026,11,7,18,0,0,tzinfo=timezone.utc)
        self.watts = 1200
        self.age = 1
        self.calls=[]

    def opener(self, request, *, timeout):
        self.calls.append({"url":request.full_url,"timeout":timeout})
        result={"entity_id":self.config["entity_id"],"state":str(self.watts),
            "last_updated":(self.now-timedelta(seconds=self.age)).isoformat(),
            "attributes":{"unit_of_measurement":"W"}}
        return Stream(json.dumps(result).encode())

    def guard(self):
        return LocalGridImportGuard(
            config_path=self.mapping, identity_path=self.identity,
            token="fixture-token", opener=self.opener, clock=lambda:self.now)

    def test_read_only_verified_local_meter_permits_preflight_not_actuation(self):
        guard=self.guard()
        self.assertEqual(guard.check_before_discharge(),1200.0)
        self.assertEqual(guard.check_during_discharge(),1200.0)
        self.assertEqual(len(self.calls),2)
        self.assertTrue(all(x["url"].startswith("http://supervisor/core/api/states/sensor.") for x in self.calls))

    def test_insufficient_import_denied(self):
        self.watts=790
        with self.assertRaises(GridGuardUnavailable):
            self.guard().check_before_discharge()
        self.watts=299
        with self.assertRaises(GridGuardUnavailable):
            self.guard().check_during_discharge()

    def test_export_is_never_accepted(self):
        self.watts=-10
        with self.assertRaises(GridGuardUnavailable):
            self.guard().check_during_discharge()

    def test_stale_data_is_denied(self):
        self.age=4
        with self.assertRaises(GridGuardUnavailable):
            self.guard().check_before_discharge()

    def test_future_meter_data_denied(self):
        self.age=-3
        with self.assertRaises(GridGuardUnavailable):
            self.guard().check_before_discharge()

    def test_unverified_hardware_export_limit_denied(self):
        self.config["hardware_zero_export_verified"]=False
        self.mapping.write_text(json.dumps(self.config))
        with self.assertRaises(GridGuardUnavailable):
            self.guard()

    def test_wrong_device_uuid_denied(self):
        self.config["device_uuid"]="11111111-1111-1111-1111-111111111111"
        self.mapping.write_text(json.dumps(self.config))
        with self.assertRaises(GridGuardUnavailable):
            self.guard()

    def test_missing_mapping_denied(self):
        self.mapping.unlink()
        with self.assertRaises(GridGuardUnavailable):
            self.guard()

    def test_missing_auth_denied(self):
        with patch.dict("os.environ",{"SUPERVISOR_TOKEN":""}):
            with self.assertRaises(GridGuardUnavailable):
                LocalGridImportGuard(config_path=self.mapping,identity_path=self.identity)

    def test_wrong_measurement_unit_denied(self):
        old=self.opener
        def wrong(request,*,timeout):
            data={"entity_id":self.config["entity_id"],"state":"1200",
                  "last_updated":self.now.isoformat(),
                  "attributes":{"unit_of_measurement":"kW"}}
            return Stream(json.dumps(data).encode())
        with self.assertRaises(GridGuardUnavailable):
            LocalGridImportGuard(config_path=self.mapping,identity_path=self.identity,
                token="fixture-token",opener=wrong,clock=lambda:self.now).check_before_discharge()

    def test_normal_00018_is_not_forced_to_use_00007_mapping(self):
        self.identity.write_text(json.dumps({"device_uuid":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}))
        # Does not create or inspect a fake meter for a different installation.
        require_protected_00007_discharge_safety(
            identity_path=self.identity,config_path=self.root/"nonexistent.json"
        )

    def test_initial_requires_800_but_renewal_only_300_reserve(self):
        self.watts = 450
        meter=self.guard()
        with patch("local_modules.foxess_commissioning_grid_guard.LocalGridImportGuard",
                   return_value=meter):
            with self.assertRaises(GridGuardUnavailable):
                require_protected_00007_discharge_safety(
                    identity_path=self.identity,config_path=self.mapping,renewal=False
                )
            require_protected_00007_discharge_safety(
                identity_path=self.identity,config_path=self.mapping,renewal=True
            )

    def test_00007_enforces_local_mapping_before_discharge(self):
        self.mapping.unlink()
        with self.assertRaises(GridGuardUnavailable):
            require_protected_00007_discharge_safety(
                identity_path=self.identity,config_path=self.mapping
            )


if __name__=="__main__":
    unittest.main()
