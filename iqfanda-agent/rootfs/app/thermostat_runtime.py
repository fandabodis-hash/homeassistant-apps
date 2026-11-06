"""F55 local thermostat runtime candidate. Cloud is NOT used by the control loop.

Production activation is deliberately gated: options mode=active, explicit target
IDs, installer-verified contracts, local identity, persistent owner claim, sensor
freshness, and local registry/read-back checks. Default mode is disabled.
"""
from __future__ import annotations
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
import hashlib
import json
import logging
import os
import threading
import time

from thermostat_engine import ContractError, action_plan, compile_targets, decide, initial_state, number

LOCK = threading.RLock()
LOCAL_CALL = ContextVar('f55_local_thermostat_call', default=False)
ROOT = Path('/config/local-thermostat')
OWNERS = {}  # Reserved until a separate, explicit ownership handback succeeds.
STATES = {}
STATUS = {'schema_version': 1, 'executor': 'addon', 'component': 'F55_LOCAL_THERMOSTAT_V1',
          'candidate': True, 'mode': 'disabled', 'targets': [], 'physical_control_active': False}
SOC = {'value': None, 'received_mono': None}
INSTALLED = False
BOOT_MODE = None
BOOTSTRAPPED = set()
LAST_SAVE = 0.0


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic_json(path, value):
    """Durable same-directory atomic replace, restrictive permissions."""
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=True, allow_nan=False, sort_keys=True)
            out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def identity():
    data = read_json(os.getenv('IQF_DEVICE_CONFIG_PATH', '/config/device.json'))
    if not isinstance(data, dict) or not data.get('device_uuid'):
        raise ContractError('local identity unavailable')
    return data


def options():
    data = read_json('/data/options.json')
    mode = data.get('local_thermostat_mode', 'disabled')
    selected = data.get('local_thermostat_targets', [])
    age = number(data.get('local_thermostat_sensor_max_age_seconds', 1800), 'sensor age', 180, 7200)
    if mode not in ('disabled','shadow','active') or not isinstance(selected, list):
        raise ContractError('invalid local thermostat options')
    return mode, selected, age


def cached_contracts(selected):
    device = identity()
    cloud = read_json(os.getenv('IQF_CLOUD_CONFIG_PATH', '/config/cloud-config.json'))
    contracts = compile_targets(cloud, device, selected)
    revision = cloud.get('config_version')
    return contracts, device['device_uuid'], revision


def persist_owners(device_uuid):
    atomic_json(ROOT / 'ownership.json', {'schema_version': 1, 'device_uuid': device_uuid,
                                         'outputs': OWNERS})


def reserve(contracts, device_uuid):
    proposed = dict(OWNERS)
    for c in contracts:
        for output in c['outputs']:
            ref = output['reference']
            expected = {'target_id': c['target_id'], 'device_reg_id': output['device_reg_id']}
            existing = proposed.get(ref)
            if existing is not None and existing != expected:
                raise ContractError('ownership conflict/rebinding requires explicit handover')
            proposed[ref] = expected
    atomic_json(ROOT / 'ownership.json', {'schema_version': 1, 'device_uuid': device_uuid, 'outputs': proposed})
    OWNERS.clear(); OWNERS.update(proposed)


def load_reservations():
    p = ROOT / 'ownership.json'
    if not p.exists():
        return
    doc = read_json(p)
    if doc.get('schema_version') != 1 or doc.get('device_uuid') != identity()['device_uuid']:
        raise ContractError('persisted ownership belongs to another device/schema')
    rows = doc.get('outputs')
    if not isinstance(rows, dict):
        raise ContractError('invalid persisted ownership')
    from thermostat_engine import ENTITY
    for ref, item in rows.items():
        if not ENTITY.fullmatch(ref) or not isinstance(item, dict) or not item.get('device_reg_id'):
            raise ContractError('invalid persisted output identity')
    OWNERS.update(rows)


def _active_commission_marker(*, selected, uid, revision, contracts):
    p = ROOT / 'commission.json'
    if not p.exists():
        raise ContractError('active mode requires explicit F55 R6 commissioning marker')
    doc = read_json(p)
    expected_targets = sorted(str(x) for x in selected)
    expected_outputs = sorted(o['reference'] for c in contracts for o in c['outputs'])
    if (doc.get('schema_version') != 1 or doc.get('device_uuid') != uid
            or sorted(str(x) for x in (doc.get('target_ids') or [])) != expected_targets
            or doc.get('config_version') != revision
            or sorted(str(x) for x in (doc.get('outputs') or [])) != expected_outputs):
        raise ContractError('commission marker does not match active local contract')
    if doc.get('phase') not in ('commission_pending_restart', 'active'):
        raise ContractError('commission marker phase invalid')
    return doc


