"""Offline testy bez pristupu k siti, databazi nebo rele."""
import copy
import io
import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import f48_r16_tuv as m


class SimulaceHA:
    def __init__(self, entita=m.BIVALENCE, cil='on'):
        self.cas0 = datetime(2026, 10, 27, 18, 0, 0, tzinfo=timezone.utc)
        self.sekundy = 0.0
        self.entita, self.cil = entita, cil
        self.volani = []
        self.posty = 0
        self.akcni = 0
        self.setup_cteni = 0
        self.setup_post = 0
        self.post_timeout = False
        self.post_bez_zmeny = False
        self.chyba_cteni = 0
        self.chyba_post = None
        self.modifikator = None
        self.config_stav = 'RUNNING'
        self.radky = {e: {'entity_id': e, 'device_reg_id': 'reg-' + str(i),
                         'identifiers': [['zha', ieee]], 'state': 'off', 'restored': False,
                         'last_changed': (self.cas0 - timedelta(minutes=15)).isoformat()}
                      for i, (e, (ieee, _)) in enumerate(m.VYSTUPY.items())}
        self.radky[entita]['state'] = 'off' if cil == 'on' else 'on'

    def ted(self):
        return self.cas0 + timedelta(seconds=self.sekundy)

    def monotonic(self):
        return self.sekundy

    def spi(self, doba):
        self.sekundy += doba

    def pozadavek(self, cesta, metoda, telo, limit):
        self.volani.append((cesta, metoda))
        self.sekundy += min(0.05, limit)
        if cesta.startswith('/services/'):
            self.posty += 1
            if self.setup_post:
                self.setup_post -= 1
                raise m.HaStartuje('setup')
            if self.chyba_post:
                raise self.chyba_post
            self.akcni += 1
            if not self.post_bez_zmeny:
                self.radky[self.entita].update(state=self.cil, last_changed=self.ted().isoformat())
            if self.post_timeout:
                self.spi(min(15, limit))
                raise m.TransportNejisty('timed out')
            return []
        if self.setup_cteni:
            self.setup_cteni -= 1
            raise m.HaStartuje('setup')
        if self.chyba_cteni:
            self.chyba_cteni -= 1
            raise m.TransportNejisty('timed out')
        if cesta == '/config':
            return {'state': self.config_stav}
        data = {'cas': self.ted().isoformat(), 'radky': copy.deepcopy(list(self.radky.values()))}
        if self.modifikator:
            self.modifikator(data, self)
        return data

    def payload(self):
        return {'entity_id': self.entita, 'ieee': m.VYSTUPY[self.entita][0],
                'source': m.ZDROJ, 'target_state': self.cil,
                'dhw_source': m.VYSTUPY[self.entita][1],
                'device_reg_id': self.radky[self.entita]['device_reg_id'],
                'deadline_at': (self.cas0 + timedelta(seconds=180)).isoformat()}


