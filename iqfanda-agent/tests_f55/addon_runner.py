"""Runs only inside an isolated QA container, never on a deployed Fanda."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ATTEMPTS=[]
def deny_network(event,args):
    if event in ('socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','socket.sendto'):
        ATTEMPTS.append(event)
        raise RuntimeError('F55_QA_NETWORK_ACCESS_FORBIDDEN:'+event)
sys.addaudithook(deny_network)
os.environ.pop('SUPERVISOR_TOKEN',None)
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
sys.path[:0]=['/app','/opt/f55qa']

print('F55_QA_NO_NETWORK_NO_HARDWARE=YES',flush=True)
for name in ('thermostat_engine','thermostat_runtime','zigbee_manager','heartbeat',
             'telemetrie_modulu','command_worker','startup','thermostat_preflight',
             'thermostat_commission'):
    module=importlib.import_module(name)
    path=Path(module.__file__).resolve()
    if not path.is_relative_to(Path('/app')):raise RuntimeError('unexpected_import_source:'+name)
    print('REAL_IMPORT='+name,flush=True)
assert str(ZoneInfo('Europe/Prague'))=='Europe/Prague'
version=Path('/app/VERSION').read_text().strip()
assert version=='0.1.172','version_mismatch'
# No main()/service thread is started. Runtime ticks are explicitly simulated.
loader=unittest.TestLoader()
unit=loader.discover('/opt/f55qa',pattern='test_thermostat.py')
integration=loader.discover('/opt/f55qa',pattern='test_integration.py')
unit_count=unit.countTestCases();integration_count=integration.countTestCases()
preflight=loader.discover('/opt/f55qa',pattern='test_preflight.py')
routing=loader.discover('/opt/f55qa',pattern='test_preflight_adapter.py')
commission=loader.discover('/opt/f55qa',pattern='test_commission.py')
assert preflight.countTestCases()==22 and routing.countTestCases()==4 and commission.countTestCases()==8
assert unit_count>=81 and integration_count>=19,'tests_missing'
result=unittest.TextTestRunner(verbosity=2,stream=sys.stdout).run(unittest.TestSuite([unit,integration,preflight,routing,commission]))
summary={'unit_tests':unit_count,'integration_tests':integration_count,
         'read_only_preflight_tests':22,'worker_routing_tests':4,'commission_tests':8,
         'total':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
         'skipped':len(result.skipped),'real_imports':True,'physical_io':'SIMULATED',
         'network_attempts':ATTEMPTS,'hardware_tested':False}
print('F55_R4_ADDON_QA_SUMMARY='+json.dumps(summary),flush=True)
if not result.wasSuccessful() or ATTEMPTS or result.skipped:sys.exit(2)
print('F55_R4_ADDON_QA_PASS=YES',flush=True)
