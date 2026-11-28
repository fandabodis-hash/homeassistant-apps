"""F171 local loop tests: simulated Zigbee, disk cache, WAN forbidden.
Never accesses a real HA service, device, cloud, or serial port.
"""
import copy
import importlib
import json
import socket
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib import request
import f171_midea_00019_thermostat as m
from test_f171_midea_00019_thermostat import HA, IDENT, C, cloud

class LocalHA(HA):
    def get_entity_state(self, entity):
        state = super().get_entity_state(entity)
        if entity == m.ROOM:
            state['last_reported'] = (datetime.now(timezone.utc) - timedelta(seconds=1801 if self.stale else 1)).isoformat()
        return state

class EndCycle(BaseException):
    pass

class TestOffline(unittest.TestCase):
    def exercise(self, cache_module):
        """Start, arm and then restart the actual runtime loop, using only files."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            identity = root/'device.json'; config = root/'cloud-config.json'; state = root/'state.json'
            identity.write_text(json.dumps({**IDENT,'device_token':'TEST_ONLY'}), encoding='utf-8')
            cfg = copy.deepcopy(C); cfg['phase'] = 'shadow'
            config.write_text(json.dumps(cloud(cfg)), encoding='utf-8')
            ha = LocalHA()
            def cycle_once():
                with patch.object(m.time,'sleep',side_effect=EndCycle):
                    with self.assertRaises(EndCycle): m.run_forever()
                return m.load_state(state)
            with patch.object(cache_module,'DEVICE_CONFIG_PATH',identity), patch.object(cache_module,'CLOUD_CONFIG_PATH',config), patch.object(m,'STATE_PATH',state), patch.dict(sys.modules,{'device_config':cache_module,'zigbee_manager':ha}), patch.object(cache_module,'fetch_cloud_config',side_effect=AssertionError('WAN_FORBIDDEN')) as fetch, patch.object(request,'urlopen',side_effect=AssertionError('WAN_FORBIDDEN')) as url, patch.object(socket,'create_connection',side_effect=AssertionError('WAN_FORBIDDEN')) as connect, patch.object(socket.socket,'connect',side_effect=AssertionError('WAN_FORBIDDEN')) as connect2:
                self.assertTrue(cycle_once()['shadow_ready']); self.assertEqual(ha.calls,[])
                # Settings already synced/armed locally BEFORE the outage. From
                # here the file stays unchanged, including across repeated starts.
                cfg['phase']='armed'; config.write_text(json.dumps(cloud(cfg)),encoding='utf-8')
                cached=config.read_bytes()
                self.assertTrue(cycle_once()['physical_control_active']);self.assertEqual(ha.calls,[True])
                ha.room=20.8;self.assertTrue(cycle_once()['relay_on']);self.assertEqual(ha.calls,[True])
                ha.room=21;self.assertFalse(cycle_once()['relay_on']);self.assertEqual(ha.calls,[True,False])
                # Reload module to reset its in-memory locks and simulated process state.
                importlib.reload(m);m.STATE_PATH=state
                ha.room=20.7;self.assertTrue(cycle_once()['physical_control_active']);self.assertEqual(ha.calls,[True,False,True])
                self.assertEqual(config.read_bytes(),cached)
                ha.stale=True;ha.room=25;s=cycle_once();self.assertFalse(s['physical_control_active']);self.assertEqual(s['reason'],'room_measurement_stale');self.assertEqual(ha.calls,[True,False,True])
                for spy in (fetch,url,connect,connect2):spy.assert_not_called()
    def test_offline_loop_disk_cache_fixture(self):
        c=types.ModuleType('device_config');c.DEVICE_CONFIG_PATH=None;c.CLOUD_CONFIG_PATH=None
        c.load_device_identity=lambda:json.loads(c.DEVICE_CONFIG_PATH.read_text())
        c.load_cached_cloud_config=lambda:json.loads(c.CLOUD_CONFIG_PATH.read_text())
        c.fetch_cloud_config=lambda *a: (_ for _ in ()).throw(AssertionError('network'))
        self.exercise(c)
    def test_offline_loop_real_agent_cache(self):
        import device_config
        self.exercise(device_config)

if __name__=='__main__':unittest.main(verbosity=2)
