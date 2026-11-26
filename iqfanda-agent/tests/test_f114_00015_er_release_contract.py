"""F114 R30 successor: reviewed ER changes only; 181 and 182 remain historical."""
import hashlib,json,subprocess,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
BASE='9e503531e545cb3bf3cacbaf732c561e9913fd0e'
EXPECTED={'.github/workflows/f106-midea-00019-readonly.yml': 'M', 'iqfanda-agent/config.yaml': 'M', 'iqfanda-agent/contracts/manual_battery_release.json': 'M', 'iqfanda-agent/rootfs/app/VERSION': 'M', 'iqfanda-agent/rootfs/app/f114_heating_circuit_local.py': 'M', 'iqfanda-agent/rootfs/app/heating_circuit_controller.py': 'M', 'iqfanda-agent/rootfs/app/protection/manual_battery_manifest.json': 'M', 'iqfanda-agent/tests/test_f114_00015_er_release_contract.py': 'A', 'iqfanda-agent/tests/test_f114_heating_circuit_local.py': 'M', 'iqfanda-agent/tests/test_heating_circuit_controller.py': 'M'}
HASHES={'.github/workflows/f106-midea-00019-readonly.yml': '6a61f4c7264cc475c54bef3e7da18ee6b6b97dc414922a3e1f6f37a1870166b2', 'iqfanda-agent/config.yaml': 'a8bdbdcd68f04b837808be5d1733efc69ac81edf8819128a44deb85447b896ed', 'iqfanda-agent/rootfs/app/VERSION': 'bfea5cf6f3ead8cdf50848e4eed72ded56d58c9fb12d7e8b13afe7056defadb7', 'iqfanda-agent/rootfs/app/f114_heating_circuit_local.py': 'e16af3d65e11976d5932bf32d8ccd094523b2606f917eb94c9acc5d2cb0009ad', 'iqfanda-agent/rootfs/app/heating_circuit_controller.py': '598fb366d4881991310bd4f00eef18b267d8a10334cbbee88da2f360dcb31691', 'iqfanda-agent/tests/test_f114_heating_circuit_local.py': 'a05f9081e282970c160df1ebe2118fb8457abca945d34e193b269c11a32b0272', 'iqfanda-agent/tests/test_heating_circuit_controller.py': '9ac19573be31c0de7a65765c3e2e78261a89415697ed9db7bc314e4612291c6b'}
CON='iqfanda-agent/contracts/manual_battery_release.json'
MAN='iqfanda-agent/rootfs/app/protection/manual_battery_manifest.json'
EVID='iqfanda-agent/contracts/manual_battery_physical_evidence.json'
LEG='iqfanda-agent/tests/test_f114_00015_release_contract.py'
NEW='iqfanda-agent/tests/test_f114_00015_er_release_contract.py'
APP='iqfanda-agent/rootfs/app/'
def git(*a):return subprocess.check_output(['git','-C',str(ROOT),*a],stderr=subprocess.STDOUT,timeout=60)
def digest(p):return hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
class TestExact183Successor(unittest.TestCase):
    def test_only_reviewed_delta(self):
        git('merge-base','--is-ancestor',BASE,'HEAD')
        actual={p:s for s,p in (line.split('\t',1) for line in git('diff','--name-status','--no-renames',BASE,'--').decode().splitlines())}
        self.assertEqual(actual,EXPECTED)
    def test_exact_reviewed_sources(self):
        for p,h in HASHES.items():self.assertEqual(digest(p),h,p)
    def test_historical_evidence_and_contracts_unchanged(self):
        for p in (EVID,LEG,'iqfanda-agent/tests/test_f133_agent_0177_additive_contract.py',APP+'command_worker.py',APP+'zigbee_manager.py',APP+'heartbeat.py',APP+'startup.py'):
            self.assertEqual((ROOT/p).read_bytes(),git('show',BASE+':'+p),p)
        current=json.loads((ROOT/CON).read_bytes());previous=json.loads(git('show',BASE+':'+CON))
        self.assertEqual(current['change_history'][:len(previous['change_history'])],previous['change_history'])
    def test_manual_release_and_integrity(self):
        cfg=(ROOT/'iqfanda-agent/config.yaml').read_text()
        self.assertIn('version: "0.1.183"',cfg);self.assertIn('  - "0.1.183"',cfg)
        c=json.loads((ROOT/CON).read_bytes());m=json.loads((ROOT/MAN).read_bytes())
        self.assertEqual(c['agent_version'],'0.1.183');self.assertEqual(m['agent_version'],'0.1.183')
        self.assertEqual(c['files'][NEW],digest(NEW))
        for p in ('f114_heating_circuit_local.py','heating_circuit_controller.py','heartbeat.py','startup.py','zigbee_manager.py'):
            self.assertEqual(m['files'][p],digest(APP+p),p)
