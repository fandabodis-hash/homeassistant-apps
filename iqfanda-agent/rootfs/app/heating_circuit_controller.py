"""F114_R16: pure local heating-circuit decision core; NO I/O or actuation.
Pilot 00015. A future adapter must verify physical bindings, own the relay,
read fresh local sensors and confirmed relay state, and explicitly arm.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from math import isfinite

HYSTERESIS_C = Decimal('0.2')
LEVELS = ('frost', 'setback', 'comfort', 'boost')
GROUPS = ('workdays', 'weekend')
ZONE = ZoneInfo('Europe/Prague')

class ConfigError(ValueError):
    pass

@dataclass(frozen=True)
class Decision:
    desired_on: bool | None  # None = no ownership/actuation authorization
    reason: str
    target_c: float | None = None
    season_allows_auto: bool | None = None


def decimal_number(value):
    if type(value) not in (int, float, Decimal):
        raise ConfigError('number_required')
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ConfigError('invalid_number')
    if not d.is_finite():
        raise ConfigError('non_finite_number')
    return d


def temp(value, minimum, maximum):
    d = decimal_number(value)
    if not minimum <= d <= maximum:
        raise ConfigError('temperature_outside_verified_limits')
    return d


def parse_time(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ':' or not value[:2].isascii() or not value[3:].isascii() or not value[:2].isdigit() or not value[3:].isdigit():
        raise ConfigError('time_HH_MM_required')
    h,m = int(value[:2]),int(value[3:])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ConfigError('time_outside_day')
    return h*60+m


def validate_schedule(schedule, lo, hi):
    if not isinstance(schedule, dict) or set(schedule) != set(GROUPS):
        raise ConfigError('two_day_groups_required')
    out = {}
    for group in GROUPS:
        rows=schedule[group]
        if not isinstance(rows,list) or not 1 <= len(rows) <= 5:
            raise ConfigError('one_to_five_complete_rows_per_group')
        parsed=[]
        for row in rows:
            if not isinstance(row,dict) or set(row) != {'at','temperature_c'}:
                raise ConfigError('complete_time_temperature_row_required')
            parsed.append((parse_time(row['at']),temp(row['temperature_c'],lo,hi)))
        if any(a[0]>=b[0] for a,b in zip(parsed,parsed[1:])):
            raise ConfigError('times_must_be_unique_and_increasing')
        out[group]=parsed
    return out


def scheduled_target(week, now):
    if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ConfigError('aware_timestamp_required')
    local=now.astimezone(ZONE)
    group='workdays' if local.weekday()<5 else 'weekend'
    minute=local.hour*60+local.minute
    current=[t for at,t in week[group] if at<=minute]
    if current:
        return current[-1]
    yesterday=local.date()-timedelta(days=1)
    return week['workdays' if yesterday.weekday()<5 else 'weekend'][-1][1]


def validate_auto_config(cfg, limits):
    if not isinstance(cfg,dict) or not isinstance(limits,dict):
        raise ConfigError('configuration_required')
    lo,hi=decimal_number(limits.get('room_min_c')),decimal_number(limits.get('room_max_c'))
    olo,ohi=decimal_number(limits.get('outdoor_min_c')),decimal_number(limits.get('outdoor_max_c'))
    if not lo < hi or not olo < ohi:
        raise ConfigError('installation_limits_invalid')
    level=cfg.get('thermostat_mode')
    if level not in (*LEVELS,'schedule'):
        raise ConfigError('thermostat_mode_required')
    # Explicit mode is supplied by the thermostat owner. A boost request is
    # not a substitute for a verified, separately authorized FVE boost.
    levels=cfg.get('levels_c')
    if not isinstance(levels,dict) or set(levels)!=set(LEVELS):
        raise ConfigError('four_temperature_levels_required')
    values={k:temp(v,lo,hi) for k,v in levels.items()}
    if not values['frost']<=values['setback']<=values['comfort']<=values['boost']:
        raise ConfigError('levels_not_monotonic')
    week=validate_schedule(cfg.get('week'),lo,hi) if level=='schedule' else None
    threshold=temp(cfg.get('outdoor_threshold_c'),olo,ohi)
    if cfg.get('season_mode') not in ('auto','winter','summer'):
        raise ConfigError('season_mode_required')
    return level, values, week, threshold


def decide(cfg, *, limits, now, room_c=None, outdoor_c=None, room_fresh=False,
           outdoor_fresh=False, pump_on=None, enabled=False, identity_ok=False,
           bindings_verified=False, exclusive_owner=False, safety_allows_on=False,
           boost_authorized=False):
    """No calls, no file/DB writes. Adapter must distinguish desired from actual.

    enabled: explicit activation after verified install. No activation by default.
    pump_on: confirmed local read-back, not previous requested state.
    safety_allows_on: verified independent equipment protections.
    Any mode with unverified relay/owner -> no command. On invalid AUTO sensor or
    config, request OFF only for a verified owned relay; future adapter must
    enforce equipment-specific safe-stop/frost policy before physical actuation.
    """
    if not all(x is True for x in (enabled,identity_ok,bindings_verified,exclusive_owner)):
        return Decision(None,'not_armed_or_identity_binding_owner_unverified')
    if type(pump_on) is not bool:
        return Decision(None,'relay_readback_unknown')
    if not isinstance(cfg,dict):
        return Decision(False,'invalid_config')
    mode=cfg.get('pump_mode')
    if mode=='off':
        return Decision(False,'manual_off')
    if mode not in ('manual','auto'):
        return Decision(False,'invalid_pump_mode')
    if safety_allows_on is not True:
        return Decision(False,'equipment_safety_block')
    if mode=='manual':
        return Decision(True,'manual_on')
    try:
        level, values, week, threshold=validate_auto_config(cfg,limits)
        if cfg['season_mode']=='summer':
            return Decision(False,'summer_auto_block',season_allows_auto=False)
        # Winter never bypasses the absolute threshold specified by the owner.
        if outdoor_fresh is not True:
            return Decision(False,'outdoor_temperature_unavailable',season_allows_auto=False)
        outdoor=decimal_number(outdoor_c)
        if outdoor>=threshold:
            return Decision(False,'outdoor_threshold_auto_block',season_allows_auto=False)
        if level=='boost' and boost_authorized is not True:
            return Decision(False,'fve_boost_not_authorized',season_allows_auto=True)
        target=scheduled_target(week,now) if level=='schedule' else values[level]
        if room_fresh is not True:
            return Decision(False,'room_temperature_unavailable',float(target),True)
        actual=decimal_number(room_c)
        if actual<=target-HYSTERESIS_C:
            return Decision(True,'below_target_minus_hysteresis',float(target),True)
        if actual>=target:
            return Decision(False,'target_reached',float(target),True)
        return Decision(pump_on,'inside_hysteresis_keep_confirmed_state',float(target),True)
    except (ConfigError,TypeError,ValueError,OverflowError):
        return Decision(False,'invalid_auto_configuration_or_measurement')
