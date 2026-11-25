"""F169 R2: portable repository-relative manifest discovery, no hardware access."""
import importlib.util
import unittest
from pathlib import Path, PureWindowsPath, PurePosixPath
from unittest.mock import patch

GUARD=Path(__file__).resolve().parents[1]/'scripts'/'check_manual_battery_release.py'
spec=importlib.util.spec_from_file_location('f169_release_guard',GUARD)
g=importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

class DiscoveryRoot:
    def __init__(self, path):self.path=path
    def __truediv__(self, relative):return DiscoveryRoot(self.path/relative)
    def glob(self, pattern):
        assert pattern=='test_*battery*.py'
        return [self.path/'test_manual_battery_runtime.py',self.path/'test_extra_battery.py']

class Tests(unittest.TestCase):
    def test_windows_and_posix_auto_discovery(self):
        for root in (PureWindowsPath(r'C:\repos\candidate'),PurePosixPath('/repos/candidate')):
            expected={'iqfanda-agent/tests/test_manual_battery_runtime.py','iqfanda-agent/tests/test_extra_battery.py','existing/guard.py'}
            def capture(base,paths):
                self.assertEqual(paths,expected)
                return {p:'0'*64 for p in sorted(paths)}
            with self.subTest(root=str(root)),patch.object(g,'Path',lambda x:DiscoveryRoot(x)),patch.object(g,'profile_snapshot',return_value={}),patch.object(g,'source_snapshot',side_effect=capture),patch.object(g,'read_version',return_value='0.1.181'),patch.object(g,'production_manifest',return_value={'runtime_fingerprint':'0'*64}):
                actual=g.expected_snapshot(root,{'existing/guard.py'})
                self.assertEqual(set(actual['files']),expected)
    def test_existing_path_security_unchanged(self):
        root=GUARD.parent
        for path in (r'tests\bad.py','../bad.py','/absolute.py',''):
            with self.subTest(path=path),self.assertRaises(g.ContractError):g.safe_path(root,path)

if __name__=='__main__':unittest.main()
