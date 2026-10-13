"""Read-only v2 spin presentation. No control authority or clock translation."""
from __future__ import annotations
import copy
import math
from .sim_platform_spin import number, request


def _integer(value):
    if type(value) is not int or value < 0:raise ValueError('Invalid identity')
    return value


def _age(now, stamp):
    try:return number(now, 0, 1e15) - number(stamp, 0, now)
    except (ValueError, TypeError):return None


class SpinStatusPresenter:
    """Caches only previously validated applied samples, never request values."""
    def __init__(self, runtime):
        self.runtime = runtime
        self._identity = None
        self._last = None

    def snapshot(self):
        t = self.runtime.transport
        with t._lock:
            out = dict(requested=None, available=False, admitted=False,
                       snapshot_gate=False, snapshot_receive_age=None,
                       last_applied=None, reason='unavailable')
            try:
                now = number(t.clock(), 0, 1e15)
                wanted = self.runtime.modes.request()
                if wanted is not None:out['requested'] = request(wanted)
                connected = t._connected and not t._stop.is_set() and not t.client.uncertain
                if not connected:
                    self._identity = None; self._last = None
                    out['reason'] = 'disconnected'; return out
                raw = t.raw_state
                identity = (_integer(t._connection_generation), _integer(t._watch_generation),
                            t.client.session_id, t.watch_robot, _integer(t.watch_epoch),
                            _integer(raw['episode']))
                if any(type(v) is not str or not v for v in identity[2:4]):raise ValueError('Unknown identity')
                if identity != self._identity:
                    self._identity = identity; self._last = None
                sim_time = number(raw['sim_time'], 0, 1e15)
                age = _age(now, t.raw_state_at)
                out['snapshot_receive_age'] = age
                cap = t.spin_capability()
                out['available'] = cap is not None
                fresh = age is not None and age <= .5
                local = bool(fresh and cap and self.runtime.armed and t._focus
                             and self.runtime.modes.window is not None
                             and self.runtime.modes.window.isActiveWindow()
                             and self.runtime.modes.window.combat_control_input_allowed()
                             and t.role == 'operator' and 'navigation' not in t.plan
                             and t.owned_robot == t.watch_robot and wanted
                             and t._window and now < number(t._window_deadline, 0, 1e15))
                if local:
                    out['admitted'] = self._admitted(raw, request(wanted))
                    feedback = self.runtime.modes.feedback()
                    if out['admitted'] and feedback.get('spin') is True:
                        out['snapshot_gate'] = True
                        q = raw['spin_consumption_receipts'][t.watch_robot]['entry']
                        lease = q['manual_inputs']['motion']
                        sample = raw['motion_feedback'][t.watch_robot]
                        post = number(sample['sample_sim_time'], 0, sim_time)
                        key = identity + (_integer(lease['epoch']), _integer(lease['seq']),
                                          _integer(lease['input_window_id']), post)
                        # Repeated receipt/state polling/painting must not wash age.
                        if self._last is None or (key != self._last['key'] and post > self._last['sample_sim_time']):
                            self._last = dict(key=key, lease_epoch=lease['epoch'], seq=lease['seq'],
                                window_id=lease['input_window_id'], sample_sim_time=post,
                                received_at=number(t.raw_state_at, 0, now),
                                target_yaw_rate_rps=feedback['target_yaw_rate_rps'],
                                measured_yaw_rate_rps=feedback['measured_yaw_rate'],
                                torque_nm=feedback['applied_yaw_torque_nm'], saturated=feedback['saturated'])
                if self._last is not None:
                    sample = copy.deepcopy(self._last)
                    sample.pop('key')
                    sample['receive_age'] = _age(now, sample.pop('received_at'))
                    sample['sim_age'] = _age(sim_time, sample['sample_sim_time'])
                    sample['stale'] = (sample['receive_age'] is None or sample['receive_age'] > .5
                                       or sample['sim_age'] is None or sample['sim_age'] > .2)
                    out['last_applied'] = sample
                out['reason'] = ('fresh snapshot' if fresh else 'stale / unknown snapshot')
            except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
                # Broken identity cannot carry a previously cached sample forward.
                self._identity = None; self._last = None
                out.update(admitted=False, snapshot_gate=False, last_applied=None, reason='unknown identity / sample')
            return copy.deepcopy(out)

    def _admitted(self, raw, wanted):
        """Same-server timestamp comparisons; no local/server epoch subtraction."""
        t = self.runtime.transport
        try:
            live = raw['control']['leases'][t.watch_robot]['motion']
            window = t._window
            epoch = _integer(t.owned_epochs['motion'])
            if (live['owner'] != t.client.session_id or _integer(live['epoch']) != epoch
                or type(live['mode_fault']) is not bool or live['mode_fault']
                or _integer(live['input_window_id']) != _integer(window['window_id'])
                or _integer(window['lease_epochs']['motion']) != epoch
                or window['robot_id'] != t.watch_robot or window['session_id'] != t.client.session_id):return False
            _integer(live['seq'])
            value = live['input']
            if type(value) is not dict or set(value) != {'mode','frame','vx_mps','vy_mps','direction','speed_dps'}:return False
            if value['mode'] != 'spin_v1' or value['frame'] != 'base_link':return False
            if request({k:value[k] for k in wanted}) != wanted:return False
            number(value['vx_mps'], -1, 1); number(value['vy_mps'], -1, 1)
            server_now = number(raw['control']['server_monotonic_s'], 0, 1e15)
            issued = number(window['issued_wall'], 0, 1e15)
            expiry = number(live['input_expires'], 0, 1e15)
            deadline = number(live['input_window_deadline'], 0, 1e15)
            return (issued <= server_now < expiry <= deadline == number(window['expires_wall'], 0, 1e15)
                    and server_now < number(live['expires'], 0, 1e15))
        except (KeyError, TypeError, ValueError, OverflowError):return False


