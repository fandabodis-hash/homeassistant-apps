"""F169 fake transport tests. Never open real serial ports."""
import importlib.util
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

HERE=Path(__file__).resolve()
APP=HERE.parents[1]/'rootfs'/'app'
if APP.is_dir():
    sys.path.insert(0,str(APP))
    from communication import midea_00019_power as m
else:
    # isolated local tests with imports replaced by known exact contract constants
    names={'communication.midea_00019_cloud_probe':dict(DEVICE_UUID='648d4229-7c17-400f-bbed-ffe5ab04b844',SITE_ID='980e5e5e-76b4-4cac-9b6c-1f31fa5fd6e1'),
           'communication.midea_00019_readonly':dict(SERIAL='F800711-TNG-00019',FTDI_PORT='/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02YF7T-if00-port0',BAUDRATE=9600,PARITY='N',STOP_BITS=1),
           'communication.midea_00019_live':dict(verified_port=lambda x:False),
           'communication.modbus_bus_lock':dict(ziskej_zamek_modbus_sbernice=lambda x:threading.Lock())}
    sys.modules['communication']=types.ModuleType('communication')
    for k,v in names.items():
        sys.modules[k]=types.ModuleType(k);sys.modules[k].__dict__.update(v)
    spec=importlib.util.spec_from_file_location('f169',HERE.parents[1]/'agent'/'midea_00019_power.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Response:
    def __init__(self,regs,fc=3,address=0,error=False):
        self.registers=regs;self.function_code=fc;self.address=address;self.error=error
    def isError(self):return self.error

class Fake:
    def __init__(self,word=2,mode=3,prot=0,loads=0,temps=(37,35),ack=True,change=True):
        self.word=word;self.mode=mode;self.prot=prot;self.loads=loads;self.temps=temps;self.ack=ack;self.change=change;self.calls=[];self.writes=[];self.closed=False;self.bad_reads=False
    def connect(self):return True
    def close(self):self.closed=True
    def read_holding_registers(self,*,address,count,device_id):
        assert device_id==1;self.calls.append((address,count))
        return Response({0:[self.word,self.mode],128:[self.prot,self.loads],104:list(self.temps)}[address],error=self.bad_reads)
    def write_register(self,*,address,value,device_id):
        assert device_id==1 and address==0;self.writes.append((address,value));
        if self.change:self.word=value
        return Response([value],fc=6,error=not self.ack)

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'state.json';self.clock=datetime(2026,11,25,21,0,tzinfo=timezone.utc);self.lock=threading.Lock()
        self.id={'device_uuid':m.DEVICE_UUID,'site_id':m.SITE_ID};self.patcher=patch.object(m,'now',lambda:self.clock);self.patcher.start();self.addCleanup(self.patcher.stop)
    def payload(self,on=True,before=0,commission=True):
        return dict(device_uuid=m.DEVICE_UUID,site_id=m.SITE_ID,serial_number=m.SERIAL,profile=m.PROFILE,desired_on=on,expected_word=before,commissioning=commission,safety_confirmed=True,nonce=str(uuid4()),expires_at=(self.clock+timedelta(seconds=90)).isoformat())
    def call(self,f,p=None,identity=None,cid=None,**kw):
        options=dict(path=self.path,client_factory=lambda **k:self.factory(f,k),identity_check=lambda i:True,port_check=lambda i:True,lock_factory=lambda p:self.lock,sleep=lambda t:None);options.update(kw)
        return m.execute(self.id if identity is None else identity,cid or str(uuid4()),p or self.payload(),**options)
    def factory(self,f,k):self.assertEqual(k['port'],m.FTDI_PORT);self.assertEqual(k['retries'],0);return f
    def test_off_on_and_client(self):
        f=Fake();a=self.call(f,self.payload(False,2));self.assertEqual(a['phase'],'confirmed');self.assertEqual(f.writes,[(0,0)]);self.assertFalse(m.commissioned(m.load_state(self.path)));self.assertTrue(f.closed)
        self.clock+=timedelta(seconds=31);b=self.call(f,self.payload(True,0));self.assertEqual(b['phase'],'confirmed');self.assertTrue(m.commissioned(m.load_state(self.path)))
        self.clock+=timedelta(seconds=31);c=self.call(f,self.payload(False,2,False));self.assertEqual(c['phase'],'confirmed');self.assertEqual(f.writes,[(0,0),(0,2),(0,0)])
    def test_no_write_if_already_on(self):
        f=Fake();a=self.call(f,self.payload(True,2));self.assertEqual(a['phase'],'already_set');self.assertFalse(a['physical_transition']);self.assertFalse(f.writes);self.assertFalse(m.commissioned(m.load_state(self.path)))
    def test_not_commissioned(self):
        f=Fake();self.assertEqual(self.call(f,self.payload(False,2,False))['reason'],'NOT_COMMISSIONED');self.assertFalse(f.writes)
    def test_foreign(self):
        f=Fake();self.assertEqual(self.call(f,identity={})['reason'],'IDENTITY_MISMATCH');self.assertFalse(f.calls)
    def test_invalid_payload(self):
        for k,v in [('expected_word',True),('desired_on',1),('safety_confirmed',False),('serial_number','other'),('profile','other'),('address',99),('expires_at',(self.clock-timedelta(seconds=1)).isoformat())]:
            f=Fake();p=self.payload();p[k]=v;self.assertEqual(self.call(f,p)['writes_attempted'],0,k);self.assertFalse(f.calls)
    def test_wrong_state_mode_and_other_bits(self):
        for f,p in [(Fake(2),self.payload(True,0)),(Fake(0,mode=2),self.payload()),(Fake(6),self.payload(False,2))]:
            self.assertEqual(self.call(f,p)['writes_attempted'],0);self.assertFalse(f.writes)
    def test_defrost_and_cold(self):
        for f in [Fake(prot=2),Fake(prot=4),Fake(loads=32768),Fake(prot=65535),Fake(temps=(65533,35)),Fake(temps=(9,35))]:
            self.assertEqual(self.call(f,self.payload(False,2))['writes_attempted'],0);self.assertFalse(f.writes)
    def test_uncertain_never_retries(self):
        f=Fake(0,ack=False);a=self.call(f);self.assertEqual(a['phase'],'uncertain');self.assertEqual(f.writes,[(0,2)]);self.assertFalse(a['readback_verified']);self.assertFalse(m.commissioned(m.load_state(self.path)))
        self.clock+=timedelta(seconds=31);b=self.call(f,self.payload(False,2));self.assertEqual(b['writes_attempted'],0)
        c=self.call(f,self.payload(True,2));self.assertEqual(c['phase'],'already_set');self.assertEqual(m.load_state(self.path)['last_attempt']['phase'],'observed_on');self.assertEqual(len(f.writes),1)
    def test_readback_mismatch(self):
        f=Fake(0,change=False);self.assertEqual(self.call(f)['phase'],'uncertain');self.assertEqual(len(f.writes),1)
    def test_persist_before_write(self):
        f=Fake(0);old=f.write_register
        def write(**k):self.assertEqual(m.load_state(self.path)['last_attempt']['phase'],'attempted');return old(**k)
        f.write_register=write;self.assertEqual(self.call(f)['phase'],'confirmed')
    def test_corrupt_journal(self):
        self.path.write_text('bad');f=Fake(0);self.assertEqual(self.call(f)['writes_attempted'],0);self.assertFalse(f.calls)
    def test_replay_cooldown(self):
        f=Fake(0);cid=str(uuid4());self.assertEqual(self.call(f,cid=cid)['phase'],'confirmed');self.assertEqual(self.call(f,self.payload(False,2))['reason'],'COOLDOWN_OR_CLOCK_ERROR');self.clock+=timedelta(seconds=31);self.assertEqual(self.call(f,self.payload(False,2),cid=cid)['reason'],'REPLAY_REJECTED');self.assertEqual(len(f.writes),1)
    def test_local_identity_or_ftdi(self):
        for k in ['identity_check','port_check']:
            f=Fake(0);self.assertEqual(self.call(f,**{k:lambda i:False})['writes_attempted'],0);self.assertFalse(f.calls)
    def test_read_failure(self):
        f=Fake(0);f.bad_reads=True;self.assertEqual(self.call(f)['reason'],'FC03_READ_FAILED');self.assertFalse(f.writes)

if __name__=='__main__':unittest.main()
