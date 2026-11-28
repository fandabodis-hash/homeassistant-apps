"""F171: cumulative, manually installed, 00019-only release candidate."""
import hashlib,json,subprocess,unittest,ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
BASE='3ce8438a3a8b1a2058d8fd89d5beceaa063739c5'
APP='iqfanda-agent/rootfs/app/'
CON='iqfanda-agent/contracts/manual_battery_release.json'
MAN='iqfanda-agent/rootfs/app/protection/manual_battery_manifest.json'
EVID='iqfanda-agent/contracts/manual_battery_physical_evidence.json'
ALLOWED=['.github/workflows/f106-midea-00019-readonly.yml', 'iqfanda-agent/config.yaml', 'iqfanda-agent/contracts/manual_battery_release.json', 'iqfanda-agent/rootfs/app/VERSION', 'iqfanda-agent/rootfs/app/communication/midea_00019_power.py', 'iqfanda-agent/rootfs/app/f171_midea_00019_thermostat.py', 'iqfanda-agent/rootfs/app/heartbeat.py', 'iqfanda-agent/rootfs/app/protection/manual_battery_manifest.json', 'iqfanda-agent/rootfs/app/startup.py', 'iqfanda-agent/rootfs/app/zigbee_manager.py', 'iqfanda-agent/tests/test_f171_midea_00019_thermostat.py', 'iqfanda-agent/tests/test_f171_offline.py', 'iqfanda-agent/tests/test_f171_release_contract.py']
PINS={'iqfanda-agent/rootfs/app/f171_midea_00019_thermostat.py': '2d015c9bacc62fb703bce2b2a74d6a3ac9328301ef8d1c2fcb76de856457e3d0', 'iqfanda-agent/tests/test_f171_midea_00019_thermostat.py': 'da46f06e204e4ed608d2baa2d10cca34b1d09197fe109a287439ef163d88927e', 'iqfanda-agent/tests/test_f171_offline.py': '580524754020c37fdb9aee5a8e903602bb960521dd6815384860981e0da67d9e'}
def git(*a):return subprocess.check_output(['git','-C',str(ROOT),*a],timeout=120)
def h(p):return hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
class TestF171CumulativeRelease(unittest.TestCase):
    def test_exact_delta(self):
        git('merge-base','--is-ancestor',BASE,'HEAD')
        self.assertEqual(set(git('diff','--name-only',BASE,'HEAD').decode().splitlines()),set(ALLOWED))
    def test_exact_approved_r5_sources(self):
        for p,d in PINS.items():self.assertEqual(h(p),d,p)
    def test_protected_history(self):
        previous=json.loads(git('show',BASE+':'+CON));current=json.loads((ROOT/CON).read_bytes())
        self.assertEqual(current['change_history'][:-1],previous['change_history'])
        self.assertEqual(current['profiles'],previous['profiles'])
        self.assertEqual((ROOT/EVID).read_bytes(),git('show',BASE+':'+EVID))
        self.assertEqual(set(previous['files'])-set(current['files']),set())
        for p,d in previous['files'].items():
            if p not in ALLOWED:self.assertEqual(current['files'][p],d,p)
    def test_version_and_manifest(self):
        self.assertEqual((ROOT/(APP+'VERSION')).read_text().strip(),'0.1.184')
        cfg=(ROOT/'iqfanda-agent/config.yaml').read_text()
        self.assertIn('version: "0.1.184"',cfg);self.assertIn('  - "0.1.184"',cfg)
        m=json.loads((ROOT/MAN).read_bytes());self.assertEqual(m['agent_version'],'0.1.184')
        self.assertEqual(m['files']['f171_midea_00019_thermostat.py'],h(APP+'f171_midea_00019_thermostat.py'))
        for n in ('startup.py','heartbeat.py','zigbee_manager.py','communication/midea_00019_power.py'):
            self.assertEqual(m['files'][n],h(APP+n))
    def test_midea_versions_explicit(self):
        p=ROOT/(APP+'communication/midea_00019_power.py');t=ast.parse(p.read_text(encoding='utf-8'))
        a=[x for x in t.body if isinstance(x,ast.Assign) and any(isinstance(k,ast.Name) and k.id=='SUPPORTED_VERSIONS' for k in x.targets)]
        self.assertEqual(len(a),1);self.assertEqual(ast.literal_eval(a[0].value),('0.1.181','0.1.183','0.1.184'))