def status_lines(status):
    """Short explicit labels suitable for a narrow original HUD side column."""
    wanted = status.get('requested')
    if wanted:
        target = (-1 if wanted['direction'] == 'cw' else 1) * wanted['speed_dps'] * math.pi / 180
        request_line = 'SPIN REQUEST: %s %.0f dps (%+.2f rad/s)' % (wanted['direction'].upper(), wanted['speed_dps'], target)
    else:request_line = 'SPIN REQUEST: OFF' if status.get('available') else 'SPIN REQUEST: N/A'
    age = status.get('snapshot_receive_age')
    age_text = 'unknown' if age is None else '%.3fs' % age
    lines = [request_line,
             'ADMITTED[SNAP]: %s | ACTIVE[SNAP]: %s' % ('YES' if status.get('admitted') else 'NO', 'YES' if status.get('snapshot_gate') else 'NO'),
             'STATE receive age: '+age_text+' | '+status.get('reason','unknown')]
    sample = status.get('last_applied')
    if sample is None:
        return lines + ['LAST APPLIED: unknown (no matched receipt)', 'MEASURED / TORQUE / SATURATION: unknown', 'HISTORY: none']
    age = lambda key: 'unknown' if sample[key] is None else '%.3fs' % sample[key]
    return lines + [
        'LAST APPLIED: target %+.2f | measured %+.2f rad/s' % (sample['target_yaw_rate_rps'], sample['measured_yaw_rate_rps']),
        'HIST torque %+.2f Nm | saturated %s | lease %d / seq %d' % (sample['torque_nm'], 'YES' if sample['saturated'] else 'NO', sample['lease_epoch'], sample['seq']),
        'HIST t=%.3fs | sim age %s | rx age %s%s' % (sample['sample_sim_time'], age('sim_age'), age('receive_age'), ' STALE' if sample['stale'] else '')]


def overlaps(a, b):
    return a[0] < b[0]+b[2] and b[0] < a[0]+a[2] and a[1] < b[1]+b[3] and b[1] < a[1]+a[3]


def status_layout(width, height, count, reserved=None):
    """Find a free side column, respecting original HUD rectangles unchanged."""
    width, height = float(width), float(height)
    if width < 320 or height < 240:return []
    reserved = list(reserved) if reserved is not None else [(0,0,width,58),(width/2-230,0,460,180)]
    column_width, block_height = min(420., width-28.), count*17.
    xs = [14.,302.,(width-column_width)/2.,width-column_width-14.]
    ys = [62.,190.,height/2+38.,height-block_height-150.]
    xs += [r[0]+r[2]+8 for r in reserved]
    ys += [r[1]+r[3]+8 for r in reserved]
    for x in xs:
        for y in ys:
            block=(x,y,column_width,block_height)
            if x < 14 or y < 58 or x+column_width > width-14 or y+block_height > height-12:continue
            if not any(overlaps(block,r) for r in reserved):
                return [(x,y+i*17,column_width,16.) for i in range(count)]
    return []  # Dense layouts explicitly report no safe panel area; never cover an existing module.
