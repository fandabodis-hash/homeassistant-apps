"""Runs in the actual addon image; tests the real command-worker routing."""
import unittest
from unittest.mock import patch
import command_worker as cw
import thermostat_preflight as pre
UID='00000000-0000-0000-0000-000000000114'
class WorkerRouting(unittest.TestCase):
    def test_new_command_reaches_read_only_executor(self):
        reports=[];calls=[]
        with patch.object(cw,'submit_command_result',lambda **kw:reports.append(kw)), \
             patch.object(pre,'execute',lambda **kw:calls.append(kw)):
            cw.execute_command(identity={'device_uuid':UID},command={'id':'q','command_type':pre.COMMAND,'payload':{'request_id':'r'}})
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['command_payload'],{'request_id':'r'})
        self.assertEqual(reports[0]['status'],'running')
    def test_invalid_payload_cannot_activate(self):
        reports=[]
        with patch.object(cw,'submit_command_result',lambda **kw:reports.append(kw)):
            cw.execute_command(identity={'device_uuid':UID},command={'id':'q','command_type':pre.COMMAND,'payload':{'activate':True}})
        self.assertEqual(reports[-1]['status'],'failed');self.assertFalse(reports[-1]['result']['actuation'])
    def test_unknown_command_remains_unsupported(self):
        reports=[]
        with patch.object(cw,'submit_command_result',lambda **kw:reports.append(kw)):
            cw.execute_command(identity={'device_uuid':UID},command={'id':'q','command_type':'f55_unknown_no_action','payload':{}})
        self.assertEqual(reports[-1]['result']['phase'],'unsupported_command')
    def test_reset_route_unchanged(self):
        import reset_os
        reports=[];calls=[]
        with patch.object(cw,'submit_command_result',lambda **kw:reports.append(kw)), \
             patch.object(reset_os,'execute_reset_os',lambda **kw:calls.append(kw)):
            cw.execute_command(identity={'device_uuid':UID},command={'id':'q','command_type':'reset_os','payload':{}})
        self.assertEqual(len(calls),1)
