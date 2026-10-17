"""Frozen behavioral oracle for the BackendManager policy extraction.

The fixture was recorded before production changes (public d64f1528, local
alias da91fc54). Its content digest pins the source independently of Git IDs.
It compares
manager decisions, observable UI state, side-effect order and exact 0x5A bytes.
All transports are recording fakes: no device, network, worker or app startup.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import itertools
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.backend.manager import BackendManager
from rm_client.backend.polar_bridge import PolarControlArbiter, PolarMotionVerifier, PolarStateBridge
from rm_client.backend.polar_link import PolarFrameCodec, PolarSegment, SEG_ROBOT_MOTION
from rm_client.backend.polar_transport import PolarLinkTransport
from rm_client.model import RobotState

FIXTURE = ROOT / 'tests/fixtures/backend_policy_baseline.json'
NOW = 100.0


class Module:
    def __init__(self, enabled=True, **values):
        self.enabled = enabled
        self.values = {name: SimpleNamespace(value=value) for name, value in values.items()}

    def get_setting(self, name):
        return self.values.get(name)


DEFAULT_SETTINGS = {
    'Allow Drive': True, 'Link Mode': 'serial', 'Bridge Host': '', 'Bridge Port': 50570,
    'Mock Writes': True, 'Allow Spin Key': False, 'Radio KM Egress': False,
    'Client Chassis Drive': True, 'Client Gimbal Control': False,
    'Vision NUC Occupied': True, 'Speed Scale %': 100, 'Yaw Scale %': 100,
    'Gimbal Slew': 8, 'Drive Channel': 'both', 'Send Hz': 50,
    'Port': 'Auto', 'Baud': 115200,
}


class RecordingPolar:
    """Only the adapter contract used by the tick, with real codec output."""
    def __init__(self, events):
        self.events = events
        self.codec = PolarFrameCodec()
        self.timestamp = SimpleNamespace(roll_detected=False)
        self.port, self.baud = 'Stub', 115200
        self.transport, self.host, self.tcp_port = 'serial', '', 50570
        self.mock_writes, self.send_hz, self.drive_channel = True, 50.0, 'robot_cmd'
        self.last_error = self.last_error_code = ''
        self._serial = None
        self._running = self._connecting = self._stopping = False
        self.intent = None
        self.spin = 0
        self.gimbal = False
        self.segments = []

    def drain_segments(self):
        result, self.segments = self.segments, []
        return result

    def request_estop(self):
        self.events.append(['estop'])

    def release_estop(self):
        self.events.append(['release_estop'])

    def set_send_hz(self, hz):
        PolarLinkTransport.set_send_hz(self, hz)
        self.events.append(['send_hz', self.send_hz])

    def set_spin_command(self, value):
        self.spin = value
        self.events.append(['spin', value])

    def set_gimbal_host_request(self, value):
        self.gimbal = value
        self.events.append(['gimbal_host', value])

    def submit(self, intent):
        self.intent = intent
        self.events.append(['submit'])

    def stats(self):
        return {'fake_transport': True}

    def wire_frames(self):
        if self.intent is None:
            return []
        intent = self.intent
        return [
            self.codec.encode_robot_cmd(raw_timestamp=123,
                gimbal_pitch_rad=intent.gimbal_pitch_rad, gimbal_yaw_rad=intent.gimbal_yaw_rad,
                chassis_vx=intent.vx, chassis_vy=intent.vy, chassis_wz=intent.wz,
                friction_on=intent.friction_on, tracking=intent.tracking).hex(),
            self.codec.encode_virtual_rc(raw_timestamp=124, key_value=intent.key_value,
                key_mask=0x000F).hex(),
            self.codec.encode_chassis_cmd(raw_timestamp=125, spin_cmd=self.spin,
                param0=int(self.gimbal)).hex(),
        ]


def cases():
    result = {}
    for estop, enabled, allow, chassis, active in itertools.product((False, True), repeat=5):
        label = ''.join(str(int(v)) for v in (estop, enabled, allow, chassis, active))
        result['gates_' + label] = dict(estop=estop, enabled=enabled, active=active,
            settings={'Allow Drive': allow, 'Client Chassis Drive': chassis,
                      'Radio KM Egress': True, 'Allow Spin Key': True}, spin=True)
    for selected, reported in ((1, 0), (3, 0), (6, 0), (7, 0), (0, 0), (99, 0),
                                (1, 3), (3, 1), (3, 7), (3, 103), (101, 0)):
        result[f'role_{selected}_{reported}'] = dict(selected=selected, reported=reported)
    for label, values in {
        'gimbal_ready': {}, 'gimbal_nuc': {'nuc': True},
        'gimbal_uncalibrated': {'calibrated': False}, 'gimbal_unlocked': {'locked': False},
        'gimbal_role_blocked': {'selected': 7}, 'gimbal_reported_blocked': {'reported': 7},
        'gimbal_module_disabled': {'enabled': False}, 'gimbal_no_link': {'active': False},
        'gimbal_estop': {'estop': True}, 'gimbal_lockout': {'lockout': True},
        'gimbal_slew': {'settings': {'Gimbal Slew': 2}},
    }.items():
        settings = {'Client Gimbal Control': True, 'Vision NUC Occupied': values.pop('nuc', False)}
        settings.update(values.pop('settings', {}))
        result[label] = dict(values, settings=settings)
    for label, debug, previous, age in (
        ('missing', {}, None, 0), ('zero', {'km_rx': 0, 'km_key': 1, 'km_pol': 0}, None, 0),
        ('growing', {'km_rx': 3, 'km_key': 0xFFFF, 'km_pol': 1}, 2, 0),
        ('ignored', {'km_rx': 3, 'km_key': 2, 'km_pol': 0}, 2, 0),
        ('stalled', {'km_rx': 3, 'km_key': 2, 'km_pol': 2}, 3, 0),
        ('decreased', {'km_rx': 1, 'km_key': 2, 'km_pol': 7}, 3, 0),
        ('malformed', {'km_rx': 'bad', 'km_key': 'bad', 'km_pol': 'bad'}, 3, 0),
        ('fractional', {'km_rx': '3.9', 'km_key': '1.9', 'km_pol': 1}, 2, 0),
        ('stale', {'km_rx': 3, 'km_key': 2}, 2, 0.751),
        ('boundary', {'km_rx': 3, 'km_key': 2}, 2, 0.75),
        ('future', {'km_rx': 3, 'km_key': 2}, 2, -0.001),
    ):
        result['radio_' + label] = dict(settings={'Radio KM Egress': True}, debug=debug,
                                      previous_rx=previous, debug_age=age)
    for label, settings in {
        'port_default': {'Port': 'Auto'}, 'port_empty': {'Port': ''},
        'port_explicit': {'Port': ' COM7 '}, 'baud_default': {'Baud': 115200},
        'baud_valid': {'Baud': '921600'}, 'baud_invalid': {'Baud': 'bad'},
        'baud_small': {'Baud': 1199}, 'baud_large': {'Baud': 3000001},
        'tcp': {'Link Mode': 'TCP', 'Bridge Host': ' robot.example.invalid ', 'Bridge Port': '50571'},
        'tcp_empty': {'Link Mode': 'tcp'},
        'tcp_bad_port': {'Link Mode': 'tcp', 'Bridge Port': 'bad'},
        'mode_bad': {'Link Mode': 'wifi'}, 'channel_bad': {'Drive Channel': 'bad'},
        'channel_spaced': {'Drive Channel': 'Virtual RC'},
        'hz_low': {'Send Hz': 1}, 'hz_high': {'Send Hz': 1000}, 'hz_bad': {'Send Hz': 'bad'},
        'scale_zero': {'Speed Scale %': 0, 'Yaw Scale %': 0},
        'scale_half': {'Speed Scale %': 50, 'Yaw Scale %': 25},
        'mock_real': {'Mock Writes': False},
    }.items():
        result['settings_' + label] = dict(settings=settings)
    result['settings_missing_registry'] = dict(missing_registry=True)
    result['settings_missing_module'] = dict(missing_module=True)
    result['settings_closing_port'] = dict(settings={'Port': 'COM7'}, reconfigure=False)
    result['settings_closing_baud'] = dict(settings={'Baud': 921600}, reconfigure=False)
    result['settings_closing_endpoint'] = dict(settings={'Link Mode': 'tcp'}, reconfigure=False)
    result['settings_closing_mock'] = dict(settings={'Mock Writes': False}, running=True, reconfigure=False)
    result['no_command'] = dict(no_command=True)
    result['no_control'] = dict(no_control=True)
    result['segments_applied'] = dict(segment=True)
    result['spin_readback'] = dict(spin=True, settings={'Allow Spin Key': True}, spin_reported=True)
    result['spin_unknown_off'] = dict(spin_ever_on=True)
    result['spin_reported_off'] = dict(spin_ever_on=True, spin_reported=False)
    return result


def characterize(options, *, capture_failures=False):
    events = []
    backend = BackendManager.__new__(BackendManager)
    target = RobotState()
    target.robot_id = options.get('selected', 3)
    target.polar_reported_robot_id = options.get('reported', 0)
    target.vision_calibrated = options.get('calibrated', True)
    target.target_locked = options.get('locked', True)
    target.vision_aim_yaw_deg, target.vision_aim_pitch_deg = 17.0, -9.0
    backend.state = target
    backend.polar = RecordingPolar(events)
    backend.polar._running = options.get('running', False)
    backend.polar_bridge = PolarStateBridge(clock=lambda: NOW)
    bridge = backend.polar_bridge
    if options.get('active', True):
        bridge.last_segment_at = NOW
        bridge.segment_counts[SEG_ROBOT_MOTION] = 1
    bridge.debug_packages = dict(options.get('debug', {}))
    bridge.debug_key_at = NOW - options.get('debug_age', 0)
    bridge.debug_key_sequence = 4
    if 'spin_reported' in options:
        bridge.overlay['polar_spin_active'] = options['spin_reported']
    if options.get('segment'):
        backend.polar.segments.append(PolarSegment(SEG_ROBOT_MOTION, 1,
            {'vx_mps': 0.5, 'vy_mps': -0.5, 'wz_rps': 0.25}))
    backend.polar_arbiter = PolarControlArbiter(clock=lambda: NOW)
    if options.get('lockout'):
        backend.polar_arbiter.state = 'LOCKOUT'
    backend.polar_motion = PolarMotionVerifier(clock=lambda: NOW)
    backend.safety = SimpleNamespace(estop_active=options.get('estop', False))
    backend.field_log = SimpleNamespace(emit=lambda event, **values: events.append([event, values]))
    backend._last_spin_note = ''
    backend._spin_ever_on = options.get('spin_ever_on', False)
    backend._spin_off_since = 0.0
    backend._spin_assert = 0
    backend._polar_gimbal_last = options.get("prior_plan")
    backend._km_rx_prev = options.get('previous_rx')
    backend.polar_chassis_state_seen = False
    backend._retry_polar_connect = lambda: events.append(['retry'])
    def prepare(reason):
        events.append(['reconfigure', reason])
        return options.get('reconfigure', True)
    backend._prepare_polar_reconfigure = prepare
    values = dict(DEFAULT_SETTINGS, **options.get('settings', {}))
    module = Module(enabled=options.get('enabled', True), **values)
    registry = {'RobotLink': module, 'SpinMode': Module(enabled=options.get('spin', False)),
                'ArmorAutoAim': Module(enabled=True, **{'Mock Output': False})}
    if options.get('missing_module'):
        del registry['RobotLink']
    if options.get('missing_registry'):
        registry = None
    command = None if options.get('no_command') else SimpleNamespace(
        vx=1.5, vy=-0.5, wz=2.0, keyboard_value=0xFFFF)
    control = None if options.get('no_control') else SimpleNamespace(last_command=command, dt7_enabled=True)
    if control is not None and 'radio_initial' in options:
        control.radio_km_enabled = options['radio_initial']
    failure = None
    with patch('rm_client.backend.manager.time.perf_counter', return_value=NOW), \
         patch('rm_client.backend.manager._LOG.warning'):
        try:
            result = backend._tick_polar_link_ready(target, registry, control)
        except Exception as error:
            if not capture_failures:
                raise
            result = None
            failure = {'type': type(error).__name__,
                       'gimbal_target_deg': backend._polar_gimbal_last}
    intent = asdict(backend.polar.intent) if backend.polar.intent else None
    if intent:
        intent.pop('produced_at')
    control_values = vars(control).copy() if control else None
    if control_values is not None:
        control_values.pop('last_command')
    state_values = {name: getattr(target, name) for name in (
        'polar_link_active', 'polar_link_age_s', 'polar_segments_received',
        'polar_dropped_segments', 'polar_motion_state', 'polar_motion_detail',
        'polar_drive_armed', 'polar_gimbal_client', 'polar_authority_state',
        'polar_authority_reason', 'polar_spin_note', 'backend_stats')}
    config = {name: getattr(backend.polar, name) for name in (
        'port', 'baud', 'transport', 'host', 'tcp_port', 'mock_writes',
        'send_hz', 'drive_channel', 'last_error', 'last_error_code')}
    snapshot = dict(result=result, intent=intent, wire=backend.polar.wire_frames(),
                    control=control_values, state=state_values, config=config, events=events,
                    spin=[backend._spin_ever_on, backend._spin_off_since, backend._spin_assert],
                    previous_rx=backend._km_rx_prev)
    if failure is not None:
        snapshot['failure'] = failure
    return json.loads(json.dumps(snapshot, sort_keys=True))


class BackendPolicyCharacterizationTests(unittest.TestCase):
    def test_tick_matches_pre_refactor_observations_and_wire_bytes(self):
        oracle = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.assertEqual(set(oracle['cases']), set(cases()))
        for name, options in cases().items():
            with self.subTest(case=name):
                self.assertEqual(characterize(options), oracle['cases'][name])

    def test_spin_off_is_bounded_and_reported_stop_releases_ownership(self):
        events = []
        backend = BackendManager.__new__(BackendManager)
        backend.polar = RecordingPolar(events)
        backend._spin_ever_on, backend._spin_off_since = False, 0.0
        steps = [
            (100.0, False, True, (0, False, 0.0)),
            (100.1, True, None, (1, True, 0.0)),
            (100.2, False, None, (2, True, 100.2)),
            (101.199, False, None, (2, True, 100.2)),
            (101.2, False, None, (0, False, 0.0)),
            (102.0, True, False, (1, True, 0.0)),
            (102.1, False, True, (2, True, 0.0)),
            (102.2, False, False, (0, False, 0.0)),
        ]
        for now, desired, reported, expected in steps:
            with self.subTest(now=now), patch('rm_client.backend.manager.time.perf_counter', return_value=now):
                command = backend._update_spin_command(desired=desired, reported=reported)
                self.assertEqual((command, backend._spin_ever_on, backend._spin_off_since), expected)
                self.assertEqual(backend.polar.spin, command)

    def test_missing_target_never_submits(self):
        backend = BackendManager.__new__(BackendManager)
        backend.state = None
        self.assertEqual(backend._tick_polar_link_ready(), {'active': False})


if __name__ == '__main__':
    if '--record-baseline' in sys.argv:
        from tools.compare_polar_policy import BASELINE, BASELINE_MANAGER_SHA256
        source_hash = hashlib.sha256((ROOT / 'rm_client/backend/manager.py').read_bytes()).hexdigest()
        if source_hash != BASELINE_MANAGER_SHA256:
            raise SystemExit('Baseline recording requires the exact original reviewed manager source')
        fixture = {'source_commit': BASELINE,
            'manager_sha256': source_hash,
            'cases': {name: characterize(options) for name, options in cases().items()}}
        FIXTURE.write_text(json.dumps(fixture, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        print(f'Recorded {len(fixture["cases"])} cases from {BASELINE}')
    else:
        unittest.main()