def _complete_handback_if_requested(zm, mode):
    p = ROOT / 'handback.json'
    if not p.exists():
        return False
    try:
        doc = read_json(p)
        if mode != 'disabled' or doc.get('schema_version') != 1:
            raise ContractError('handback marker requires disabled mode')
        if doc.get('device_uuid') != identity()['device_uuid']:
            raise ContractError('handback marker belongs to another device')
        expected = sorted(str(x) for x in (doc.get('outputs') or []))
        if expected != sorted(OWNERS):
            raise ContractError('handback output set does not match ownership')
        for ref, owner in list(OWNERS.items()):
            if zm.get_entity_device_id(entity_id=ref) != owner.get('device_reg_id'):
                raise ContractError('handback registry identity changed: ' + ref)
            if (zm.get_entity_state(ref) or {}).get('state') != 'off':
                raise ContractError('handback requires verified OFF: ' + ref)
        OWNERS.clear()
        for stale in (ROOT / 'ownership.json', ROOT / 'commission.json', ROOT / 'handback.json'):
            stale.unlink(missing_ok=True)
        STATUS.update(mode='disabled', physical_control_active=False, cloud_fence=False,
                      owner_outputs=[], reason='explicit_handback_completed')
        return True
    except Exception as exc:
        STATUS.update(mode='disabled', physical_control_active=False, cloud_fence=True,
                      owner_outputs=sorted(OWNERS), reason='handback_pending_fail_closed',
                      errors=[str(exc)])
        return False


def request_guard(original):
    @wraps(original)
    def guarded(*, path, method='GET', payload=None):
        # No behavior/timing change on devices without a claimed local thermostat.
        # New claims may only be acquired during startup, before legacy workers.
        if not OWNERS or str(method).upper() != 'POST':
            return original(path=path, method=method, payload=payload)
        # One lock covers permission check AND write for claimed devices.
        with LOCK:
            bits = path.strip('/').split('/')
            if not LOCAL_CALL.get() and str(method).upper() == 'POST' and len(bits) == 3:
                if bits[0] == 'services' and bits[1] in ('switch','light','input_boolean','homeassistant') and bits[2] in ('turn_on','turn_off','toggle') and OWNERS:
                    p = payload if isinstance(payload, dict) else {}
                    refs = p.get('entity_id')
                    if isinstance(refs, str):
                        refs = [x.strip() for x in refs.split(',')]
                    broad = not isinstance(refs, list) or not refs or 'all' in refs or bool(p.get('device_id') or p.get('area_id') or p.get('floor_id') or p.get('label_id'))
                    if broad or any(ref in OWNERS for ref in refs):
                        raise ContractError('F55: local thermostat owns output; cloud/legacy relay write rejected')
            return original(path=path, method=method, payload=payload)
    return guarded


def observe_soc(entities):
    # Observe EXISTING locally read Modbus data; never perform an extra bus read.
    rows = [e for e in entities if isinstance(e, dict) and e.get('entity_key') == 'baterie.soc'] if isinstance(entities, list) else []
    if len(rows) != 1 or rows[0].get('quality') not in ('good','ok'):
        return
    try:
        value = number(rows[0].get('value'), 'local SOC', 0, 100)
    except ContractError:
        return
    with LOCK:
        SOC.update(value=value, received_mono=time.monotonic())


def snapshot():
    with LOCK:
        return json.loads(json.dumps(STATUS, allow_nan=False))


