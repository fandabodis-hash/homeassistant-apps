"""Rotek ECU-RTG3: omezeny read-only instalacni Modbus RTU probe.

Dulezite:
- Tento modul se nespousti sam a neni aktivni pri samotnem importu.
- Podporuje vyhradne FC03 cteni predem povolenych adres registru.
- Neni zde zadny Modbus zapis ani Zigbee actuator.
- Serialni komunikator se vybira podle inventare konkretniho Agenta.
- Kvuli soubehu je nutne zapojit probe do stavajiciho command workeru,
  ktery respektuje sdileny Modbus bus lock. Pri dalsim procesu je navic
  zapotrebi provozni exkluzivita na urovni hostitele.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from communication.modbus_bus_lock import ziskej_zamek_modbus_sbernice


PROFILE_ID = 'rotek_ecu_rtg3_readonly_v1'
COMMUNICATION_STATE = Path(os.getenv('IQF_COMMUNICATION_STATE_PATH','/config/communication.json'))
ALLOWED_REGISTER_ADDRESSES = (23, 24, 25, 26)
SERIAL_PATH_PATTERN = re.compile(r'^/dev/serial/by-id/[A-Za-z0-9_.+-]{8,180}$')
BAUDRATE = 9600
SLAVE_ID = 1
TIMEOUT_S = 1.0


class RotekProbeError(RuntimeError):
    """Bezpecne diagnosticke hlaseni bez hesel a konfiguracnich tajemstvi."""


def _load_inventory(path: Path) -> list[dict[str, Any]]:
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or not isinstance(data.get('communicators'),list):
        raise RotekProbeError('Komunikacni inventar Agenta neni dostupny.')
    return [c for c in data['communicators'] if isinstance(c,dict)]


def _str(value: Any) -> str:
    return value.strip() if isinstance(value,str) else ''


def validate_probe_input(payload: dict[str, Any], inventory: list[dict[str, Any]]) -> dict[str,Any]:
    if not isinstance(payload,dict):
        raise RotekProbeError('Pozadavek neni objekt.')
    if payload.get('read_only') is not True or payload.get('control_enabled') is not False:
        raise RotekProbeError('Jen read-only diagnostika bez ovladani.')
    if payload.get('probe_profile_id')!=PROFILE_ID:
        raise RotekProbeError('Neplatny Rotek profil.')
    if payload.get('communication_type')!='rs485' or payload.get('manufacturer')!='rotek':
        raise RotekProbeError('Generator nema potvrzenou RS485 konfiguraci Rotek.')
    if type(payload.get('slave_id')) is not int or payload['slave_id']!=SLAVE_ID:
        raise RotekProbeError('Nepovolena Modbus adresa jednotky.')
    if type(payload.get('baudrate')) is not int or payload['baudrate']!=BAUDRATE:
        raise RotekProbeError('Nepovolena rychlost Modbus RTU.')
    identity=_str(payload.get('communicator_id'))
    path=_str(payload.get('preferred_path'))
    serial=_str(payload.get('communicator_serial_number'))
    if not identity or not serial or not SERIAL_PATH_PATTERN.fullmatch(path):
        raise RotekProbeError('Neni urcena stabilni identita prevodniku.')
    matches=[c for c in inventory if _str(c.get('communicator_id'))==identity]
    if len(matches)!=1:
        raise RotekProbeError('Komunikator neni jednoznacny.')
    c=matches[0]
    stable=c.get('stable_paths')
    cap=c.get('capabilities')
    if c.get('connected') is not True or c.get('status')!='ready':
        raise RotekProbeError('Komunikator neni aktualne pripraven.')
    if _str(c.get('preferred_path'))!=path or not isinstance(stable,list) or path not in stable:
        raise RotekProbeError('Stabilni cesta neodpovida inventari.')
    if _str(c.get('serial_number'))!=serial:
        raise RotekProbeError('Seriove cislo prevodniku nesouhlasi.')
    if not isinstance(cap,list) or 'serial' not in cap or not ('rs485' in cap or 'rs485_or_rs232' in cap or 'modbus_rtu' in cap):
        raise RotekProbeError('Vybrane zarizeni nema RS485 moznosti.')
    if _str(c.get('type')) not in {'usb_serial','usb_rs485'}:
        raise RotekProbeError('Vybrany komunikator neni USB seriovy prevodnik.')
    physical=[x for x in inventory if isinstance(x,dict) and _str(x.get('preferred_path'))==path]
    # Neblokovat historicky chybne prirazenou cestu Generic USB Hub,
    # ale nesmi existovat vice skutecnych serialnich portu se stejnou cestou.
    serial_ports=[x for x in physical if _str(x.get('type')) in {'usb_serial','usb_rs485'}]
    if len(serial_ports)!=1 or _str(serial_ports[0].get('communicator_id'))!=identity:
        raise RotekProbeError('Fyzicky seriovy port neni jednoznacny.')
    return {'communicator_id':identity,'serial_number':serial,'preferred_path':path}


def _one_register(client: Any, addr: int) -> int:
    if addr not in ALLOWED_REGISTER_ADDRESSES:
        raise RotekProbeError('Nepovoleny registr.')
    try:
        response=client.read_holding_registers(address=addr,count=1,device_id=SLAVE_ID)
    except Exception as exc:
        raise RotekProbeError(f'Cteni registru {addr} selhalo: {type(exc).__name__}.') from None
    if response is None or response.isError():
        raise RotekProbeError(f'ECU neodpovedela na registr {addr}.')
    values=getattr(response,'registers',None)
    if not isinstance(values,(list,tuple)) or len(values)!=1 or type(values[0]) is not int or not (0<=values[0]<=65535):
        raise RotekProbeError(f'Neplatna odpoved registru {addr}.')
    return values[0]


def _decode(readings: dict[int,int]) -> dict[str, Any]:
    fuel=readings.get(23)
    battery=readings.get(24)
    freq=readings.get(25)
    rpm=readings.get(26)
    fuel_ok=type(fuel) is int and 0<=fuel<=100
    battery_ok=type(battery) is int and 90<=battery<=180
    freq_ok=type(freq) is int and 0<=freq<=700
    rpm_ok=type(rpm) is int and 0<=rpm<=3000
    return {
        'fuel_level_percent':fuel if fuel_ok else None,
        'fuel_level_status':'available' if fuel_ok else 'not_supported_or_invalid',
        'starter_battery_v':round(battery/10,1) if battery_ok else None,
        'frequency_hz':round(freq/10,1) if freq_ok else None,
        'engine_rpm':rpm if rpm_ok else None,
        'plausible':battery_ok and freq_ok and rpm_ok,
    }


def probe_rotek_ecu_rtg3_readonly(
    payload: dict[str,Any],
    *,
    inventory: list[dict[str,Any]] | None=None,
    client_factory: Callable[...,Any] | None=None,
) -> dict[str,Any]:
    """Pouze jednotlive FC03 pozadavky, zadna start/stop aktivace."""
    if inventory is None:
        inventory=_load_inventory(COMMUNICATION_STATE)
    chosen=validate_probe_input(payload,inventory)
    if client_factory is None:
        from pymodbus.client import ModbusSerialClient
        client_factory=ModbusSerialClient
    path=chosen['preferred_path']
    lock=ziskej_zamek_modbus_sbernice(path)
    if not lock.acquire(blocking=False):
        raise RotekProbeError('Seriovou sbernici pouziva jiny proces/adapter.')
    client=None
    try:
        client=client_factory(port=path,baudrate=BAUDRATE,bytesize=8,parity='N',stopbits=1,
                              timeout=TIMEOUT_S,retries=0)
        if not client.connect():
            raise RotekProbeError('Seriovy port nelze otevrit.')
        readings={}
        errors={}
        for addr in ALLOWED_REGISTER_ADDRESSES:
            try:
                readings[addr]=_one_register(client,addr)
            except RotekProbeError as exc:
                errors[str(addr)]=str(exc)
        decoded=_decode(readings)
        # Samotny libovolny Modbus response jeste neprokazuje ECU profil.
        verified=decoded['plausible'] is True and len(readings)>=3
        return {
            'profile_id':PROFILE_ID,
            'executor':'rotek_ecu_rtg3_readonly',
            'read_only':True,
            'write_performed':False,
            'communication_type':'rs485',
            'manufacturer':'rotek',
            'communicator_id':chosen['communicator_id'],
            'communicator_serial_number':chosen['serial_number'],
            'serial_path':path,
            'matched_device_id':SLAVE_ID,
            'baudrate':BAUDRATE,
            'function_code':3,
            'communication_detected':bool(readings),
            'register_readable':bool(readings),
            'profile_verified':verified,
            'phase':'profile_identified' if verified else 'register_response' if readings else 'no_response',
            'raw_registers':{str(k):v for k,v in readings.items()},
            'register_errors':errors,
            'telemetry':decoded,
        }
    finally:
        if client is not None:
            try:client.close()
            except Exception:pass
        lock.release()