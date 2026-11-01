"""Offline behavioral tests of the scoped, read-only F53 energy audit."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "f53_energy_context.py"
_SPEC = importlib.util.spec_from_file_location("f53_energy_context", _PATH)
energy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(energy)


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name, "eq", value

    def in_(self, values):
        return self.name, "in", tuple(values)


class Device:
    serial_number = Column("serial_number")


class InstallationModule:
    site_id = Column("site_id")
    module_key = Column("module_key")


class ModuleEntityState:
    installation_module_id = Column("installation_module_id")
    source_device_id = Column("source_device_id")
    entity_key = Column("entity_key")


class Query:
    def __init__(self, model):
        self.model, self.filters, self.maximum = model, [], None

    def where(self, *filters):
        self.filters.extend(filters)
        return self

    def limit(self, maximum):
        self.maximum = maximum
        return self


class Session:
    def __init__(self, owner):
        self.owner, self.events, self.closed = owner, [], False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def execute(self, sql):
        self.events.append(sql)

    def rows(self, query):
        self.events.append(query)
        if self.owner.fail_reads and query.model is ModuleEntityState:
            raise RuntimeError("postgres://private-password@private-host/db")
        rows = self.owner.data[query.model]
        for key, operation, value in query.filters:
            rows = [row for row in rows if (getattr(row, key) == value if operation == "eq"
                                            else getattr(row, key) in value)]
        return rows if query.maximum is None else rows[:query.maximum]

    def scalar(self, query):
        rows = self.rows(query)
        return rows[0] if rows else None

    def scalars(self, query):
        rows = self.rows(query)
        return SimpleNamespace(all=lambda: rows)


class EnergyContextTests(unittest.TestCase):
    def setUp(self):
        self.stamp = datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc)
        self.device = SimpleNamespace(id="device-7", site_id="site-7", serial_number="F800711-TNG-00007")
        self.module = SimpleNamespace(id="pv-7", site_id="site-7", module_key="photovoltaic")
        self.sessions, self.fail_reads = [], False
        self.data = {Device: [self.device], InstallationModule: [self.module],
                     ModuleEntityState: [self.row(key) for key in energy.ENTITY_SPECS]}
        self.base = SimpleNamespace(SessionLocal=self.session, select=Query, text=lambda sql: sql,
                                    Device=Device, InstallationModule=InstallationModule,
                                    ModuleEntityState=ModuleEntityState, now=lambda: self.stamp,
                                    aware=lambda value: value.replace(tzinfo=timezone.utc)
                                    if value.tzinfo is None else value.astimezone(timezone.utc))
        self.base.selected = lambda db: db.scalar(Query(Device).where(
            Device.serial_number == "F800711-TNG-00007"))

    def session(self):
        session = Session(self)
        self.sessions.append(session)
        return session

    def row(self, key="baterie.vykon", **changes):
        unit, sign = energy.ENTITY_SPECS.get(key, ("W", None))
        values = dict(installation_module_id="pv-7", source_device_id="device-7", entity_key=key,
                      value=-500 if key == "baterie.vykon" else 50, unit=unit, quality="good",
                      source="inverter_profile", captured_at=self.stamp - timedelta(seconds=10),
                      received_at=self.stamp - timedelta(seconds=2), updated_at=self.stamp,
                      attributes={"sign_convention": sign, "private": "never-export-attributes"})
        values.update(changes)
        return SimpleNamespace(**values)

    def capture(self):
        return energy.capture_energy_context(self.base, "before")

    def entity(self, result, key="baterie.vykon"):
        return next(row for row in result["entities"] if row["entity_key"] == key)

    def test_exact_identity_module_and_whitelist_in_new_read_only_transactions(self):
        self.data[Device].insert(0, SimpleNamespace(id="device-18", site_id="site-18", serial_number="F800711-TNG-00018"))
        self.data[InstallationModule].insert(0, SimpleNamespace(id="pv-18", site_id="site-18", module_key="photovoltaic"))
        self.data[InstallationModule].insert(0, SimpleNamespace(id="heating-7", site_id="site-7", module_key="heat_pump"))
        self.data[ModuleEntityState].extend([
            self.row(value=9999, source_device_id="device-18"),
            self.row(value=8888, installation_module_id="pv-18"),
            self.row("private.unrelated_sensor", value=7777),
        ])
        first = self.capture()
        self.assertEqual((first["serial"], first["device_id"], first["installation_module_id"]),
                         (self.device.serial_number, "device-7", "pv-7"))
        self.assertEqual(self.entity(first)["value"], -500)
        self.assertEqual(len(first["entities"]), 7)
        self.data[ModuleEntityState][4].value = -450
        second = energy.capture_energy_context(self.base, "after")
        self.assertEqual(self.entity(second)["value"], -450)
        self.assertEqual(len(self.sessions), 2)
        for session in self.sessions:
            self.assertTrue(session.closed)
            self.assertEqual(session.events[0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            query = next(event for event in session.events if isinstance(event, Query)
                         and event.model is ModuleEntityState)
            self.assertEqual(query.filters, [("installation_module_id", "eq", "pv-7"),
                ("source_device_id", "eq", "device-7"), ("entity_key", "in", tuple(energy.ENTITY_SPECS))])
        self.assertTrue(first["context_only"])
        self.assertFalse(first["concurrent_measurement_confirmed"])

    def test_missing_module_missing_entity_null_and_real_zero_remain_distinct(self):
        self.data[InstallationModule] = []
        absent = self.capture()
        self.assertEqual(absent["status"], "photovoltaic_module_missing")
        self.assertTrue(all(not row["present"] and row["value"] is None for row in absent["entities"]))
        self.data[InstallationModule] = [self.module]
        self.data[ModuleEntityState] = [self.row("pv1.vykon", value=0), self.row("baterie.soc", value=None)]
        result = self.capture()
        zero = self.entity(result, "pv1.vykon")
        self.assertEqual(zero["value"], 0)
        self.assertTrue(zero["usable_numeric_context"])
        null = self.entity(result, "baterie.soc")
        self.assertTrue(null["present"])
        self.assertIn("value_missing", null["issues"])
        missing = self.entity(result, "smartmeter.vykon_celkem")
        self.assertFalse(missing["present"])
        self.assertIsNone(missing["value"])
        self.assertIsNone(missing["capture_age_seconds"])

    def test_fresh_ingestion_does_not_hide_stale_capture_and_source_is_retained(self):
        self.data[ModuleEntityState] = [self.row(captured_at=self.stamp - timedelta(seconds=181),
            received_at=self.stamp - timedelta(seconds=1), updated_at=self.stamp, source="inverter_profile")]
        row = self.entity(self.capture())
        self.assertEqual(row["value"], -500)
        self.assertEqual(row["capture_timestamp_status"], "stale")
        self.assertEqual((row["capture_age_seconds"], row["receive_age_seconds"], row["ingestion_lag_seconds"]), (181, 1, 180))
        self.assertEqual(row["source"], "inverter_profile")
        self.assertEqual(row["updated_at"], self.stamp.isoformat())
        self.assertFalse(row["usable_numeric_context"])

    def test_observation_clock_is_taken_after_the_database_reads(self):
        stamps = iter((self.stamp, self.stamp + timedelta(seconds=180)))
        self.base.now = lambda: next(stamps)
        result = self.capture()
        self.assertEqual(result["query_started_at_utc"], self.stamp.isoformat())
        self.assertEqual(result["observed_at_utc"], (self.stamp + timedelta(seconds=180)).isoformat())
        self.assertEqual(self.entity(result)["capture_age_seconds"], 190)
        self.assertEqual(self.entity(result)["capture_timestamp_status"], "stale")
        self.assertFalse(self.entity(result)["usable_numeric_context"])

    def test_boolean_strings_and_nonfinite_numbers_never_become_zero_or_json_nan(self):
        for value in (True, False, "0", "-500", "", float("nan"), float("inf"), -float("inf"), {}, []):
            with self.subTest(value=repr(value)):
                self.data[ModuleEntityState] = [self.row(value=value)]
                result = self.capture()
                row = self.entity(result)
                self.assertIsNone(row["value"])
                self.assertFalse(row["usable_numeric_context"])
                self.assertIn("value_not_finite_number", row["issues"])
                json.dumps(result, allow_nan=False)
        self.data[ModuleEntityState] = [self.row(value=-0.0)]
        self.assertEqual(str(self.entity(self.capture())["value"]), "-0.0")

    def test_wrong_units_and_bad_quality_are_preserved_without_conversion(self):
        for changes, issue in (({"unit": "kW"}, "unit_mismatch"), ({"unit": None}, "unit_mismatch"),
                               ({"quality": "warning"}, "quality_not_good"),
                               ({"quality": "unavailable"}, "quality_not_good"),
                               ({"source": ""}, "source_missing")):
            with self.subTest(changes=changes):
                self.data[ModuleEntityState] = [self.row(**changes)]
                row = self.entity(self.capture())
                self.assertEqual(row["value"], -500)
                for key, value in changes.items():
                    self.assertEqual(row[key], value)
                self.assertIn(issue, row["issues"])
                self.assertFalse(row["usable_numeric_context"])

    def test_missing_naive_future_and_boundary_capture_times_are_not_false_fresh(self):
        for captured, expected in (
            (None, "missing"), (self.stamp.replace(tzinfo=None), "timezone_missing"),
            (self.stamp + timedelta(microseconds=1), "future"),
            (self.stamp - timedelta(seconds=180), "recent_timestamp"),
            (self.stamp - timedelta(seconds=180, microseconds=1), "stale"),
        ):
            with self.subTest(expected=expected):
                self.data[ModuleEntityState] = [self.row(captured_at=captured)]
                row = self.entity(self.capture())
                self.assertEqual(row["capture_timestamp_status"], expected)
                if expected != "recent_timestamp":
                    self.assertFalse(row["usable_numeric_context"])
        offset = (self.stamp - timedelta(seconds=10)).astimezone(timezone(timedelta(hours=2)))
        self.data[ModuleEntityState] = [self.row(captured_at=offset)]
        row = self.entity(self.capture())
        self.assertEqual(row["captured_at"], offset.isoformat())
        self.assertEqual(row["capture_age_seconds"], 10)
        self.assertTrue(row["usable_numeric_context"])
        self.data[ModuleEntityState] = [self.row(received_at=self.stamp - timedelta(seconds=11))]
        row = self.entity(self.capture())
        self.assertEqual(row["ingestion_lag_seconds"], -1)
        self.assertTrue(row["clock_uncertain"])
        self.assertFalse(row["usable_numeric_context"])

    def test_only_reported_known_sign_metadata_is_exported_and_attributes_stay_private(self):
        result = self.capture()
        self.assertEqual(self.entity(result)["sign_convention"], "negative_charging_positive_discharging")
        self.assertNotIn("never-export-attributes", json.dumps(result))
        self.assertNotIn("attributes", self.entity(result))
        self.data[ModuleEntityState] = [self.row(attributes={"sign_convention": "private-unknown-sign"})]
        result = self.capture()
        row = self.entity(result)
        self.assertIsNone(row["sign_convention"])
        self.assertEqual(row["sign_convention_status"], "unexpected")
        self.assertFalse(row["usable_numeric_context"])
        self.assertNotIn("private-unknown-sign", json.dumps(result))
        self.data[ModuleEntityState] = [self.row(attributes={})]
        row = self.entity(self.capture())
        self.assertEqual(row["sign_convention_status"], "missing")
        self.assertFalse(row["usable_numeric_context"])

    def test_ambiguous_duplicate_state_is_not_arbitrarily_selected(self):
        self.data[ModuleEntityState] = [self.row(value=-500), self.row(value=500)]
        row = self.entity(self.capture())
        self.assertIsNone(row["value"])
        self.assertFalse(row["usable_numeric_context"])
        self.assertIn("ambiguous_entity", row["issues"])
        self.data[ModuleEntityState] = [self.row(value=number) for number in range(15)]
        result = self.capture()
        self.assertEqual(result["status"], "energy_context_read_failed")
        self.assertTrue(all(not row["present"] for row in result["entities"]))

    def test_database_failure_closes_transaction_and_returns_no_secrets_or_partial_values(self):
        self.fail_reads = True
        result = self.capture()
        self.assertEqual(result["status"], "energy_context_read_failed")
        self.assertTrue(self.sessions[0].closed)
        self.assertTrue(all(row["value"] is None and not row["present"] for row in result["entities"]))
        self.assertNotIn("private-password", json.dumps(result))
        self.assertNotIn("private-host", json.dumps(result))
        self.base.now = lambda: self.stamp.replace(tzinfo=None)
        result = self.capture()
        self.assertEqual(result["status"], "energy_context_read_failed")
        self.assertIsNone(result["observed_at_utc"])


if __name__ == "__main__":
    unittest.main()
