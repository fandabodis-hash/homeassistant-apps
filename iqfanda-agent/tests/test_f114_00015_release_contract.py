"""Exact F114 00015-only 182 successor; 181 F133 remains immutable."""
import hashlib, json, subprocess, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
BASE='a58969a904e9234e56912e9f9a30baa3d354b14a'
APP='iqfanda-agent/rootfs/app/'
CONTRACT='iqfanda-agent/contracts/manual_battery_release.json'
MANIFEST=APP+'protection/manual_battery_manifest.json'
EVIDENCE='iqfanda-agent/contracts/manual_battery_physical_evidence.json'
APPROVED_HASHES={'.github/workflows/f106-midea-00019-readonly.yml': '941a117745aac17307083c99ca2e716083d4353dc8c6d6bb971cb80d9e594a61', 'iqfanda-agent/Dockerfile': '663f98d078e3873a5ba253a9b1703dc3158f5b5840b974bb41b6a2737d91f1ef', 'iqfanda-agent/config.yaml': 'ea4a31ce809948d697b37f0157ade051e3e8c1d6b6bb0655c0c22346993c9d30', 'iqfanda-agent/rootfs/app/VERSION': 'd350ec54ee92ed63d0a7d0beaf4fdeec09b70dbeab53c35f5ebc2a77b470c873', 'iqfanda-agent/rootfs/app/f114_heating_circuit_local.py': 'b0cfcbf7e7510ff51157800ae4f747118e41178aec3d414fb2abfe1fd1b6076e', 'iqfanda-agent/rootfs/app/heartbeat.py': '55e1e401f35c6ec039235f98b7a83b9563c052318b59ef8c16f75a34a811b677', 'iqfanda-agent/rootfs/app/heating_circuit_controller.py': '50e7f0d1ada49d5ee222b7488b461e5bd04d2c8e47fdc167c6f5bd08b04b007f', 'iqfanda-agent/rootfs/app/startup.py': 'd2823c065cf6a98b48706e7bea09bf3a2051517b0a8fe855049357f622036be8', 'iqfanda-agent/rootfs/app/zigbee_manager.py': 'af6a046288a62b6f24c070767b7d3241f32d0f3c43ecbef008deb1ebb5d63f8d', 'iqfanda-agent/tests/test_f114_heating_circuit_local.py': '2197dd3b65e4e2b0e49760d4cf336912a25dbf305687bc4131a772b6b64daca1', 'iqfanda-agent/tests/test_heating_circuit_controller.py': '13934140183e550e87ca5c732caca8be020bd3affcacdc421dab6435e069de16'}
ALLOWED={'.github/workflows/f106-midea-00019-readonly.yml': 'M', 'iqfanda-agent/Dockerfile': 'M', 'iqfanda-agent/config.yaml': 'M', 'iqfanda-agent/contracts/manual_battery_release.json': 'M', 'iqfanda-agent/rootfs/app/VERSION': 'M', 'iqfanda-agent/rootfs/app/f114_heating_circuit_local.py': 'A', 'iqfanda-agent/rootfs/app/heartbeat.py': 'M', 'iqfanda-agent/rootfs/app/heating_circuit_controller.py': 'A', 'iqfanda-agent/rootfs/app/protection/manual_battery_manifest.json': 'M', 'iqfanda-agent/rootfs/app/startup.py': 'M', 'iqfanda-agent/rootfs/app/zigbee_manager.py': 'M', 'iqfanda-agent/tests/test_f114_00015_release_contract.py': 'A', 'iqfanda-agent/tests/test_f114_heating_circuit_local.py': 'A', 'iqfanda-agent/tests/test_heating_circuit_controller.py': 'A'}

def git(*args):
    return subprocess.check_output(['git','-C',str(ROOT),*args],stderr=subprocess.STDOUT,timeout=60)

def digest(path):return hashlib.sha256((ROOT/path).read_bytes()).hexdigest()

class TestExact182Successor(unittest.TestCase):
    def test_only_reviewed_changes(self):
        git('merge-base','--is-ancestor',BASE,'HEAD')
        rows=git('diff','--name-status','--no-renames',BASE,'--').decode().splitlines()
        actual={}
        for row in rows:
            state,path=row.split('\t',1)
            self.assertIn(state,('A','M'),row)
            actual[path]=state
        self.assertEqual(actual,ALLOWED)
    def test_exact_runtime_and_ci_bytes(self):
        for path,value in APPROVED_HASHES.items():
            self.assertEqual(digest(path),value,path)
    def test_181_legacy_contract_and_physical_evidence_unchanged(self):
        for path in (EVIDENCE,'iqfanda-agent/tests/test_f133_agent_0177_additive_contract.py','iqfanda-agent/rootfs/app/command_worker.py'):
            self.assertEqual((ROOT/path).read_bytes(),git('show',BASE+':'+path),path)
    def test_manual_version_and_extended_manifest(self):
        cfg=(ROOT/'iqfanda-agent/config.yaml').read_text()
        self.assertIn('version: "0.1.182"',cfg)
        self.assertIn('  - "0.1.182"',cfg)
        contract=json.loads((ROOT/CONTRACT).read_bytes());manifest=json.loads((ROOT/MANIFEST).read_bytes())
        self.assertEqual(contract['agent_version'],'0.1.182')
        self.assertEqual(manifest['agent_version'],'0.1.182')
        for rel in ('zigbee_manager.py','f114_heating_circuit_local.py','heating_circuit_controller.py','startup.py','heartbeat.py'):
            self.assertEqual(manifest['files'][rel],digest(APP+rel),rel)
        for p in ('iqfanda-agent/tests/test_f114_00015_release_contract.py','.github/workflows/f106-midea-00019-readonly.yml'):
            self.assertEqual(contract['files'][p],digest(p),p)

if __name__=='__main__':unittest.main()