def install():
    """Called before command-worker starts. No service call or relay write here."""
    global INSTALLED, BOOT_MODE
    if INSTALLED:
        return
    import zigbee_manager as zm
    import heartbeat as hb
    import telemetrie_modulu as tm
    with LOCK:
        # A corrupted prior claim must never silently restore cloud authority.
        load_reservations()
        mode, selected, _ = options()
        if mode == 'disabled' and (ROOT / 'handback.json').exists():
            _complete_handback_if_requested(zm, mode)
        BOOT_MODE = mode
        if mode == 'active':
            contracts, uid, revision = cached_contracts(selected)
            marker = _active_commission_marker(
                selected=selected, uid=uid, revision=revision, contracts=contracts)
            reserve(contracts, uid)
            marker = dict(marker)
            marker['phase'] = 'active'
            marker['activated_at'] = datetime.now(timezone.utc).isoformat()
            atomic_json(ROOT / 'commission.json', marker)
            STATUS.update(cloud_fence=True, owner_outputs=sorted(OWNERS),
                          selected_targets=sorted(str(x) for x in selected),
                          config_version=revision)
        elif OWNERS:
            STATUS.update(cloud_fence=True, owner_outputs=sorted(OWNERS))
        else:
            STATUS.update(cloud_fence=False, owner_outputs=[])
        zm.home_assistant_request = request_guard(zm.home_assistant_request)
        previous_heartbeat = hb.build_communication_state
        @wraps(previous_heartbeat)
        def heartbeat():
            result = previous_heartbeat()
            result['local_thermostat_runtime'] = snapshot()
            return result
        hb.build_communication_state = heartbeat
        previous_reader = tm.nacti_fve_telemetrii
        @wraps(previous_reader)
        def inverter_reader(*args, **kwargs):
            result = previous_reader(*args, **kwargs)
            observe_soc(result[1])
            return result
        tm.nacti_fve_telemetrii = inverter_reader
        INSTALLED = True
        STATUS['mode'] = mode
        STATUS['owner_outputs'] = sorted(OWNERS)
        STATUS['selected_targets'] = sorted(str(x) for x in selected)


def sensor_value(state, max_age, now):
    if not isinstance(state, dict) or state.get('state') in ('unknown','unavailable',None):
        raise ContractError('sensor unavailable')
    # HA last_updated may remain old for an unchanged value: prefer last_reported.
    stamp = state.get('last_reported') or state.get('last_updated')
    if not isinstance(stamp, str):
        raise ContractError('sensor has no observation timestamp')
    dt = datetime.fromisoformat(stamp.replace('Z','+00:00'))
    if dt.tzinfo is None:
        raise ContractError('sensor timestamp has no timezone')
    age = (now - dt.astimezone(timezone.utc)).total_seconds()
    if age < -30 or age > max_age:
        raise ContractError('temperature observation stale or from future')
    unit = (state.get('attributes') or {}).get('unit_of_measurement')
    if unit not in ('°C','C'):
        raise ContractError('temperature must be Celsius')
    return number(state.get('state'), 'temperature', -50, 120)


def set_outputs(contract, desired):
    """Verify registry on EVERY write, switch explicit channels, verify all feedback.

    On partial ON failure attempt verified OFF on all channels. Never bypass the
    physical equipment's independent safeties. Errors stay visible in heartbeat.
    """
    import zigbee_manager as zm
    refs = contract['outputs']
    def one(o, on):
        ref = o['reference']
        if zm.get_entity_device_id(entity_id=ref) != o['device_reg_id']:
            raise ContractError('local registry binding changed: ' + ref)
        target = 'on' if on else 'off'
        before = zm.get_entity_state(ref)
        if before.get('state') != target:
            token = LOCAL_CALL.set(True)
            try:
                zm.call_binary_power_output_service(entity_id=ref, desired_on=on)
            finally:
                LOCAL_CALL.reset(token)
        for _ in range(8):
            if zm.get_entity_state(ref).get('state') == target:
                return
            time.sleep(0.25)
        raise ContractError('output read-back failed: ' + ref)
    try:
        for output in refs:
            one(output, desired)
    except Exception as original:
        rollback_errors = []
        if desired:
            for output in refs:
                try:
                    one(output, False)
                except Exception as exc:
                    rollback_errors.append(str(exc))
        raise ContractError('actuation failed: ' + str(original) + '; rollback=' + repr(rollback_errors)) from original


def off_reserved(reason):
    errors = []
    # Do not release reservations when cloud configuration disappears or corrupts.
    for ref, owner in list(OWNERS.items()):
        try:
            set_outputs({'outputs': [{'reference': ref, **owner}]}, False)
        except Exception as exc:
            errors.append(str(exc))
    STATUS.update(physical_control_active=False, reason=reason, errors=errors)


