"""F47 R120: build a standard Zigbee OnOff timed command for MINI-ZB2GS.

The ZCL 0x0006, command 0x42, carries an ON duration inside ONE Zigbee
request. No separate cloud ON and OFF pair is generated. Device firmware
support is unconfirmed: this module builds only a command descriptor and
does not send it or attest any actuator safety condition.
"""
from __future__ import annotations

SERIAL="F800711-TNG-00013"
UUID="014a0d23-d440-4131-abb4-28a860595043"
IEEE="08:fd:52:ff:fe:07:87:d9"
OPEN="switch.sonoff_mini_zb2gs_spinac_3"
CLOSE="switch.sonoff_mini_zb2gs_spinac_4"
CLUSTER=0x0006
TIMED_COMMAND=0x42

class SafetyStop(RuntimeError):
    pass

def make_timed_request(*,device_uuid:str,ieee:str,endpoint:int,
                       relay_open_state:str,relay_close_state:str) -> dict:
    if device_uuid!=UUID or ieee.lower()!=IEEE:
        raise SafetyStop("wrong_device_identity")
    if type(endpoint) is not int or endpoint not in (1,2):
        raise SafetyStop("wrong_endpoint")
    if relay_open_state!="off" or relay_close_state!="off":
        raise SafetyStop("directions_not_both_off")
    return {
        "ieee":IEEE,
        "endpoint_id":endpoint,
        "cluster_id":CLUSTER,
        "cluster_type":"in",
        "command":TIMED_COMMAND,
        "command_type":"server",
        "params":{
            "on_off_control":0,
            "on_time":10,  # 10 x 0.1 seconds
            "off_wait_time":20,  # 20 x 0.1 seconds
        },
    }
