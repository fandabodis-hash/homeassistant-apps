"""Fixture pro read-only Rotek probe; zadny realny port se neotevira."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

# Ve strome repozitare pouzijeme skutecny sdileny zamykaci modul.
# Lokalni fixture bez instalace Agenta vyuziva izolovany stub.
import threading
p=Path(__file__).with_name('rotek_ecu_rtg3_readonly.py')
if not p.is_file():
    p=Path(__file__).resolve().parents[1] / 'rootfs/app/communication/rotek_ecu_rtg3_readonly.py'
if p.parent.name == 'communication':
    sys.path.insert(0,str(p.parents[1]))
    from communication import modbus_bus_lock as locks
else:
    mutex={}
    com=types.ModuleType('communication')
    locks=types.ModuleType('communication.modbus_bus_lock')
    locks.ziskej_zamek_modbus_sbernice=lambda x:mutex.setdefault(x,threading.Lock())
    sys.modules['communication']=com
    sys.modules['communication.modbus_bus_lock']=locks
spec=importlib.util.spec_from_file_location('rotek_readonly_under_test',p)
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
PATH='/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A9T872DV-if00-port0'
BASE={
    'read_only':True,'control_enabled':False,'probe_profile_id':r.PROFILE_ID,
    'communication_type':'rs485','manufacturer':'rotek','slave_id':1,'baudrate':9600,
    'communicator_id':'ftdi-123','communicator_serial_number':'A9T872DV','preferred_path':PATH,
}
COMM={
    'communicator_id':'ftdi-123','serial_number':'A9T872DV','preferred_path':PATH,
    'manufacturer':'FTDI','vid':'0403','pid':'6001',
    'status':'ready','connected':True,'stable_paths':[PATH],
    'capabilities':['serial','rs485_or_rs232'],'type':'usb_serial',
}
HUB={
    'communicator_id':'hub-mirror','serial_number':None,'preferred_path':PATH,
    'manufacturer':'Generic','status':'ready','connected':True,
    'stable_paths':[PATH],'capabilities':['serial'],'type':'usb_device',
}
class Response:
    def __init__(self,value):self.registers=[value]
    def isError(self):return False
class FakeClient:
    def __init__(self,registers):self.reg=registers;self.calls=[];self.closed=False
    def connect(self):return True
    def close(self):self.closed=True
    def read_holding_registers(self,**kwargs):
        assert set(kwargs)=={'address','count','device_id'}
        self.calls.append(kwargs)
        value=self.reg.get(kwargs['address'])
        return Response(value) if value is not None else None

class TestRotek(unittest.TestCase):
    def test_typical_75_percent(self):
        c=FakeClient({23:76,24:124,25:0,26:0})
        result=r.probe_rotek_ecu_rtg3_readonly(BASE,inventory=[COMM,HUB],client_factory=lambda **kw:c)
        self.assertEqual(result['telemetry']['fuel_level_percent'],76)
        self.assertTrue(result['profile_verified'])
        self.assertEqual([x['address'] for x in c.calls],[23,24,25,26])
        self.assertTrue(c.closed)
        self.assertFalse(result['write_performed'])
        self.assertEqual(result['function_code'],3)
    def test_invalid_fuel_is_unavailable(self):
        c=FakeClient({23:65535,24:121,25:0,26:0})
        out=r.probe_rotek_ecu_rtg3_readonly(BASE,inventory=[COMM],client_factory=lambda **kw:c)
        self.assertIsNone(out['telemetry']['fuel_level_percent'])
        self.assertEqual(out['telemetry']['fuel_level_status'],'not_supported_or_invalid')
    def test_missing_battery_does_not_verify(self):
        c=FakeClient({23:76,25:0,26:0})
        out=r.probe_rotek_ecu_rtg3_readonly(BASE,inventory=[COMM],client_factory=lambda **kw:c)
        self.assertFalse(out['profile_verified'])
    def test_disallow_write_flag(self):
        value={**BASE,'control_enabled':True}
        with self.assertRaises(r.RotekProbeError):r.validate_probe_input(value,[COMM])
    def test_wrong_serial(self):
        value={**BASE,'communicator_serial_number':'JINY'}
        with self.assertRaises(r.RotekProbeError):r.validate_probe_input(value,[COMM])
    def test_disallow_hub(self):
        value={**BASE,'communicator_id':'hub-mirror','communicator_serial_number':'X'}
        with self.assertRaises(r.RotekProbeError):r.validate_probe_input(value,[COMM,HUB])
    def test_disallow_port_ambiguity(self):
        bad={**COMM,'communicator_id':'ftdi-456'}
        with self.assertRaises(r.RotekProbeError):r.validate_probe_input(BASE,[COMM,bad])
    def test_disallow_other_slave(self):
        value={**BASE,'slave_id':200}
        with self.assertRaises(r.RotekProbeError):r.validate_probe_input(value,[COMM])
    def test_disallow_arbitrary_register(self):
        with self.assertRaises(r.RotekProbeError):r._one_register(FakeClient({200:1}),200)
    def test_bus_lock_prevents_parallel(self):
        lock=locks.ziskej_zamek_modbus_sbernice(PATH)
        lock.acquire()
        try:
            with self.assertRaises(r.RotekProbeError):
                r.probe_rotek_ecu_rtg3_readonly(BASE,inventory=[COMM],client_factory=lambda **kw:FakeClient({23:72}))
        finally:
            lock.release()

if __name__=='__main__':unittest.main()