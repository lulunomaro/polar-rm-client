"""Explicit SIM vehicle selection contracts. No device profiles or hardware I/O."""
from __future__ import annotations

MAX_SEQUENCE=2**53-1
ROBOT_IDS=tuple(team+'_'+role for team in ('red','blue') for role in ('hero','infantry','sentry'))
SELECTION_SETTING='SIM Vehicle'

def selection_profiles(session):
    if not isinstance(session,dict):return ()
    caps=session.get('capabilities',{})
    if not isinstance(caps,dict) or caps.get('robot_selection') is not True:return ()
    if type(caps.get('robot_selection_version')) is not int or caps['robot_selection_version']!=1:return ()
    endpoints=session.get('endpoints')
    if not isinstance(endpoints,dict) or endpoints.get('select_robot')!='/select_robot':raise ValueError('Unsupported selection endpoint')
    profiles=session.get('robot_profiles')
    if not isinstance(profiles,list) or not 1<=len(profiles)<=6:raise ValueError('Invalid selectable robot profiles')
    result=[]
    for item in profiles:
        if not isinstance(item,dict) or item.get('robot_id') not in ROBOT_IDS:raise ValueError('Invalid selectable robot')
        robot=item['robot_id']
        if robot!=item.get('team','')+'_'+item.get('role','') or robot in result:raise ValueError('Invalid selectable identity')
        if not isinstance(item.get('capabilities'),dict):raise ValueError('Missing per-robot capabilities')
        result.append(robot)
    if session.get('robot_id') not in result:raise ValueError('Current robot not selectable')
    last=session.get('last_selection_id')
    if type(last) is not int or not -1<=last<=MAX_SEQUENCE:raise ValueError('Invalid selection sequence')
    return tuple(result)

def validate_selection_ack(ack,request):
    if not isinstance(ack,dict) or ack.get('schema')!='polar.sim.operator.v1' or type(ack.get('accepted')) is not bool:raise ValueError('Invalid selection acknowledgement')
    if type(ack.get('selection_id')) is not int or ack['selection_id']!=request['selection_id']:raise ValueError('Wrong selection acknowledgement ID')
    if ack.get('session_id')!=request['session_id']:raise ValueError('Wrong selection session')
    if type(ack.get('last_selection_id')) is not int or not -1<=ack['last_selection_id']<=MAX_SEQUENCE:raise ValueError('Invalid selection acknowledgement sequence')
    if ack['accepted']:
        if ack['last_selection_id']!=request['selection_id']:raise ValueError('Unconfirmed selection sequence')
        if ack.get('previous_robot_id')!=request['robot_id'] or type(ack.get('previous_epoch')) is not int or ack['previous_epoch']!=request['epoch']:raise ValueError('Wrong previous selection identity')
        if ack.get('robot_id')!=request['requested_robot_id'] or type(ack.get('epoch')) is not int or ack['epoch']!=request['epoch']+1:raise ValueError('Wrong selected identity')
        if ack.get('requires_rearm') is not True or ack.get('match_reset') is not False:raise ValueError('Selection must release input without resetting match')
        if ack.get('reason')!='robot_selected':raise ValueError('Unconfirmed selection result')
    return bool(ack['accepted'])
