"""TNG IQ FANDA F55: deterministic local thermostat, no network or hardware I/O.
Candidate component 1.0 for Agent 0.1.171. All timestamps explicitly UTC-aware.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo
import re

DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
LEVELS = {'frost': (3, 10), 'setback': (15, 80), 'comfort': (18, 80), 'boost': (20, 80)}
ENTITY = re.compile(r'^switch\.[a-z0-9_]+$')
SENSOR = re.compile(r'^sensor\.[a-z0-9_]+$')

class ContractError(ValueError):
    pass

def number(value: Any, name: str, lo: float, hi: float) -> float:
    if isinstance(value, bool):
        raise ContractError(name + ': boolean is not a number')
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ContractError(name + ': missing/invalid number') from exc
    if not isfinite(out) or not lo <= out <= hi:
        raise ContractError(name + ': number outside allowed range')
    return out

def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError('timezone-aware clock required')
    return value.astimezone(timezone.utc)

def validate_program(value: Any) -> dict:
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ContractError('weekly schema_version must be 1')
    if type(value.get('enabled')) is not bool:
        raise ContractError('weekly enabled must be boolean')
    try:
        ZoneInfo(value['timezone'])
    except Exception as exc:
        raise ContractError('invalid program timezone') from exc
    levels = value.get('levels_c')
    if not isinstance(levels, dict) or set(levels) != set(LEVELS):
        raise ContractError('exactly four temperature levels required')
    for key, limits in LEVELS.items():
        number(levels[key], 'levels_c.' + key, *limits)
    week = value.get('week')
    if not isinstance(week, dict) or set(week) != set(DAYS):
        raise ContractError('exactly seven days required')
    count = 0
    for day in DAYS:
        events = week[day]
        if not isinstance(events, list) or len(events) > 48:
            raise ContractError('invalid daily events')
        seen = set()
        for event in events:
            if not isinstance(event, dict) or set(event) != {'at', 'level'}:
                raise ContractError('invalid schedule event')
            at = str(event['at'])
            if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', at) or at in seen:
                raise ContractError('invalid/duplicate schedule time')
            if event['level'] not in ('frost', 'setback', 'comfort'):
                raise ContractError('boost is SOC-controlled, not scheduled')
            seen.add(at)
            count += 1
    if not count:
        raise ContractError('empty weekly schedule')
    override = value.get('boost_soc')
    if override is not None:
        if not isinstance(override, dict) or set(override) != {'enable_percent','disable_percent'}:
            raise ContractError('invalid per-target boost SOC override')
        on = number(override['enable_percent'], 'SOC on', 0, 100)
        off = number(override['disable_percent'], 'SOC off', 0, 100)
        if off >= on:
            raise ContractError('SOC off must be below SOC on')
    return deepcopy(value)

def schedule_level(program: dict, now: datetime) -> str:
    local = utc(now).astimezone(ZoneInfo(program['timezone']))
    minute = local.hour * 60 + local.minute
    for offset in range(8):
        day = DAYS[(local.weekday() - offset) % 7]
        events = sorted(program['week'][day], key=lambda x: x['at'], reverse=True)
        for e in events:
            hh, mm = map(int, e['at'].split(':'))
            if offset or hh * 60 + mm <= minute:
                return e['level']
    raise ContractError('no preceding schedule event')

def compile_targets(cloud: dict, identity: dict, selected: list[str]) -> list[dict]:
    """Explicitly selected verified generic thermostats only; no serial/model forks.

    Configuration does not grant local authority by itself. The runtime requires
    an explicit local mode, target selection and a persisted ownership claim.
    """
    if not isinstance(identity, dict) or not identity.get('device_uuid'):
        raise ContractError('missing local device identity')
    if not isinstance(cloud, dict) or cloud.get('device_uuid') != identity['device_uuid']:
        raise ContractError('foreign-device configuration')
    if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
        raise ContractError('explicit unique target selection required')
    modules = [m for m in cloud.get('module_runtime_configurations', [])
               if isinstance(m, dict) and m.get('module_key') == 'pv_surplus_control']
    if len(modules) != 1 or not isinstance(modules[0].get('configuration'), dict):
        raise ContractError('ambiguous/missing pv_surplus_control runtime')
    cfg = modules[0]['configuration']
    if cfg.get('actuation_enabled') is not True:
        raise ContractError('actuation disabled; no automatic ownership transfer')
    targets = cfg.get('targets')
    programs = cfg.get('weekly_temperature_programs')
    if not isinstance(targets, list) or not isinstance(programs, dict):
        raise ContractError('missing targets/programs')
    result = []
    claimed: set[str] = set()
    for tid in selected:
        match = [t for t in targets if isinstance(t, dict) and t.get('id') == tid]
        if len(match) != 1:
            raise ContractError('target missing or duplicated: ' + tid)
        t = match[0]
        if t.get('type') != 'generic_load' or t.get('enabled') is not True or t.get('configuration_status') != 'verified':
            raise ContractError('target is not an enabled verified generic thermostat')
        p = validate_program(programs.get(tid))
        if p['enabled'] is not True:
            raise ContractError('weekly program is not enabled')
        role = t.get('weekly_control_output')
        output = t.get('output')
        if not isinstance(role, dict) or not isinstance(output, dict):
            raise ContractError('missing original output/weekly actuation role')
        if role.get('status') != 'verified' or role.get('role') not in ('thermostat_permission', 'direct_heating_load'):
            raise ContractError('unverified heating role')
        if output.get('status') != 'verified' or role.get('reference') != output.get('reference'):
            raise ContractError('weekly output differs from verified telemetry output')
        outputs = t.get('weekly_control_outputs', [role])
        if not isinstance(outputs, list) or not 1 <= len(outputs) <= 3:
            raise ContractError('one to three verified outputs required')
        if outputs[0] != role:
            raise ContractError('first fan-out output must equal original role contract')
        refs = []
        for o in outputs:
            if not isinstance(o, dict) or o.get('status') != 'verified' or o.get('role') != role['role']:
                raise ContractError('every fan-out output must be separately verified')
            ref = str(o.get('reference') or '')
            if not ENTITY.fullmatch(ref) or not re.fullmatch(r'[A-Za-z0-9_-]{10,128}', str(o.get('device_reg_id') or '')):
                raise ContractError('invalid output entity or hardware registry identity')
            if ref in refs or ref in claimed:
                raise ContractError('duplicate output ownership')
            refs.append(ref)
            for other in targets:
                if not isinstance(other, dict) or other.get('id') == tid or other.get('enabled') is not True:
                    continue
                oo = [other.get('output'), other.get('weekly_control_output')]
                oo.extend(other.get('weekly_control_outputs') or [])
                if any(isinstance(x, dict) and x.get('reference') == ref for x in oo):
                    raise ContractError('output shared with another active target')
        sensors = [s for s in t.get('sensors', []) if isinstance(s, dict)
                   and s.get('role') == 'temperature' and s.get('status') == 'verified']
        if len(sensors) != 1 or not SENSOR.fullmatch(str(sensors[0].get('reference') or '')):
            raise ContractError('one verified room-temperature sensor required')
        c = t.get('conditions') or {}
        lo = number(c.get('minimum_temperature_c'), 'minimum', 0, 95)
        hi = number(c.get('maximum_temperature_c'), 'maximum', 0, 95)
        hy = number(c.get('hysteresis_c'), 'hysteresis', 0.1, 1.0)
        if lo >= hi:
            raise ContractError('invalid safety temperature limits')
        if any(not lo <= float(v) <= hi for v in p['levels_c'].values()):
            raise ContractError('program exceeds existing safety limits')
        soc = p.get('boost_soc') or (cfg.get('surplus_source') or {}).get('battery_soc') or {}
        on = number(soc.get('enable_percent', soc.get('enable_soc_percent')), 'SOC on', 0, 100)
        off = number(soc.get('disable_percent', soc.get('disable_soc_percent')), 'SOC off', 0, 100)
        if off >= on:
            raise ContractError('invalid SOC hysteresis')
        confirm = number((cfg.get('surplus_source') or {}).get('confirmation_seconds', 30), 'confirmation_seconds', 30, 3600)
        result.append({'device_uuid': identity['device_uuid'], 'target_id': tid,
                       'program': p, 'temperature_entity': sensors[0]['reference'],
                       'outputs': deepcopy(outputs), 'minimum_c': lo, 'maximum_c': hi,
                       'hysteresis_c': hy, 'soc_on': on, 'soc_off': off,
                       'confirmation_seconds': confirm, 'minimum_off_seconds': 300})
        claimed.update(refs)
    return result

def initial_state(now_mono: float) -> dict:
    return {'temperature_demand': False, 'boost': False, 'confirmation_since': None,
            'off_since': float(now_mono), 'last_mono': float(now_mono), 'actual_on': False}

def decide(contract: dict, state: dict | None, *, now: datetime, monotonic: float,
           temperature: float | None, sensor_fresh: bool, actual_on: bool,
           soc: float | None = None, soc_fresh: bool = False,
           safety_ok: bool = True) -> tuple[dict, dict]:
    """Pure deterministic decision. Does NOT claim ownership or operate a relay.

    Monotonic counters are never restored across process restarts. The caller
    must confirm all outputs OFF at first takeover and before restarting timers.
    """
    now = utc(now)
    mono = number(monotonic, 'monotonic', 0, 1e15)
    s = deepcopy(state) if state is not None else initial_state(mono)
    if mono < s.get('last_mono', mono):
        s = initial_state(mono)
    if s.get('actual_on') is True and not actual_on:
        s['off_since'] = mono
    s['last_mono'] = mono
    s['actual_on'] = bool(actual_on)
    p = contract['program']
    scheduled = schedule_level(p, now)
    soc_available = bool(soc_fresh and soc is not None)
    if soc_available:
        try:
            val = number(soc, 'SOC', 0, 100)
        except ContractError:
            soc_available = False
    if not soc_available:
        boost = False
    else:
        boost = True if val >= contract['soc_on'] else False if val <= contract['soc_off'] else bool(s['boost'])
    level = 'boost' if boost else scheduled
    setpoint = float(p['levels_c'][level])
    result = {'requested_c': setpoint, 'scheduled_level': scheduled, 'active_level': level,
              'temperature_c': temperature, 'boost': boost, 'desired_on': False,
              'reason': '', 'local_timestamp': now.astimezone(ZoneInfo(p['timezone'])).isoformat(),
              'soc_available': soc_available}
    s['boost'] = boost
    block = None
    if not safety_ok:
        block = 'independent_safety_interlock'
    elif not p.get('enabled'):
        block = 'program_disabled'
    elif not sensor_fresh or temperature is None:
        block = 'temperature_unavailable_or_stale'
    else:
        try:
            temperature = number(temperature, 'temperature', -50, 120)
        except ContractError:
            block = 'invalid_temperature'
        else:
            if temperature >= contract['maximum_c']:
                block = 'safety_maximum_reached'
    if block:
        s['temperature_demand'] = False
        s['confirmation_since'] = None
        result['reason'] = block
        return result, s
    if temperature >= setpoint:
        demand, reason = False, 'setpoint_reached'
    elif temperature <= setpoint - contract['hysteresis_c']:
        demand, reason = True, 'below_hysteresis_start'
    else:
        demand, reason = bool(s['temperature_demand']), 'hysteresis_hold'
    s['temperature_demand'] = demand
    if not demand:
        s['confirmation_since'] = None
    else:
        if s['confirmation_since'] is None:
            s['confirmation_since'] = mono
        if actual_on:
            result['desired_on'] = True
        elif mono - s['off_since'] < contract['minimum_off_seconds']:
            reason = 'minimum_off_300_seconds'
        elif mono - s['confirmation_since'] < contract['confirmation_seconds']:
            reason = 'confirmation_pending'
        else:
            result['desired_on'] = True
    result['reason'] = reason
    return result, s

def action_plan(contract: dict, decision: dict, output_states: dict[str, str]) -> list[dict]:
    """Returns an intended plan only. Hardware execution requires separate gate.

    Any unknown feedback prevents ON of every channel. OFF is always planned for
    all verified channels. A production adapter must verify every read-back and
    roll all channels back OFF if even one ON fails.
    """
    refs = [o['reference'] for o in contract['outputs']]
    on = decision.get('desired_on') is True
    if on and any(output_states.get(r) not in ('on', 'off') for r in refs):
        on = False
    return [{'entity_id': r, 'target_state': 'on' if on else 'off',
             'device_reg_id': o['device_reg_id']}
            for r, o in zip(refs, contract['outputs'])]
