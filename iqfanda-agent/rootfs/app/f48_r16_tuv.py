"""F48 R16 kandidat: omezene potvrzovani TUV prikazu pouze modelu 00012.

Pred nasazenim vyzaduje integracni test; neni nahrazkou HW tepelnych ochran.
Import nema sitove ani akcnici vedlejsi ucinky. Zakaz opakovani nejisteho POST.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Any, Callable
from urllib import request, error

UUID_00012 = '28fb44c3-4f8d-4ea4-90ab-f7e6a3b509fc'
ZDROJ = 'phase32_dhw_thermostat'
BIVALENCE = 'switch.sonoff_zbminil2_3'
TC1 = 'light.tz3000_kqvb5akv_ts0001'
TC2 = 'light.tz3000_kqvb5akv_ts0001_2'
VYSTUPY = {
    BIVALENCE: ('b0:e8:e8:ff:fe:66:2f:fd', 'bivalence'),
    TC1: ('a4:c1:38:90:9a:42:09:7f', 'heat_pump'),
    TC2: ('a4:c1:38:b5:45:cf:8a:c8', 'heat_pump'),
}
API = 'http://supervisor/core/api'
MAX_DOBA_S = 75.0
MAX_HTTP_S = 10.0
REZERVA_S = 3.0

# POST /template pouze cte HA; neni to volani sluzby ani zapis stavu.
SABLONA = """{% set ns = namespace(radky=[]) %}
{% for e in ENTITIES %}
{% set d = device_id(e) %}{% set s = states[e] %}
{% set ns.radky = ns.radky + [{
'entity_id': e, 'device_reg_id': d,
'identifiers': (device_attr(d, 'identifiers') or []) | list,
'state': states(e),
'last_changed': s.last_changed.isoformat() if s is not none and s is defined else none
}] %}
{% endfor %}
{{ {'cas': now().isoformat(), 'radky': ns.radky} | tojson }}""".replace('ENTITIES', json.dumps(list(VYSTUPY)))


class ChybaTuv(RuntimeError):
    pass


class HaStartuje(ChybaTuv):
    pass


class TransportNejisty(ChybaTuv):
    pass


def utc(hodnota: object) -> datetime:
    try:
        cas = hodnota if isinstance(hodnota, datetime) else datetime.fromisoformat(str(hodnota).replace('Z', '+00:00'))
        if cas.tzinfo is None or cas.utcoffset() is None:
            raise ValueError('timezone')
        return cas.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ChybaTuv('F48_R16_NEPLATNY_CAS') from exc


def over(podminka: bool, kod: str) -> None:
    if not podminka:
        raise ChybaTuv(kod)


class LokalniHA:
    def __init__(self) -> None:
        self.monotonic = time.monotonic
        self.spi = time.sleep
        self.ted = lambda: datetime.now(timezone.utc)

    def pozadavek(self, cesta: str, metoda: str, telo: dict | None, limit: float) -> Any:
        token = os.environ.get('SUPERVISOR_TOKEN', '').strip()
        over(bool(token), 'F48_R16_CHYBI_SUPERVISOR_TOKEN')
        data = json.dumps(telo).encode('utf-8') if telo is not None else None
        req = request.Request(API + cesta, data=data, method=metoda, headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
            'Accept': 'application/json', 'User-Agent': 'TNG-IQ-FANDA-F48-R16',
        })
        try:
            with request.urlopen(req, timeout=limit) as odpoved:
                raw = odpoved.read(131073)
                over(len(raw) <= 131072, 'F48_R16_ODPOVED_PRILIS_VELKA')
                return json.loads(raw.decode('utf-8')) if raw.strip() else None
        except error.HTTPError as exc:
            # Pouze presna odmitnuta odpoved setup, nikoli libovolna HTTP 400.
            try:
                zprava = json.loads(exc.read(4096).decode('utf-8'))
            except (ValueError, UnicodeError):
                zprava = {}
            if (exc.code == 400 and isinstance(zprava, dict)
                    and zprava.get('result') == 'error'
                    and zprava.get('message') == 'System is not ready with state: setup'):
                raise HaStartuje('F48_R16_HA_SETUP_ODMITNUTO') from exc
            raise ChybaTuv('F48_R16_HA_HTTP_' + str(exc.code)) from exc
        except (TimeoutError, error.URLError, ConnectionError) as exc:
            raise TransportNejisty('F48_R16_HA_TRANSPORT_' + type(exc).__name__) from exc
        except (ValueError, UnicodeError) as exc:
            raise ChybaTuv('F48_R16_HA_NEPLATNA_ODPOVED') from exc


def cilovy_prikaz(identita: dict, prikaz: dict) -> bool:
    return (isinstance(identita, dict) and isinstance(prikaz, dict)
            and identita.get('device_uuid') == UUID_00012
            and prikaz.get('source') == ZDROJ and prikaz.get('entity_id') in VYSTUPY)


def proved(prikaz: dict, ha: Any, stopa: dict) -> dict:
    """Vykona jeden schvaleny prikaz. Pri nejistem POST uz jen cte, nikdy ho neopakuje."""
    entita = prikaz.get('entity_id')
    over(entita in VYSTUPY, 'F48_R16_JINA_ENTITA')
    ieee, rezim = VYSTUPY[entita]
    cil = prikaz.get('target_state')
    over(cil in ('on', 'off'), 'F48_R16_NEPLATNY_CIL')
    over(str(prikaz.get('ieee') or '').lower() == ieee, 'F48_R16_JINE_IEEE')
    over(prikaz.get('source') == ZDROJ, 'F48_R16_JINY_ZDROJ')
    over(prikaz.get('dhw_source') in ((rezim, 'all_sources') if cil == 'off' else (rezim,)), 'F48_R16_JINY_REZIM')
    registr = prikaz.get('device_reg_id')
    over(isinstance(registr, str) and bool(registr.strip()), 'F48_R16_CHYBI_REGISTR')
    konec_utc = utc(prikaz.get('deadline_at'))
    zbyva = (konec_utc - ha.ted()).total_seconds() - REZERVA_S
    over(zbyva > 1, 'F48_R16_PRIKAZ_EXPIROVAL')
    konec_mono = ha.monotonic() + min(MAX_DOBA_S, zbyva)
    stopa.update(faze='pripravenost_ha', fyzicky_post=0, odmitnute_setup=0, cteni=0)

    def limit() -> float:
        rezerva = min(konec_mono - ha.monotonic(), (konec_utc - ha.ted()).total_seconds() - REZERVA_S)
        over(rezerva > 0.1, 'F48_R16_CASOVY_LIMIT')
        return min(MAX_HTTP_S, rezerva)

    def pauza() -> None:
        ha.spi(min(1.0, limit()))

    def cti(cesta: str, telo: dict | None = None) -> Any:
        while True:
            try:
                stopa['cteni'] += 1
                return ha.pozadavek(cesta, 'GET' if telo is None else 'POST', telo, limit())
            except (HaStartuje, TransportNejisty):
                pauza()

    def snapshot() -> dict:
        data = cti('/template', {'template': SABLONA})
        over(isinstance(data, dict) and isinstance(data.get('radky'), list), 'F48_R16_SNAPSHOT_FORMAT')
        utc(data.get('cas'))
        radky = data['radky']
        over(len(radky) == 3 and all(isinstance(r, dict) for r in radky), 'F48_R16_SNAPSHOT_POCET')
        index = {r.get('entity_id'): r for r in radky}
        over(set(index) == set(VYSTUPY), 'F48_R16_SNAPSHOT_ENTITY')
        for e, r in index.items():
            # ON vyzaduje jistotu protilehleho zdroje; OFF neblokovat poruchou jineho vystupu.
            kontrolovat = e == entita or (cil == 'on' and (entita == BIVALENCE or e == BIVALENCE))
            if not kontrolovat:
                continue
            identifikatory = r.get('identifiers')
            over(isinstance(identifikatory, list), 'F48_R16_REGISTR_IEEE_CHYBI')
            shody = [i for i in identifikatory if isinstance(i, (list, tuple)) and len(i) == 2
                     and i[0] == 'zha' and str(i[1]).lower() == VYSTUPY[e][0]]
            over(len(shody) == 1 and bool(r.get('device_reg_id')), 'F48_R16_REGISTR_IEEE_NESHODA')
            if e == entita:
                over(r['device_reg_id'] == registr, 'F48_R16_REGISTR_ID_NESHODA')
            elif cil == 'on':
                over(r.get('state') == 'off', 'F48_R16_PROTILEHLY_ZDROJ_NENI_OFF')
        return {'cas': utc(data['cas']), 'radky': index}

    while True:
        stopa['faze'] = 'pripravenost_ha'
        pripraven = cti('/config')
        over(isinstance(pripraven, dict), 'F48_R16_KONFIG_FORMAT')
        if str(pripraven.get('state', '')).lower() != 'running':
            pauza()
            continue
        stopa['faze'] = 'predbezne_cteni'
        pred = snapshot()
        stav_pred = pred['radky'][entita].get('state')
        over(stav_pred in ('on', 'off') or cil == 'off', 'F48_R16_ON_NEZNA_PUVODNI_STAV')
        if stav_pred == cil:
            limit()
            return {'previous_state': stav_pred, 'requested_state': cil, 'readback_state': cil,
                    'readback_verified': True, 'service_called': False, 'service': None,
                    'verification': 'f48_r16_cerstve_cteni_bez_noveho_povelu'}
        # Odmitnuti setup je jiste neprovedeni v gateway. Pred dalsim pokusem znovu cele kontroly.
        stopa['faze'] = 'sluzba_' + cil
        try:
            limit_post = limit()
            stopa['fyzicky_post'] += 1
            ha.pozadavek('/services/' + entita.split('.')[0] + '/turn_' + cil,
                         'POST', {'entity_id': entita}, limit_post)
        except HaStartuje:
            stopa['odmitnute_setup'] += 1
            over(stopa['odmitnute_setup'] < 3, 'F48_R16_SETUP_TRVA')
            pauza()
            continue
        except TransportNejisty:
            stopa['transport_po_povelu'] = 'nejisty'
        break

    stopa['faze'] = 'overeni_' + cil
    stary_cas = pred['radky'][entita].get('last_changed')
    while True:
        zpet = snapshot()
        stav = zpet['radky'][entita]
        if stav.get('state') == cil:
            novy_cas = utc(stav.get('last_changed'))
            # Dve HA casova razitka ve stejne casove zakladne; lokalni hodiny se tu nepouziji.
            over(novy_cas >= pred['cas'] - timedelta(seconds=2), 'F48_R16_STARY_STAV_NENI_DUKAZ')
            over(novy_cas <= zpet['cas'] + timedelta(seconds=1), 'F48_R16_BUDOUCI_STAV_NENI_DUKAZ')
            if stary_cas:
                over(novy_cas > utc(stary_cas), 'F48_R16_CHYBI_NOVY_PRECHOD')
            over(zpet['cas'] >= pred['cas'], 'F48_R16_HA_HODINY_ZPET')
            limit()
            return {'previous_state': stav_pred, 'requested_state': cil, 'readback_state': cil,
                    'readback_verified': True, 'service_called': True,
                    'service': entita.split('.')[0] + '.turn_' + cil,
                    'readback_changed_at': novy_cas.isoformat(),
                    'verification': 'f48_r16_novy_overeny_prechod'}
        pauza()


def vytvor_vykonavac(puvodni: Callable, odesli: Callable, ha_factory: Callable = LokalniHA) -> Callable:
    @wraps(puvodni)
    def vykonej(*, identity: dict, command_id: str, command_payload: dict) -> None:
        if not cilovy_prikaz(identity, command_payload):
            return puvodni(identity=identity, command_id=command_id, command_payload=command_payload)
        zaklad = {'worker': 'command_worker', 'executor': 'zigbee_switch_set',
                  'capability': 'binary_power_output', 'entity_id': command_payload.get('entity_id'),
                  'ieee': command_payload.get('ieee'), 'device_reg_id': command_payload.get('device_reg_id'),
                  'source': ZDROJ, 'manual_override': False, 'fanda_has_priority': True,
                  'target_state': command_payload.get('target_state'), 'f48_r16': True}
        stopa: dict = {}
        # Reportovaci transport neni soucasti try pro fyzicky prikaz: pri jeho chybe zadny druhy povel.
        odesli(identity=identity, command_id=command_id, status='running',
               result={**zaklad, 'phase': 'f48_r16_priprava'}, error_message=None)
        try:
            dukaz = proved(command_payload, ha_factory(), stopa)
        except Exception as exc:
            logging.error('F48_R16 TUV 00012 failed phase=%s type=%s', stopa.get('faze'), type(exc).__name__)
            odesli(identity=identity, command_id=command_id, status='failed',
                   result={**zaklad, 'phase': 'switch_control_failed', 'diagnostika': stopa,
                           'error_type': type(exc).__name__},
                   error_message=str(exc) if isinstance(exc, ChybaTuv) else 'F48_R16_INTERNI_CHYBA_' + type(exc).__name__)
            return
        odesli(identity=identity, command_id=command_id, status='succeeded',
               result={**zaklad, **dukaz, 'phase': 'switch_state_verified', 'diagnostika': stopa},
               error_message=None)
    return vykonej


def spustit() -> None:
    """Jediny vstup pred spustenim puvodnich vlaken; ostatni identity jdou puvodni cestou."""
    import command_worker
    import startup
    command_worker.execute_zigbee_switch_set = vytvor_vykonavac(
        command_worker.execute_zigbee_switch_set, command_worker.submit_command_result)
    startup.main()
