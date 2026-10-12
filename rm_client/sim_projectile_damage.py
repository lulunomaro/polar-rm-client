"""SIM-only actual-projectile-damage observation contract.

This pure consumer is independent of UI and control transport. It never infers
hits from HP or emits spin/fire. The separate hit-spin integration requires an
authoritative simulator producer and explicit current live-lease eligibility.
"""
from __future__ import annotations
from dataclasses import dataclass
import math

SCHEMA = 'polar.sim.projectile_damage.v1'
SOURCE = 'simulator_judge_actual_projectile_damage'
MAX_INTEGER = 2**53-1
IDENTITY_KEYS = ('session_id','epoch','robot_id')


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise ValueError('Invalid projectile damage integer')
    return value


def _time(value):
    if type(value) not in (int,float):
        raise ValueError('Invalid projectile damage timestamp')
    try:
        value=float(value)
    except (ValueError,OverflowError):
        raise ValueError('Invalid projectile damage timestamp') from None
    if not math.isfinite(value) or value < 0:
        raise ValueError('Invalid projectile damage timestamp')
    return value


def _identity(value):
    if not isinstance(value,dict):
        raise ValueError('Missing projectile damage identity')
    session=value.get('session_id');epoch=value.get('epoch');robot=value.get('robot_id')
    if not isinstance(session,str) or not session or len(session)>100:
        raise ValueError('Invalid projectile damage session')
    _integer(epoch)
    if robot not in {team+'_'+role for team in ('red','blue') for role in ('hero','infantry','sentry')}:
        raise ValueError('Invalid projectile damage robot')
    return session,epoch,robot


@dataclass(frozen=True)
class DamageObservation:
    identity: tuple[str,int,str,int]
    revision: int
    total_damage: float
    last_event_sequence: int
    sim_time: float


@dataclass(frozen=True)
class DamageDelta:
    revision: int = 0
    damage: float = 0.
    reason: str = 'baseline_required'


def parse_observation(value, expected_identity, current_sim_time, max_age=.5):
    if not isinstance(value,dict) or value.get('schema')!=SCHEMA or value.get('source')!=SOURCE:
        raise ValueError('Authoritative simulator projectile damage required')
    identity=_identity(value)
    if identity!=_identity(expected_identity):
        raise ValueError('Wrong projectile damage identity')
    revision=_integer(value.get('revision'));total=_time(value.get('total_damage'))
    if total>MAX_INTEGER:raise ValueError('Projectile damage total outside contract')
    sequence=_integer(value.get('last_event_sequence'),-1)
    stamp=_time(value.get('sim_time'));now=_time(current_sim_time);age=_time(max_age)
    if age>1 or not 0<=now-stamp<=age:
        raise ValueError('Stale or future projectile damage observation')
    if (revision==0)!=(total==0) or (revision==0)!=(sequence==-1):
        raise ValueError('Inconsistent projectile damage baseline')
    episode=_integer(value.get('episode'))
    return DamageObservation((*identity,episode),revision,total,sequence,stamp)


class ProjectileDamageConsumer:
    """Consume a new positive cumulative delta only inside an explicit live lease.

    Any failure invalidates the baseline. New identity, reconnect, initial sample
    and every live-lease edge establish a fresh baseline without replaying hits.
    Revision gaps may aggregate damage; repeated and reordered observations cannot
    cause duplicate automation. No operation accepts ordinary HP observations.
    """
    def __init__(self):
        self._last=None
        self._live=False

    def reset(self):
        self._last=None
        self._live=False

    def observe(self,value,expected_identity,current_sim_time,*,live=False):
        if type(live) is not bool:
            self.reset()
            raise ValueError('Live lease must be explicit boolean')
        if live!=self._live:
            self._last=None
            self._live=live
        try:
            current=parse_observation(value,expected_identity,current_sim_time)
            previous=self._last
            if previous is not None and current.identity==previous.identity:
                if current.sim_time<previous.sim_time or current.revision<previous.revision or current.total_damage<previous.total_damage or current.last_event_sequence<previous.last_event_sequence:
                    raise ValueError('Regressed projectile damage observation')
                if current.revision==previous.revision:
                    if current.total_damage!=previous.total_damage or current.last_event_sequence!=previous.last_event_sequence:
                        raise ValueError('Damage changed without new revision')
                elif current.total_damage<=previous.total_damage or current.last_event_sequence<=previous.last_event_sequence:
                    raise ValueError('Revision changed without new positive damage event')
        except (ValueError,TypeError):
            self.reset()
            raise
        self._last=current
        if not live:
            return DamageDelta(current.revision,0,'not_live')
        if previous is None or current.identity!=previous.identity:
            return DamageDelta(current.revision,0,'baseline' if previous is None else 'identity_changed')
        if current.revision==previous.revision:
            return DamageDelta(current.revision,0,'duplicate')
        return DamageDelta(current.revision,current.total_damage-previous.total_damage,'new_projectile_damage')


def supports_damage_observation(session):
    caps=(session or {}).get('capabilities',{})
    return (isinstance(caps,dict) and type(caps.get('auto_spin_on_hit_damage_version')) is int
            and caps['auto_spin_on_hit_damage_version']==1)
