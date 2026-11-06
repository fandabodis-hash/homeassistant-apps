"""F55 R4: bounded, read-only commissioning evidence from the actual addon.

This command NEVER claims outputs, modifies options/configuration, restarts a
service, or calls turn_on/turn_off. Its result is NOT an activation permit.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import uuid

VERSION = '0.1.172'
COMMAND = 'local_thermostat_preflight_v1'

class PreflightError(ValueError):
    pass

def canonical_uuid(value):
    if not isinstance(value, str):
        raise PreflightError('INVALID_UUID')
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise PreflightError('INVALID_UUID') from exc
    return value

def inspect(*, identity, payload):
    import thermostat_runtime as runtime
    import zigbee_manager as ha
    if not isinstance(payload, dict) or set(payload) - {
        'device_uuid','request_id','target_ids','expected_config_version','expected_sensor_devices'}:
        raise PreflightError('INVALID_PAYLOAD_FIELDS')
    uid = canonical_uuid(payload.get('device_uuid'))
    request_id = canonical_uuid(payload.get('request_id'))
    if uid != str(identity.get('device_uuid')) or uid != str(runtime.identity().get('device_uuid')):
        raise PreflightError('FOREIGN_DEVICE')
    selected = payload.get('target_ids')
    if not isinstance(selected, list) or not 1 <= len(selected) <= 8:
        raise PreflightError('INVALID_TARGET_SELECTION')
    if len({canonical_uuid(x) for x in selected}) != len(selected):
        raise PreflightError('DUPLICATE_TARGET')
    expected_revision = payload.get('expected_config_version')
    if type(expected_revision) is not int or expected_revision < 1:
        raise PreflightError('EXPECTED_CONFIG_VERSION_REQUIRED')
    sensors = payload.get('expected_sensor_devices')
    if not isinstance(sensors, dict) or set(sensors) != set(selected):
        raise PreflightError('EXACT_SENSOR_DEVICE_BINDINGS_REQUIRED')
    for reg in sensors.values():
        if not isinstance(reg, str) or not 10 <= len(reg) <= 128:
            raise PreflightError('INVALID_SENSOR_DEVICE_ID')
    contracts, cached_uid, revision = runtime.cached_contracts(selected)
    if cached_uid != uid or revision != expected_revision:
        raise PreflightError('CONFIG_REVISION_NOT_ACKNOWLEDGED')
    mode, _, max_age = runtime.options()
    now = datetime.now(timezone.utc)
    installed = Path('/app/VERSION').read_text(encoding='utf-8-sig').strip()
    machine = platform.machine().lower()
    checks = []
    for c in contracts:
        row = {'target_id':c['target_id'], 'checks_passed':True, 'errors':[], 'outputs':[]}
        sensor = c['temperature_entity']
        observed_reg = ha.get_entity_device_id(entity_id=sensor)
        row['sensor_device_matches'] = observed_reg == sensors[c['target_id']]
        if not row['sensor_device_matches']:
            row['errors'].append('SENSOR_REGISTRY_MISMATCH')
        state = ha.get_entity_state(sensor)
        row['temperature_entity'] = sensor
        row['last_reported'] = state.get('last_reported')
        row['last_updated'] = state.get('last_updated')
        row['sensor_max_age_seconds'] = max_age
        row['observation_timestamp_field'] = 'last_reported' if state.get('last_reported') else 'last_updated'
        try:
            row['temperature_c'] = runtime.sensor_value(state, max_age, now)
        except (ValueError, TypeError, KeyError):
            row['temperature_c'] = None
            row['errors'].append('SENSOR_VALUE_UNIT_OR_FRESHNESS_INVALID')
        for output in c['outputs']:
            ref = output['reference']
            local_reg = ha.get_entity_device_id(entity_id=ref)
            observed = ha.get_entity_state(ref)
            item = {'reference':ref, 'registry_matches':local_reg == output['device_reg_id'],
                    'state':observed.get('state')}
            row['outputs'].append(item)
            if not item['registry_matches']:
                row['errors'].append('OUTPUT_REGISTRY_MISMATCH')
            if item['state'] not in ('on','off'):
                row['errors'].append('OUTPUT_FEEDBACK_UNAVAILABLE')
        row['all_outputs_currently_off'] = all(x['state'] == 'off' for x in row['outputs'])
        row['checks_passed'] = not row['errors']
        row['contract_sha256'] = hashlib.sha256(json.dumps(c,sort_keys=True,allow_nan=False,
                                   separators=(',',':')).encode()).hexdigest()
        checks.append(row)
    return {'schema_version':1, 'command':COMMAND, 'request_id':request_id, 'device_uuid':uid,
            'config_version':revision, 'observed_at':now.isoformat(), 'installed_version':installed,
            'architecture':machine, 'native_arm64':machine in ('aarch64','arm64'), 'mode':mode,
            'checks_passed':installed == VERSION and machine in ('aarch64','arm64') and
                            bool(checks) and all(c['checks_passed'] for c in checks),
            'targets':checks, 'actuation':False, 'ownership_changed':False,
            'handover_authorized':False, 'scope':'READ_ONLY_FIELD_EVIDENCE_NOT_ACTIVATION'}

def execute(*, identity, command_id, command_payload, submit_result):
    try:
        result = inspect(identity=identity, payload=command_payload)
        ok = result['checks_passed']
        submit_result(identity=identity, command_id=command_id,
                      status='succeeded' if ok else 'failed',
                      result={'worker':'command_worker','executor':COMMAND,
                              'phase':'read_only_preflight_complete','actuation':False,
                              'preflight':result},
                      error_message=None if ok else 'F55 local preflight checks not passed; no activation.')
    except Exception as exc:
        # Never echo adapter exceptions: HTTP errors may contain credentials.
        code = str(exc) if isinstance(exc, PreflightError) else type(exc).__name__
        submit_result(identity=identity, command_id=command_id,status='failed',
                      result={'worker':'command_worker','executor':COMMAND,
                              'phase':'read_only_preflight_failed','reason':code,'actuation':False},
                      error_message='F55 local preflight rejected; no actuation.')
