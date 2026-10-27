"""F48 R10 kandidat: dodatecne cteni HA stavu po timeoutu OFF, jen klient 00012.

Tato knihovna neodesila zadny povel rele ani sama neupravuje DB.
Musi byt vlozena do lokalniho Agenta a nasazena pouze pro 00012.
NENI urcena k primemu spusteni na VPS a NENI produkcne overena.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Any
import time

CIL_UUID = '28fb44c3-4f8d-4ea4-90ab-f7e6a3b509fc'
CIL_ENTITY = 'switch.sonoff_zbminil2_3'
CIL_IEEE = 'b0:e8:e8:ff:fe:66:2f:fd'
CIL_ZDROJ = 'phase32_dhw_thermostat'


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
    if str(prikaz.get('entity_id') or '').strip() != CIL_ENTITY:
        return None
    if str(prikaz.get('ieee') or '').strip().lower() != CIL_IEEE:
        return None
    if str(prikaz.get('target_state') or '').strip().lower() != 'off':
        return None
    if str(prikaz.get('dhw_source') or '').strip() not in ('all_sources', 'bivalence'):
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
            zpet = nacti_stav(CIL_ENTITY)
        except Exception:
            continue
        if not isinstance(zpet, dict):
            continue
        if str(zpet.get('entity_id') or '') != CIL_ENTITY:
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
            'service': 'switch.turn_off',
            'verification': 'f48_r10_fresh_ha_readback_after_timeout',
            'readback_changed_at': cas_zpet.isoformat(),
        }
    return None