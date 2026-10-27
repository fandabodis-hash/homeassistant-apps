from datetime import datetime, timezone, timedelta
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('f48r10', '/mnt/data/f48_r10_00012_off_readback.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
now=datetime(2026,10,27,10,0,0,tzinfo=timezone.utc)
before={'entity_id':m.CIL_ENTITY,'state':'on','last_changed':(now-timedelta(minutes=23)).isoformat()}
valid={'entity_id':m.CIL_ENTITY,'state':'off','last_changed':(now-timedelta(seconds=8)).isoformat()}
payload={'source':m.CIL_ZDROJ,'entity_id':m.CIL_ENTITY,'ieee':m.CIL_IEEE,'target_state':'off','dhw_source':'all_sources'}
kw={'identita':{'device_uuid':m.CIL_UUID},'prikaz':payload,'pred_stavem':before,'chyba':TimeoutError('timed out'),'registr_id':'dev-reg-00012','lokalni_id':'dev-reg-00012','pokus_o_povel':True,'nacti_stav':lambda _:valid,'ted':lambda:now,'spi':lambda _:None}

def chk(name, expected, **update):
    k=dict(kw);k.update(update)
    result=m.overit_off_po_timeoutu(**k)
    assert (result is not None)==expected,(name,result)
    print('PASS',name)

chk('validni readback po timeoutu',True)
chk('jiny klient 00013',False,identita={'device_uuid':'014a0d23-d440-4131-abb4-28a860595043'})
chk('neznama identita',False,identita={})
chk('chybejici registr',False,registr_id='')
chk('jine registr id',False,lokalni_id='other')
chk('povel nebyl odeslan',False,pokus_o_povel=False)
chk('bez timeoutu',False,chyba=RuntimeError('error'))
chk('odlisny timeout text',False,chyba=TimeoutError('HTTP timeout'))
chk('jina entita',False,prikaz={**payload,'entity_id':'switch.sonoff_mini_zb2gs_spinac_6'})
chk('jine ieee',False,prikaz={**payload,'ieee':'x'})
chk('jiny source',False,prikaz={**payload,'source':'other'})
chk('povel ON',False,prikaz={**payload,'target_state':'on'})
chk('neznamy predchozi stav',False,pred_stavem={'state':'unknown'})
chk('predtim uz off',False,pred_stavem={'state':'off','last_changed':before['last_changed']})
chk('nezname last_changed',False,pred_stavem={'state':'on'})
chk('stary off',False,nacti_stav=lambda _: {**valid,'last_changed':(now-timedelta(minutes=60)).isoformat()})
chk('stale on',False,nacti_stav=lambda _: {**valid,'state':'on'})
chk('nespravna entita v odpovedi',False,nacti_stav=lambda _: {**valid,'entity_id':'switch.other'})
chk('cas zpet pred puvodnim ON',False,nacti_stav=lambda _: {**valid,'last_changed':(now-timedelta(minutes=24)).isoformat()})
chk('budouci timestamp vice jak 5s',False,nacti_stav=lambda _: {**valid,'last_changed':(now+timedelta(seconds=15)).isoformat()})
chk('nepodlozeny cas tz',False,nacti_stav=lambda _: {**valid,'last_changed':'2026-10-27T10:00:00'})
chk('jinny režim TUV',False,prikaz={**payload,'dhw_source':'fireplace'})
reads=[]
def slow_read(_):
    reads.append('get')
    if len(reads)==1:raise TimeoutError('timed out')
    return valid
chk('prvni GET neodpovedel druhy OFF',True,nacti_stav=slow_read)
assert len(reads)==2
reads=[]
chk('dva GET timeouty bez akce',False,nacti_stav=lambda _: (reads.append('get'),(_ for _ in ()).throw(TimeoutError('timed out')))[1])
assert len(reads)==2
print('TESTS_PASS=24')