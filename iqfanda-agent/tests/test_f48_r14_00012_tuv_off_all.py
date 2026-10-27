"""F48 R14 independent contracts: only 00012 and three pinned TUV OFF outputs."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import importlib.util
f = Path(__file__).resolve().parents[1] / 'rootfs/app/f48_r10_00012_off_readback.py'
spec=importlib.util.spec_from_file_location('r14',f); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
now=datetime(2026,10,27,17,21,30,tzinfo=timezone.utc)
past={'state':'on','last_changed':(now-timedelta(minutes=23)).isoformat()}
fresh=(now-timedelta(seconds=7)).isoformat()
b=('switch.sonoff_zbminil2_3','b0:e8:e8:ff:fe:66:2f:fd')
h1=('light.tz3000_kqvb5akv_ts0001','a4:c1:38:90:9a:42:09:7f')
h2=('light.tz3000_kqvb5akv_ts0001_2','a4:c1:38:b5:45:cf:8a:c8')
count=0
def check(title,item,mode,expected=True,**kw):
    global count
    entity,ieee=item
    p={'entity_id':entity,'ieee':ieee,'source':m.CIL_ZDROJ,'target_state':'off','dhw_source':mode}
    p.update(kw.get('p',{}))
    state=kw.get('state',{'entity_id':entity,'state':'off','last_changed':fresh})
    r=m.overit_off_po_timeoutu(
        identita=kw.get('id',{'device_uuid':m.CIL_UUID}),prikaz=p,
        pred_stavem=kw.get('prior',past),chyba=kw.get('error',TimeoutError('timed out')),
        registr_id=kw.get('reg','zz'),lokalni_id=kw.get('local','zz'),
        pokus_o_povel=kw.get('sent',True),nacti_stav=lambda _:state,
        ted=lambda:now,spi=lambda _:None)
    assert (r is not None)==expected,(title,r)
    if r:
        assert r['service']==entity.split('.')[0]+'.turn_off',title
        assert r['readback_entity_id']==entity,title
    count+=1;print('PASS',title)
for item,mode in [(b,'bivalence'),(h1,'heat_pump'),(h2,'heat_pump')]:
    entity=item[0]
    check(entity+' correct',item,mode)
    check(entity+' all_sources',item,'all_sources')
    check(entity+' wrong mode',item,'other',False)
    check(entity+' different customer',item,mode,False,id={'device_uuid':'00000000-0000-0000-0000-000000000013'})
    check(entity+' wrong IEEE',item,mode,False,p={'ieee':'x'})
    check(entity+' attempted ON',item,mode,False,p={'target_state':'on'})
    check(entity+' no command sent',item,mode,False,sent=False)
    check(entity+' exception different',item,mode,False,error=RuntimeError('timed out'))
    check(entity+' original was OFF',item,mode,False,prior={'state':'off','last_changed':past['last_changed']})
    check(entity+' old report',item,mode,False,state={'entity_id':entity,'state':'off','last_changed':(now-timedelta(minutes=35)).isoformat()})
    check(entity+' state still ON',item,mode,False,state={'entity_id':entity,'state':'on','last_changed':fresh})
check('HP mode cannot be bivalence',h1,'bivalence',False)
check('bivalence mode cannot be HP',b,'heat_pump',False)
check('registry mismatch',h2,'heat_pump',False,reg='other')
check('unlisted switch',('switch.other','xx'),'all_sources',False)
assert count==37,count
print('F48_R14_TESTS_PASS=37')
