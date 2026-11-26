import unittest
from datetime import datetime, timezone
from copy import deepcopy
from heating_circuit_controller import decide

# Synthetic test values, never client settings.
BASE=dict(pump_mode='auto',thermostat_mode='comfort',season_mode='auto',outdoor_threshold_c=18,
          levels_c=dict(frost=7,setback=18,comfort=22,boost=24),
          week=dict(workdays=[dict(at='06:00',temperature_c=21),dict(at='22:00',temperature_c=17)],weekend=[dict(at='08:00',temperature_c=23),dict(at='23:00',temperature_c=19)]))
LIMITS=dict(room_min_c=3,room_max_c=30,outdoor_min_c=5,outdoor_max_c=30)
ARGS=dict(limits=LIMITS,now=datetime(2026,11,25,12,tzinfo=timezone.utc),room_c=21.8,outdoor_c=10,room_fresh=True,outdoor_fresh=True,pump_on=False,enabled=True,identity_ok=True,bindings_verified=True,exclusive_owner=True,safety_allows_on=True,boost_authorized=True)

def d(cfg=None, **kw):
    args=dict(ARGS,**kw)
    return decide(deepcopy(BASE if cfg is None else cfg),**args)

def cfg(**kw): return dict(deepcopy(BASE),**kw)

class Tests(unittest.TestCase):
    def test_01_default_disabled(self): self.assertIsNone(decide(BASE,limits=LIMITS,now=ARGS['now']).desired_on)
    def test_02_identity(self): self.assertIsNone(d(identity_ok=False).desired_on)
    def test_03_mapping(self): self.assertIsNone(d(bindings_verified=False).desired_on)
    def test_04_owner(self): self.assertIsNone(d(exclusive_owner=False).desired_on)
    def test_05_readback(self): self.assertIsNone(d(pump_on=None).desired_on)
    def test_06_off(self): self.assertFalse(d(cfg(pump_mode='off'),pump_on=True).desired_on)
    def test_07_manual_on_hot(self): self.assertTrue(d(cfg(pump_mode='manual',season_mode='summer'),outdoor_c=32,room_c=None,room_fresh=False,outdoor_fresh=False).desired_on)
    def test_08_manual_safety(self): self.assertFalse(d(cfg(pump_mode='manual'),safety_allows_on=False).desired_on)
    def test_09_auto_on_boundary(self): self.assertTrue(d(room_c=21.8).desired_on)
    def test_10_auto_below(self): self.assertTrue(d(room_c=21.79).desired_on)
    def test_11_auto_off_boundary(self): self.assertFalse(d(room_c=22,pump_on=True).desired_on)
    def test_12_auto_above(self): self.assertFalse(d(room_c=22.1,pump_on=True).desired_on)
    def test_13_deadband_off(self): self.assertFalse(d(room_c=21.9,pump_on=False).desired_on)
    def test_14_deadband_on(self): self.assertTrue(d(room_c=21.9,pump_on=True).desired_on)
    def test_15_outdoor_above(self): self.assertFalse(d(outdoor_c=18.01,room_c=10,pump_on=True).desired_on)
    def test_16_outdoor_equal(self): self.assertFalse(d(outdoor_c=18,room_c=10,pump_on=True).desired_on)
    def test_17_outdoor_below(self): self.assertTrue(d(outdoor_c=17.99,room_c=10).desired_on)
    def test_18_winter_no_bypass(self): self.assertFalse(d(cfg(season_mode='winter'),outdoor_c=19).desired_on)
    def test_19_summer_block(self): self.assertFalse(d(cfg(season_mode='summer'),outdoor_c=-5).desired_on)
    def test_20_room_stale(self): self.assertFalse(d(room_fresh=False,pump_on=True).desired_on)
    def test_21_outdoor_stale(self): self.assertFalse(d(outdoor_fresh=False,pump_on=True).desired_on)
    def test_22_invalid_room(self): self.assertFalse(d(room_c=float('nan'),pump_on=True).desired_on)
    def test_23_frost(self): self.assertTrue(d(cfg(thermostat_mode='frost'),room_c=6.8).desired_on)
    def test_24_setback(self): self.assertFalse(d(cfg(thermostat_mode='setback'),room_c=18,pump_on=True).desired_on)
    def test_25_boost(self): self.assertTrue(d(cfg(thermostat_mode='boost'),room_c=23.8).desired_on)
    def test_26_boost_not_authorized(self): self.assertFalse(d(cfg(thermostat_mode='boost'),boost_authorized=False).desired_on)
    def test_27_weekday(self): self.assertEqual(d(cfg(thermostat_mode='schedule')).target_c,21)
    def test_28_weekend(self): self.assertEqual(d(cfg(thermostat_mode='schedule'),now=datetime(2026,11,28,12,tzinfo=timezone.utc)).target_c,23)
    def test_29_monday_carry_sunday(self): self.assertEqual(d(cfg(thermostat_mode='schedule'),now=datetime(2026,11,23,3,tzinfo=timezone.utc)).target_c,19)
    def test_30_saturday_carry_friday(self): self.assertEqual(d(cfg(thermostat_mode='schedule'),now=datetime(2026,11,28,3,tzinfo=timezone.utc)).target_c,17)
    def test_31_incomplete_row(self): self.assertFalse(d(cfg(thermostat_mode='schedule',week={'workdays':[{'at':'06:00'}],'weekend':[]})).desired_on)
    def test_32_duplicate_time(self):
        w=deepcopy(BASE['week']);w['weekend'].append(w['weekend'][-1]);self.assertFalse(d(cfg(thermostat_mode='schedule',week=w)).desired_on)
    def test_33_six_rows(self):
        w=deepcopy(BASE['week']);w['workdays']=[{'at':f'{x:02d}:00','temperature_c':21} for x in range(6)];self.assertFalse(d(cfg(thermostat_mode='schedule',week=w)).desired_on)
    def test_34_no_limits(self): self.assertFalse(d(limits={}).desired_on)
    def test_35_target_change(self): self.assertFalse(d(cfg(thermostat_mode='setback'),room_c=20,pump_on=True).desired_on)
    def test_36_invalid_config(self): self.assertFalse(d(cfg(pump_mode='x')).desired_on)
    def test_37_room_not_required_off(self): self.assertFalse(d(cfg(pump_mode='off'),room_fresh=False,outdoor_fresh=False).desired_on)
    def test_38_timezone_summer(self): self.assertEqual(d(cfg(thermostat_mode='schedule'),now=datetime(2026,7,4,6,tzinfo=timezone.utc)).target_c,23)
    def test_39_other_guard(self): self.assertIsNone(d(cfg(pump_mode='manual'),enabled=False).desired_on)
    def test_40_safety_auto(self): self.assertFalse(d(safety_allows_on=False,pump_on=True).desired_on)

if __name__=='__main__': unittest.main(verbosity=2)