class TestR16(unittest.TestCase):
    def uspech(self, ha, **zmeny):
        p = ha.payload(); p.update(zmeny)
        stopa = {}
        r = m.proved(p, ha, stopa)
        self.assertTrue(r['readback_verified'])
        self.assertEqual(r['readback_state'], p['target_state'])
        return r, stopa

    def test_01_bezne_ON_OFF_vsechny_vystupy(self):
        for entita in m.VYSTUPY:
            for cil in ('on', 'off'):
                with self.subTest(entita=entita, cil=cil):
                    ha = SimulaceHA(entita, cil)
                    self.uspech(ha)
                    self.assertEqual(ha.akcni, 1)

    def test_02_timeout_POST_ON_OFF_potvrzeny_bez_druheho_povelu(self):
        for entita in m.VYSTUPY:
            for cil in ('on', 'off'):
                with self.subTest(entita=entita, cil=cil):
                    ha = SimulaceHA(entita, cil); ha.post_timeout = True
                    self.uspech(ha)
                    self.assertEqual(ha.posty, 1)

    def test_03_timeout_neznamena_uspech(self):
        for cil in ('on', 'off'):
            with self.subTest(cil=cil):
                ha = SimulaceHA(cil=cil); ha.post_timeout = True; ha.post_bez_zmeny = True
                with self.assertRaises(m.ChybaTuv): m.proved(ha.payload(), ha, {})
                self.assertEqual(ha.posty, 1)
                self.assertLess(ha.sekundy, 76)

    def test_04_setup_pred_povelem(self):
        ha = SimulaceHA(); ha.setup_cteni = 5
        self.uspech(ha)
        self.assertEqual(ha.akcni, 1)
        self.assertGreater(ha.sekundy, 5)

    def test_05_setup_trva_bez_povelu(self):
        ha = SimulaceHA(); ha.setup_cteni = 1000
        with self.assertRaises(m.ChybaTuv): m.proved(ha.payload(), ha, {})
        self.assertEqual(ha.posty, 0)

    def test_06_timeout_prvniho_cteni(self):
        ha = SimulaceHA(); ha.chyba_cteni = 3
        self.uspech(ha)
        self.assertEqual(ha.akcni, 1)

    def test_07_setup_presne_odmitnuty_POST(self):
        ha = SimulaceHA(); ha.setup_post = 1
        self.uspech(ha)
        self.assertEqual((ha.posty, ha.akcni), (2, 1))

    def test_08_setup_opakovane_odmitnuti(self):
        ha = SimulaceHA(); ha.setup_post = 100
        with self.assertRaises(m.ChybaTuv): m.proved(ha.payload(), ha, {})
        self.assertEqual((ha.posty, ha.akcni), (3, 0))

    def test_09_jine_HTTP_neopakovane(self):
        for kod in (400, 401, 403, 404, 500, 503):
            with self.subTest(kod=kod):
                ha = SimulaceHA(); ha.chyba_post = m.ChybaTuv('HTTP ' + str(kod))
                with self.assertRaises(m.ChybaTuv): m.proved(ha.payload(), ha, {})
                self.assertEqual(ha.posty, 1)

    def test_10_neplatna_pole_pred_siti(self):
        for zmena in ({'ieee':'jine'}, {'target_state':'toggle'}, {'source':'other'},
                      {'dhw_source':'all_sources'}, {'device_reg_id':''},
                      {'deadline_at':'2026-10-27T18:02:00'}, {'entity_id':'switch.other'}):
            with self.subTest(zmena=zmena):
                ha = SimulaceHA(); p=ha.payload(); p.update(zmena)
                with self.assertRaises(m.ChybaTuv): m.proved(p, ha, {})
                self.assertEqual(ha.volani, [])

    def test_11_deadline_dodrzen(self):
        ha = SimulaceHA(); ha.setup_cteni = 1000
        p = ha.payload(); p['deadline_at']=(ha.cas0+timedelta(seconds=8)).isoformat()
        with self.assertRaises(m.ChybaTuv): m.proved(p, ha, {})
        self.assertLess(ha.sekundy, 8)
        self.assertEqual(ha.posty, 0)

    def test_12_jiz_expirovany(self):
        ha = SimulaceHA(); p=ha.payload(); p['deadline_at']=(ha.cas0-timedelta(seconds=1)).isoformat()
        with self.assertRaises(m.ChybaTuv): m.proved(p, ha, {})
        self.assertEqual(ha.volani, [])

    def test_13_ON_blokace_protilehleho_zdroje(self):
        for e, cizi in ((m.BIVALENCE,m.TC1),(m.BIVALENCE,m.TC2),(m.TC1,m.BIVALENCE),(m.TC2,m.BIVALENCE)):
            for stav in ('on','unknown','unavailable'):
                with self.subTest(e=e,cizi=cizi,stav=stav):
                    ha=SimulaceHA(e); ha.radky[cizi]['state']=stav
                    with self.assertRaises(m.ChybaTuv): m.proved(ha.payload(),ha,{})
                    self.assertEqual(ha.posty,0)

    def test_14_TC_par_muze_postupne_ON(self):
        ha=SimulaceHA(m.TC2);ha.radky[m.TC1]['state']='on'
        self.uspech(ha)

    def test_15_OFF_neblokovat_cizim_ON(self):
        ha=SimulaceHA(m.BIVALENCE,'off'); ha.radky[m.TC1]['state']='on'
        self.uspech(ha)

    def test_16_registr_neshoda(self):
        ha=SimulaceHA(); p=ha.payload(); p['device_reg_id']='jiny'
        with self.assertRaises(m.ChybaTuv): m.proved(p,ha,{})
        self.assertEqual(ha.posty,0)

    def test_17_IEEE_neshoda_v_HA(self):
        ha=SimulaceHA();ha.radky[m.TC1]['identifiers']=[['zha','00']]
        with self.assertRaises(m.ChybaTuv): m.proved(ha.payload(),ha,{})
        self.assertEqual(ha.posty,0)

    def test_18_stary_stav_nepotvrdit(self):
        ha=SimulaceHA(); ha.post_timeout=True
        def stary(data,h):
            if h.akcni:
                data['radky'][0]['last_changed']=(h.cas0-timedelta(minutes=2)).isoformat()
        ha.modifikator=stary
        with self.assertRaisesRegex(m.ChybaTuv,'STARY_STAV'):m.proved(ha.payload(),ha,{})
        self.assertEqual(ha.posty,1)

    def test_19_budouci_stav_nepotvrdit(self):
        ha=SimulaceHA()
        def zmena(data,h):
            if h.akcni:data['radky'][0]['last_changed']=(h.ted()+timedelta(seconds=10)).isoformat()
        ha.modifikator=zmena
        with self.assertRaisesRegex(m.ChybaTuv,'BUDOUCI_STAV'):m.proved(ha.payload(),ha,{})

    def test_20_soubeh_behem_potvrzovani_nepotvrdit(self):
        ha=SimulaceHA()
        def zmena(data,h):
            if h.akcni:data['radky'][1]['state']='on'
        ha.modifikator=zmena
        with self.assertRaisesRegex(m.ChybaTuv,'PROTILEHLY'):m.proved(ha.payload(),ha,{})

    def test_21_shodny_stav_bez_akce(self):
        for cil in ('on','off'):
            with self.subTest(cil=cil):
                ha=SimulaceHA(cil=cil);ha.radky[ha.entita]['state']=cil
                r,_=self.uspech(ha)
                self.assertFalse(r['service_called']);self.assertEqual(ha.posty,0)

    def test_22_neplatny_snapshot(self):
        ha=SimulaceHA();ha.modifikator=lambda d,h:d['radky'].pop()
        with self.assertRaises(m.ChybaTuv):m.proved(ha.payload(),ha,{})
        self.assertEqual(ha.posty,0)

    def test_23_ostatni_klienti_delegovani(self):
        for identita in ({'device_uuid':'00013'}, {}, {'device_uuid':None}):
            calls=[]
            puvodni=lambda **k:calls.append(k)
            wrap=m.vytvor_vykonavac(puvodni,lambda **k:self.fail('Nesmi reportovat'),lambda:self.fail('Nesmi HA'))
            p=SimulaceHA().payload()
            wrap(identity=identita,command_id='x',command_payload=p)
            self.assertEqual(calls,[dict(identity=identita,command_id='x',command_payload=p)])

    def test_24_jine_prikazy_00012_delegovane(self):
        for p in ({'entity_id':'switch.other','source':m.ZDROJ}, {'entity_id':m.BIVALENCE,'source':'manual'}):
            calls=[];f=m.vytvor_vykonavac(lambda **k:calls.append(k),lambda **k:self.fail('report'))
            f(identity={'device_uuid':m.UUID_00012},command_id='x',command_payload=p)
            self.assertEqual(len(calls),1)

    def test_25_report_uspechu_a_poruchy(self):
        for selhat in (False,True):
            ha=SimulaceHA();ha.post_bez_zmeny=selhat
            vysledky=[]
            f=m.vytvor_vykonavac(lambda **k:self.fail('puvodni'),lambda **k:vysledky.append(k),lambda:ha)
            f(identity={'device_uuid':m.UUID_00012},command_id='x',command_payload=ha.payload())
            self.assertEqual([v['status'] for v in vysledky],['running','failed' if selhat else 'succeeded'])
            self.assertEqual(ha.posty,1)

    def test_26_chyba_odeslani_vysledku_neopakuje_povel(self):
        ha=SimulaceHA();volani=[]
        def odesli(**k):
            volani.append(k)
            if k['status']=='succeeded':raise RuntimeError('cloud offline')
        f=m.vytvor_vykonavac(lambda **k:None,odesli,lambda:ha)
        with self.assertRaises(RuntimeError):f(identity={'device_uuid':m.UUID_00012},command_id='x',command_payload=ha.payload())
        self.assertEqual(ha.posty,1)
        self.assertEqual([v['status'] for v in volani],['running','succeeded'])

    def test_27_HTTP_classification(self):
        with patch.dict('os.environ',{'SUPERVISOR_TOKEN':'test-only'}):
            for status, body, cls in (
                (400,{'result':'error','message':'System is not ready with state: setup'},m.HaStartuje),
                (400,{'result':'error','message':'some other setup text'},m.ChybaTuv),
                (401,{'result':'error','message':'System is not ready with state: setup'},m.ChybaTuv),
                (500,{},m.ChybaTuv)):
                with self.subTest(status=status,body=body):
                    err=HTTPError('http://supervisor/core/api/config',status,'x',{},io.BytesIO(json.dumps(body).encode()))
                    with patch('urllib.request.urlopen',side_effect=err):
                        try:m.LokalniHA().pozadavek('/config','GET',None,1)
                        except Exception as exc:self.assertEqual(type(exc),cls)
                        else:self.fail('exception required')

    def test_28_template_render(self):
        import jinja2
        from types import SimpleNamespace
        ha=SimulaceHA()
        class States:
            def __getitem__(self,e):return SimpleNamespace(last_changed=m.utc(ha.radky[e]['last_changed']))
            def __call__(self,e):return ha.radky[e]['state']
        env=jinja2.Environment()
        out=env.from_string(m.SABLONA).render(states=States(), device_id=lambda e:ha.radky[e]['device_reg_id'],
                 device_attr=lambda d,a:next(r for r in ha.radky.values() if r['device_reg_id']==d)[a],
                 state_attr=lambda e,a:ha.radky[e].get(a), now=ha.ted)
        data=json.loads(out)
        self.assertEqual(len(data['radky']),3)
        self.assertEqual(data['radky'][0]['identifiers'],[['zha',m.VYSTUPY[m.BIVALENCE][0]]])

    def test_29_config_stale_state_no_actuation(self):
        ha=SimulaceHA();ha.config_stav='STARTING'
        with self.assertRaises(m.ChybaTuv):m.proved(ha.payload(),ha,{})
        self.assertEqual(ha.posty,0)

    def test_30_import_and_registration_have_no_actuation(self):
        calls=[]
        m.vytvor_vykonavac(lambda **k:calls.append(k),lambda **k:calls.append(k))
        self.assertEqual(calls,[])

    def test_31_obnoveny_stav_nepouzivat(self):
        ha=SimulaceHA();ha.radky[m.TC1]['restored']=True
        with self.assertRaisesRegex(m.ChybaTuv,'OBNOVENY_STAV'):m.proved(ha.payload(),ha,{})
        self.assertEqual(ha.posty,0)

    def test_32_obnoveny_stav_po_POST_nepotvrdit(self):
        ha=SimulaceHA();ha.post_timeout=True
        def zmena(data,h):
            if h.akcni:data['radky'][0]['restored']=True
        ha.modifikator=zmena
        with self.assertRaisesRegex(m.ChybaTuv,'OBNOVENY_STAV'):m.proved(ha.payload(),ha,{})
        self.assertEqual(ha.posty,1)

    def test_33_setup_po_POST_nevydavat_za_potvrzeni(self):
        ha=SimulaceHA();puvodni=ha.pozadavek
        def api(cesta,metoda,telo,limit):
            r=puvodni(cesta,metoda,telo,limit)
            if cesta.startswith('/services/'):ha.setup_cteni=1
            return r
        ha.pozadavek=api
        with self.assertRaisesRegex(m.ChybaTuv,'SETUP_PO_NEJISTEM'):m.proved(ha.payload(),ha,{})
        self.assertEqual(ha.posty,1)

    def test_34_startup_registrace_pred_vlakny(self):
        import sys
        import types
        calls=[]
        old=lambda **kw:calls.append(kw)
        worker=types.SimpleNamespace(execute_zigbee_switch_set=old,submit_command_result=lambda **kw:None)
        startup=types.SimpleNamespace(main=lambda:calls.append('startup'))
        with patch.dict(sys.modules,{'command_worker':worker,'startup':startup}):
            m.spustit()
        self.assertEqual(calls,['startup'])
        self.assertIsNot(worker.execute_zigbee_switch_set,old)
        worker.execute_zigbee_switch_set(identity={'device_uuid':'00013'},command_id='x',command_payload={})
        self.assertEqual(calls[1]['command_id'],'x')

if __name__=='__main__':
    unittest.main(verbosity=2)
