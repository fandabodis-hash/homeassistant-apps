"""F48 R14: HA readback after timeout of 3 exact TUV outputs, device 00012 only.

Never sends another relay command or rearms a fault. All ambiguous observations
retain the original failed result. No other client identity is permitted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Any
import time

CIL_UUID = '28fb44c3-4f8d-4ea4-90ab-f7e6a3b509fc'
CIL_ENTITY = 'switch.sonoff_zbminil2_3'
CIL_IEEE = 'b0:e8:e8:ff:fe:66:2f:fd'
CIL_ZDROJ = 'phase32_dhw_thermostat'
# Single device: exact HA entities, Zigbee IEEE and allowed TUV modes.
CIL_VYSTUPY = {
    CIL_ENTITY: (CIL_IEEE, frozenset({'all_sources', 'bivalence'}), 'switch'),
    'light.tz3000_kqvb5akv_ts0001': (
        'a4:c1:38:90:9a:42:09:7f',
        frozenset({'all_sources', 'heat_pump'}), 'light'),
    'light.tz3000_kqvb5akv_ts0001_2': (
        'a4:c1:38:b5:45:cf:8a:c8',
        frozenset({'all_sources', 'heat_pump'}), 'light'),
}


def _utc(value: object) -> datetime | None:
    try:
        d = (value if isinstance(value, datetime) else
             datetime.fromisoformat(str(value).replace('Z', '+00:00')))
        if d.tzinfo is None or d.utcoffset() is None:
            return None
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def overit_off_po_timeoutu(
    *,
    identita: dict[str, Any],
    prikaz: dict[str, Any],
    pred_stavem: dict[str, Any],
    chyba: Exception,
    registr_id: str | None,
    lokalni_id: str | None,
    pokus_o_povel: bool,
    nacti_stav: Callable[[str], dict[str, Any]],
    ted: Callable[[], datetime] | None = None,
    spi: Callable[[float], None] | None = None,
) -> dict[str, Any] | None:
    """Vrati overeny OFF readback, jinak None. Nevykona zadny ON/OFF.

    Dvojite cteni povoleno pouze po skutecnem timeoutu uz odeslaneho OFF.
    Prvni GET musi prinaset prechod ON -> OFF s cerstvym HA last_changed.
    """
    if not isinstance(identita, dict) or not isinstance(prikaz, dict):
        return None
    if str(identita.get('device_uuid') or '').strip().lower() != CIL_UUID:
        return None
    if str(prikaz.get('source') or '').strip() != CIL_ZDROJ:
        return None
    entita = str(prikaz.get('entity_id') or '').strip()
    cil = CIL_VYSTUPY.get(entita)
    if cil is None:
        return None
    ieee, platne_zdroje, domena = cil
    if str(prikaz.get('ieee') or '').strip().lower() != ieee:
        return None
    if str(prikaz.get('target_state') or '').strip().lower() != 'off':
        return None
    if str(prikaz.get('dhw_source') or '').strip() not in platne_zdroje:
        return None
    if not registr_id or str(registr_id) != str(lokalni_id):
        return None
    if pokus_o_povel is not True or not isinstance(chyba, TimeoutError):
        return None
    if str(chyba).strip().lower() != 'timed out':
        return None
    if not isinstance(pred_stavem, dict):
        return None
    if str(pred_stavem.get('state') or '').strip().lower() != 'on':
        return None
    cas_pred = _utc(pred_stavem.get('last_changed'))
    if cas_pred is None:
        return None

    ted = ted or (lambda: datetime.now(timezone.utc))
    spi = spi or time.sleep
    for n in range(2):
        if n:
            spi(0.6)
        try:
            zpet = nacti_stav(entita)
        except Exception:
            continue
        if not isinstance(zpet, dict):
            continue
        if str(zpet.get('entity_id') or '') != entita:
            continue
        if str(zpet.get('state') or '').strip().lower() != 'off':
            continue
        cas_zpet = _utc(zpet.get('last_changed'))
        cas_nyni = _utc(ted())
        if cas_zpet is None or cas_nyni is None:
            continue
        vek = (cas_nyni - cas_zpet).total_seconds()
        # HA GET ma potvrdit NOVY prechod ON -> OFF. Neprijmout historicke OFF.
        if cas_zpet <= cas_pred or not (-5 <= vek <= 50):
            continue
        return {
            'verified': True,
            'readback_state': 'off',
            'readback_verified': True,
            'requested_state': 'off',
            'previous_state': 'on',
            'service_called': True,
            'service': f'{domena}.turn_off',
            'verification': 'f48_r14_00012_tuv_fresh_off_readback',
            'readback_entity_id': entita,
            'readback_changed_at': cas_zpet.isoformat(),
        }
    return None