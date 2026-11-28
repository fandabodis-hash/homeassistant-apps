"""F169: explicit one-shot zone 1 heating demand, ONLY FANDA 00019.
No startup action, scheduling, relay switching, setpoint/mode writes or retry.
Commission both transitions under supervision before ordinary client control.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from communication.midea_00019_cloud_probe import DEVICE_UUID, SITE_ID
from communication.midea_00019_readonly import SERIAL, FTDI_PORT, BAUDRATE, PARITY, STOP_BITS
from communication.midea_00019_live import verified_port
from communication.modbus_bus_lock import ziskej_zamek_modbus_sbernice

COMMAND = 'midea_00019_zone1_power_v1'
PROFILE = 'f169_midea_bms1_zone1_fc06_v1'
SUPPORTED_VERSIONS = ('0.1.181', '0.1.183', '0.1.184')
STATE = Path('/config/midea_00019_power_f169.json')
COOLDOWN = 30


class Rejected(RuntimeError):
    pass


def need(ok, reason):
    if not ok:
        raise Rejected(reason)


def utc(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        need(d.tzinfo is not None, 'INVALID_TIME')
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise Rejected('INVALID_TIME')


def now():
    return datetime.now(timezone.utc)


def exact(identity):
    return (isinstance(identity, dict) and str(identity.get('device_uuid', '')).lower() == DEVICE_UUID
            and str(identity.get('site_id', '')).lower() == SITE_ID)


def expiry(payload):
    need(0 < (utc(payload['expires_at']) - now()).total_seconds() <= 120, 'EXPIRED_OR_INVALID_TTL')


def validate(identity, command_id, payload):
    need(exact(identity), 'IDENTITY_MISMATCH')
    need(isinstance(payload, dict) and set(payload) == {
        'device_uuid', 'site_id', 'serial_number', 'profile', 'desired_on', 'expected_word',
        'commissioning', 'safety_confirmed', 'nonce', 'expires_at'}, 'INVALID_PAYLOAD')
    need((payload['device_uuid'], payload['site_id'], payload['serial_number'], payload['profile']) ==
         (DEVICE_UUID, SITE_ID, SERIAL, PROFILE), 'TARGET_MISMATCH')
    need(type(payload['desired_on']) is bool and type(payload['commissioning']) is bool
         and payload['safety_confirmed'] is True, 'CONFIRMATION_REQUIRED')
    need(type(payload['expected_word']) is int and payload['expected_word'] in (0, 2), 'UNSUPPORTED_ZONE_WORD')
    try:
        need(UUID(command_id) != UUID(str(payload['nonce'])), 'NONCE_INVALID')
    except (ValueError, TypeError, AttributeError):
        raise Rejected('NONCE_INVALID')
    expiry(payload)


def empty():
    return {'profile': PROFILE, 'off_verified': False, 'on_verified': False, 'last_attempt': None}


def load_state(path=STATE):
    if not path.exists():
        return empty()
    need(not path.is_symlink() and path.is_file() and path.stat().st_size <= 16384, 'JOURNAL_INVALID')
    state = json.loads(path.read_text('utf-8'))
    need(isinstance(state, dict) and set(state) == set(empty()) and state.get('profile') == PROFILE
         and type(state.get('off_verified')) is bool and type(state.get('on_verified')) is bool,
         'JOURNAL_INVALID')
    attempt = state['last_attempt']
    if attempt is not None:
        need(isinstance(attempt, dict) and set(attempt) == {'id', 'at', 'desired_on', 'phase'}
             and type(attempt['desired_on']) is bool and attempt['phase'] in ('attempted', 'confirmed', 'uncertain', 'observed_on'), 'JOURNAL_INVALID')
        UUID(attempt['id']); utc(attempt['at'])
    return state


def save_state(state, path=STATE):
    need(path.parent.is_dir() and not path.is_symlink(), 'JOURNAL_PATH_INVALID')
    fd, tmp = tempfile.mkstemp(prefix='.f169-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(state, f, sort_keys=True, separators=(',', ':')); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def commissioned(state):
    return state['off_verified'] is True and state['on_verified'] is True


def heartbeat_status(identity):
    if not exact(identity):
        return None
    try:
        s = load_state()
        a = s['last_attempt']
        return {'profile': PROFILE, 'commissioned': commissioned(s), 'off_verified': s['off_verified'],
                'on_verified': s['on_verified'], 'journal_valid': True,
                'last_attempt': a if a is not None else None}
    except Exception:
        return {'profile': PROFILE, 'commissioned': False, 'journal_valid': False}


def read_words(client, address, count):
    r = client.read_holding_registers(address=address, count=count, device_id=1)
    values = getattr(r, 'registers', None)
    need(r is not None and not r.isError() and getattr(r, 'function_code', None) == 3 and isinstance(values, list) and len(values) == count
         and all(type(w) is int and 0 <= w <= 65535 for w in values), 'FC03_READ_FAILED')
    return values


def signed(w):
    return w - 65536 if w >= 32768 else w


def production_identity(identity):
    from host.identity import DeviceIdentityService
    from runtime_config import load_device_identity
    from agent_updater import read_installed_agent_version
    return (exact(identity) and exact(load_device_identity())
            and DeviceIdentityService().get_serial_number() == SERIAL
            and read_installed_agent_version() in SUPPORTED_VERSIONS)


def execute(identity, command_id, payload, *, path=STATE, client_factory=None,
            identity_check=production_identity, port_check=verified_port,
            lock_factory=ziskej_zamek_modbus_sbernice, sleep=time.sleep):
    result = {'executor': COMMAND, 'profile': PROFILE, 'serial_number': SERIAL,
              'command_id': command_id, 'phase': 'rejected', 'writes_attempted': 0,
              'ack_verified': False, 'readback_verified': False, 'physical_transition': False,
              'desired_on': None, 'before_word': None, 'after_word': None, 'reason': None}
    c = None; bus = None; held = False; journal = None; state = None
    try:
        validate(identity, command_id, payload)
        result['desired_on'] = payload['desired_on']
        need(identity_check(identity) and port_check(identity), 'LOCAL_IDENTITY_OR_FTDI_MISMATCH')
        # Dedicated file lock + existing per-port thread lock; do not wait indefinitely.
        fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        journal = os.fdopen(fd, 'a+')
        fcntl.flock(journal, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = load_state(path)
        need(payload['commissioning'] or commissioned(state), 'NOT_COMMISSIONED')
        last = state['last_attempt']
        if last:
            need(last['id'] != command_id, 'REPLAY_REJECTED')
            need((now() - utc(last['at'])).total_seconds() >= COOLDOWN, 'COOLDOWN_OR_CLOCK_ERROR')
            # An uncertain OFF can be recovered by a fresh explicit supervised ON, never auto-retried.
            if last['phase'] not in ('confirmed', 'observed_on'):
                need(payload['commissioning'] and payload['desired_on'], 'UNCERTAIN_PREVIOUS_WRITE_SUPERVISED_ON_ONLY')
        bus = lock_factory(FTDI_PORT)
        held = bus.acquire(timeout=8)
        need(held, 'RS485_BUSY')
        expiry(payload)
        if client_factory is None:
            from pymodbus.client import ModbusSerialClient
            client_factory = ModbusSerialClient
        c = client_factory(port=FTDI_PORT, baudrate=BAUDRATE, bytesize=8,
                           parity=PARITY, stopbits=STOP_BITS, timeout=1.2, retries=0)
        need(c.connect() is True, 'FTDI_CONNECT_FAILED')
        before, mode = read_words(c, 0, 2)
        result['before_word'] = before
        need(mode == 3 and before in (0, 2) and before == payload['expected_word'], 'HEATING_MODE_OR_EXPECTED_STATE_CHANGED')
        desired = (before | 2) if payload['desired_on'] else (before & ~2)
        if desired == before:
            sleep(0.5); need(read_words(c, 0, 2) == [before, mode], 'STATE_CHANGED_DURING_READ')
            if last and last['phase'] not in ('confirmed', 'observed_on'):
                # Supervised recovery may observe ON already present. It does not prove a write.
                state['last_attempt'] = {'id': command_id, 'at': now().isoformat(), 'desired_on': True, 'phase': 'observed_on'}
                save_state(state, path)
            result.update(phase='already_set', after_word=before, readback_verified=True, confirmed_at=now().isoformat(), commissioned=commissioned(state))
            return result
        # Ordinary controller enable/disable only. For OFF, decline active
        # defrost, antifreeze, oil return/test mode or cold water. No forced writes.
        if not payload['desired_on']:
            sleep(0.12); status, loads = read_words(c, 128, 2)
            need(status != 65535 and loads != 65535 and not status & (2 | 4 | 8 | 32)
                 and not loads & (1 << 15), 'PROTECTION_ACTIVE_OR_UNKNOWN')
            sleep(0.12); temps = [signed(w) for w in read_words(c, 104, 2)]
            need(all(10 <= t <= 85 for t in temps), 'WATER_TEMPERATURE_GUARD')
        expiry(payload)
        need(identity_check(identity) and port_check(identity), 'LOCAL_IDENTITY_CHANGED')
        sleep(0.12); need(read_words(c, 0, 2) == [before, mode], 'STATE_CHANGED_BEFORE_WRITE')
        expiry(payload)
        # Persist BEFORE the only permitted FC06. A restart must not replay it.
        state['last_attempt'] = {'id': command_id, 'at': now().isoformat(), 'desired_on': payload['desired_on'], 'phase': 'attempted'}
        save_state(state, path)
        expiry(payload)
        result['writes_attempted'] = 1
        try:
            r = c.write_register(address=0, value=desired, device_id=1)
            result['ack_verified'] = bool(r is not None and not r.isError() and getattr(r, 'function_code', None) == 6
                                          and getattr(r, 'address', None) == 0 and getattr(r, 'registers', None) == [desired])
        except Exception:
            pass  # Still attempt read-back; never retry the write.
        sleep(0.6); first = read_words(c, 0, 2)
        sleep(0.6); second = read_words(c, 0, 2)
        result['after_word'] = second[0]
        need(result['ack_verified'] and first == second == [desired, mode], 'WRITE_OR_READBACK_NOT_CONFIRMED')
        state['last_attempt']['phase'] = 'confirmed'
        if payload['commissioning']:
            # Commissioning must finish with a verified restoration to ON.
            if payload['desired_on']:
                state['on_verified'] = state['off_verified'] is True
            else:
                state['off_verified'] = True; state['on_verified'] = False
        save_state(state, path)
        result.update(phase='confirmed', readback_verified=True, physical_transition=True,
                      confirmed_at=now().isoformat(), commissioned=commissioned(state))
        return result
    except Exception as exc:
        if result['writes_attempted']:
            result['phase'] = 'uncertain'
            if state is not None and state['last_attempt'] and state['last_attempt']['id'] == command_id:
                try:
                    state['last_attempt']['phase'] = 'uncertain'; save_state(state, path)
                except Exception:
                    pass
        result['reason'] = str(exc) if isinstance(exc, Rejected) else type(exc).__name__
        return result
    finally:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass
        finally:
            if held:
                bus.release()
            if journal is not None:
                journal.close()


def execute_power_command(*, identity, command_id, command_payload, submit_result):
    result = execute(identity, command_id, command_payload)
    success = result['phase'] in ('confirmed', 'already_set') and result['readback_verified'] is True
    submit_result(identity=identity, command_id=command_id, status='succeeded' if success else 'failed',
                  result=result, error_message=None if success else result['reason'])
