"""F48 R10: create local agent candidate only after exact main-branch source match.
DO NOT call on production without verifying installed Agent code, version, site and backup.
"""
from __future__ import annotations
import hashlib
from pathlib import Path

BASE_BLOB = '1639b121f937692a6321995c87294c5395c4643c'
MODULE_FILE = 'f48_r10_00012_off_readback.py'


def git_blob(data: bytes) -> str:
    return hashlib.sha1((f'blob {len(data)}\0').encode('ascii') + data).hexdigest()


def patch(source: bytes) -> bytes:
    if git_blob(source) != BASE_BLOB:
        raise RuntimeError('STOP_AGENT_BASELINE_DRIFT_NO_WRITE')
    original = source.decode('utf-8', errors='strict')
    fn = 'def execute_zigbee_switch_set('
    if original.count(fn) != 1:
        raise RuntimeError('STOP_EXECUTOR_NOT_UNIQUE')
    start = original.index(fn)
    end = original.index('\n# PHASE47_R131_00013_HA_SENSOR_REPORT_TIMESTAMPS_READ_ONLY', start)
    before, target, after = original[:start], original[start:end], original[end:]

    rules = [
      ('        service_called = False\n        service_name = None\n',
       '        service_called = False\n        service_name = None\n'
       '        f48_r10_povel_pokus = False\n'),
      ('            service_result = (\n                call_binary_power_output_service(\n',
       '            f48_r10_povel_pokus = True\n'
       '            service_result = (\n                call_binary_power_output_service(\n'),
      ('    except Exception as exc:\n\n        submit_command_result(\n',
       '''    except Exception as exc:
        # F48 R10: presne 00012 bivalence OFF; po timeoutu POUZE cteni HA.
        # Pokud nove OFF nelze overit, zustava puvodni fail-closed vetev.
        if locals().get('f48_r10_povel_pokus') is True:
            try:
                from f48_r10_00012_off_readback import overit_off_po_timeoutu
                f48_r10_dokaz = overit_off_po_timeoutu(
                    identita=identity,
                    prikaz=command_payload,
                    pred_stavem=locals().get('before') or {},
                    chyba=exc,
                    registr_id=expected_device_id,
                    lokalni_id=locals().get('actual_device_id'),
                    pokus_o_povel=True,
                    nacti_stav=get_entity_state,
                )
                if f48_r10_dokaz is not None:
                    submit_command_result(
                        identity=identity,
                        command_id=command_id,
                        status='succeeded',
                        result={
                            'worker': 'command_worker',
                            'executor': 'zigbee_switch_set',
                            'phase': 'f48_r10_off_verified_after_timeout',
                            'capability': 'binary_power_output',
                            'entity_id': entity_id,
                            'ieee': ieee,
                            'device_reg_id': expected_device_id,
                            'source': source,
                            'requested_state': 'off',
                            'previous_state': 'on',
                            'readback_state': 'off',
                            'readback_verified': True,
                            'service_called': True,
                            'service': 'switch.turn_off',
                            'verification': f48_r10_dokaz['verification'],
                            'manual_override': False,
                            'fanda_has_priority': True,
                        },
                        error_message=None,
                    )
                    logging.info('F48 R10 00012: bivalence OFF potvrzeno po timeoutu, bez opakovani povelu')
                    return
            except Exception:
                # Kriticky vystup: fallback nesmi potlacit puvodni chybu.
                logging.warning('F48 R10 00012: dodatecny readback neni spolehlivy, zachovana chyba OFF', exc_info=True)

        submit_command_result(
'''),
    ]
    for old, new in rules:
        count=target.count(old)
        if count != 1:
            raise RuntimeError(f'STOP_PATCH_ANCHOR_NOT_UNIQUE count={count}')
        target=target.replace(old,new,1)
    result=(before+target+after).encode('utf-8')
    compile(result.decode('utf-8'), 'command_worker.py', 'exec')
    if result.count(b'F48 R10') != 3:
        raise RuntimeError('STOP_R10_MARKER_COUNT')
    return result


if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('input',type=Path)
    p.add_argument('output',type=Path)
    args=p.parse_args()
    src=args.input.read_bytes()
    result=patch(src)
    if args.output.exists():
        raise RuntimeError('STOP_OUTPUT_ALREADY_EXISTS')
    args.output.write_bytes(result)
    print('R10_LOCAL_CANDIDATE_SHA256='+hashlib.sha256(result).hexdigest())
    print('NO_DEPLOY_ONLY_WRITE_LOCAL_CANDIDATE')