def tick():
    global LAST_SAVE
    import zigbee_manager as zm
    with LOCK:
        now, mono = datetime.now(timezone.utc), time.monotonic()
        mode, selected, max_age = options()
        STATUS.update(mode=mode, evaluated_at=now.isoformat(), targets=[],
                      owner_outputs=sorted(OWNERS),
                      selected_targets=sorted(str(x) for x in selected),
                      cloud_fence=bool(OWNERS))
        if mode in ('disabled', 'shadow') and OWNERS:
            off_reserved('owner_retained_pending_explicit_handback')
            return
        if mode == 'disabled':
            STATUS['physical_control_active'] = False
            if OWNERS:
                off_reserved('disabled_but_owner_retained_pending_explicit_handback')
            return
        if mode == 'active' and BOOT_MODE != 'active':
            off_reserved('activation_requires_addon_restart_before_other_workers')
            return
        contracts, uid, revision = cached_contracts(selected)
        previous = None
        p = ROOT / 'last-good.json'
        if p.exists():
            previous = read_json(p)
            if previous.get('device_uuid') != uid:
                raise ContractError('last-good device mismatch')
            old_revision = previous.get('config_version')
            if type(old_revision) is int and type(revision) is int and revision < old_revision:
                raise ContractError('stale configuration revision refused')
        if mode == 'active':
            reserve_if_changed(contracts, uid)
        normalized = {'schema_version': 1, 'device_uuid': uid, 'config_version': revision, 'contracts': contracts}
        if normalized != previous:
            atomic_json(p, normalized)
        active_refs = {o['reference'] for c in contracts for o in c['outputs']}
        retired = [r for r in OWNERS if r not in active_refs]
        if retired:
            raise ContractError('removed/rebound output requires explicit handback; reserved OFF: ' + ','.join(retired))
        for c in contracts:
            tid = c['target_id']
            details = {'target_id': tid, 'executor': 'addon', 'mode': mode, 'config_version': revision}
            try:
                output_states = {o['reference']: zm.get_entity_state(o['reference']).get('state') for o in c['outputs']}
                actual = all(v == 'on' for v in output_states.values())
                if tid not in BOOTSTRAPPED:
                    STATES[tid] = initial_state(mono)
                    if mode == 'active':
                        set_outputs(c, False)
                        actual = False
                        output_states = {o['reference']: 'off' for o in c['outputs']}
                    BOOTSTRAPPED.add(tid)
                try:
                    temperature = sensor_value(zm.get_entity_state(c['temperature_entity']), max_age, now)
                    fresh, sensor_error = True, None
                except Exception as exc:
                    temperature, fresh, sensor_error = None, False, str(exc)
                soc_fresh = SOC['received_mono'] is not None and 0 <= mono - SOC['received_mono'] <= 180
                decision, state = decide(c, STATES[tid], now=now, monotonic=mono,
                                         temperature=temperature, sensor_fresh=fresh, actual_on=actual,
                                         soc=SOC['value'], soc_fresh=soc_fresh)
                plan = action_plan(c, decision, output_states)
                if any(v not in ('on','off') for v in output_states.values()):
                    state.update(off_since=mono, confirmation_since=None, actual_on=False)
                STATES[tid] = state
                if mode == 'active':
                    desired = all(x['target_state'] == 'on' for x in plan)
                    set_outputs(c, desired)
                    output_states = {o['reference']: zm.get_entity_state(o['reference']).get('state') for o in c['outputs']}
                    if desired != decision['desired_on']:
                        decision = dict(decision, desired_on=desired, reason='unknown_output_feedback_fail_safe_off')
                details.update(decision, sensor_error=sensor_error, command_plan=plan,
                               output_feedback=output_states, actuation_verified=mode == 'active')
            except Exception as exc:
                details.update(reason='local_control_error', error=str(exc), actuation_verified=False)
                if mode == 'active':
                    STATES[tid] = initial_state(mono)
                    try:
                        set_outputs(c, False)
                    except Exception as off_exc:
                        details['fail_safe_off_error'] = str(off_exc)
            STATUS['targets'].append(details)
        STATUS['physical_control_active'] = mode == 'active'
        STATUS['reason'] = 'local_evaluation_complete'
        if mono - LAST_SAVE >= 60:
            atomic_json(ROOT / 'status.json', STATUS)
            LAST_SAVE = mono


def reserve_if_changed(contracts, uid):
    expected = {o['reference']: {'target_id': c['target_id'], 'device_reg_id': o['device_reg_id']}
                for c in contracts for o in c['outputs']}
    if any(OWNERS.get(ref) != value for ref, value in expected.items()):
        raise ContractError('new or changed ownership requires explicit commissioning and addon restart')


def run_forever():
    install()
    while True:
        try:
            tick()
        except Exception as exc:
            logging.exception('F55 local thermostat evaluation failed')
            with LOCK:
                off_reserved(str(exc))
                STATUS['evaluated_at'] = datetime.now(timezone.utc).isoformat()
        time.sleep(5)
