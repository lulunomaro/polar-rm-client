"""Frozen two-caller diagnostics, including ordered reads and failure prefixes."""
from collections.abc import Mapping
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.backend import manager as manager_module

FIXTURE = ROOT / 'tests/fixtures/polar_health_baseline.txt'
BASELINE_SHA256 = '532c7caa5a51dc38e8527bb1c396b79e6e2b46c242787146801e7d62975e4620'


def frozen_methods():
    source = FIXTURE.read_bytes()
    assert hashlib.sha256(source).hexdigest() == BASELINE_SHA256
    namespace = dict(Any=Any, RobotState=object, time=SimpleNamespace(perf_counter=lambda: 11.))
    exec(compile(source, str(FIXTURE), 'exec'), namespace)
    return namespace


class Trace:
    def __init__(self, fail_at=None):
        self.events = []
        self.fail_at = fail_at
        self.failure = RuntimeError('diagnostic observation failure')

    def event(self, *event):
        self.events.append(event)
        if len(self.events) == self.fail_at:
            raise self.failure

    def method(self, name, value):
        def call(*args):
            self.event('call', name, args)
            return value
        return call


class Source:
    def __init__(self, trace, name, **values):
        object.__setattr__(self, '_trace', trace)
        object.__setattr__(self, '_name', name)
        object.__setattr__(self, '_values', values)

    def __getattr__(self, name):
        self._trace.event('read', self._name, name)
        return self._values[name]

    def __setattr__(self, name, value):
        self._trace.event('write', self._name, name, value)
        self._values[name] = value


class SegmentCount:
    def __init__(self, trace, value):
        self.trace, self.value = trace, value

    def __int__(self):
        self.trace.event('int', self.value)
        return self.value


class DiagnosticMapping(Mapping):
    def __init__(self, trace):
        self.trace = trace
        self.values = {'transport': 'observed', 'decode': 'replace me',
                       'link_alive': 'replace me', 'segment_counts': 'replace me'}

    def __iter__(self):
        self.trace.event('mapping.iter')
        return iter(self.values)

    def __getitem__(self, key):
        self.trace.event('mapping.get', key)
        return self.values[key]

    def __len__(self):
        return len(self.values)


def fixture(trace):
    node = lambda name, **values: Source(trace, name, **values)
    stats = node('decode', frames_ok=11, crc8_errors=2, crc16_errors=3,
                 unknown_segments=4, length_errors=5, buffer_overflows=6)
    polar = node('polar', stats=trace.method('polar.stats', DiagnosticMapping(trace)),
                 codec=node('codec', stats=stats), timestamp=node('timestamp', roll_detected=True))
    bridge = node('bridge', active=True, dropped=7,
                  segment_counts={0x2A: SegmentCount(trace, 5), 1: SegmentCount(trace, 9)})
    authority = {'state': 'CLIENT', 'reason': 'fixture'}
    arbiter = node('arbiter', stats=trace.method('arbiter.stats', authority), may_command_gimbal=True)
    native = node('native', latest_timestamp=trace.method('native.latest_timestamp', 10.),
                  frame_size=trace.method('native.frame_size', (640, 480)),
                  frames_decoded=50, available=True, continuous_decoded_seconds=2.,
                  input_queue_depth=trace.method('native.input_queue_depth', 1),
                  input_chunks_dropped=2, input_chunks_written=3, input_bytes_written=4,
                  last_error='', health=trace.method('native.health', {'ok': True}))
    state = node('state', backend_stats={'native_video_0310': {}})
    lifecycle = node('lifecycle', phase=node('phase', value='RUNNING'), generation=8,
                     pending_resources=('decoder',), failures=(), elapsed_s=.3, final=False)
    qualification = node('qualification', session=None,
        core_ready=trace.method('qualification.core_ready', (True, 'ready')),
        capabilities=trace.method('qualification.capabilities', ('z', 'a')),
        store=node('store', last_error=''))
    manager = node('manager', polar=polar, polar_bridge=bridge, polar_arbiter=arbiter,
        native_decoder=native, state=state, lifecycle_snapshot=lifecycle,
        scenario_key='fixture', referee=node('referee', profile_id='2026', protocol_version='1'),
        qualification=qualification,
        field_log=node('field_log', path=Path('evidence.jsonl'),
                       snapshot=trace.method('field_log.snapshot', {'intact': True})))
    for name in ('safety', 'mqtt', 'udp', 'serial', 'controller_angles'):
        manager._values[name] = node(name, stats=trace.method(name + '.stats', {'name': name}))
    for name in ('_native_0310_bytes_since_reset', '_native_0310_dropped_chunks',
                 '_native_0310_out_of_order_chunks', '_native_0310_recovery_dropped_chunks',
                 '_native_0310_blocked_until_sps', '_native_0310_decoder_restarts',
                 '_estop_triggered_at', '_last_estop_zero_ok', '_last_estop_zero_reason',
                 '_mqtt_disconnect_lock_at', '_mqtt_reconnected_locked_at', '_input_clear_generation',
                 '_apply_error_count', '_last_apply_error', '_official_state_updates_count'):
        manager._values[name] = 0
    return manager, state


def exercise(method, name, *, fail_at=None, estopped=False):
    trace = Trace(fail_at)
    manager, state = fixture(trace)
    try:
        if name == 'stats':
            result = method(manager)
        else:
            result = method(manager, state, estopped=estopped, drive_armed=True,
                            motion_stats={'state': 'MOVING'})
        outcome = ('return', result)
    except RuntimeError as error:
        if error is not trace.failure:
            raise
        outcome = ('original failure',)
    return trace.events, outcome, state._values


class PolarHealthCharacterizationTests(unittest.TestCase):
    def test_both_caller_outputs_and_observation_order(self):
        for name in ('stats', '_publish_polar_authority'):
            for estopped in (False, True):
                with self.subTest(caller=name, estopped=estopped), \
                        patch.object(manager_module.time, 'perf_counter', return_value=11.):
                    self.assertEqual(exercise(getattr(manager_module.BackendManager, name), name, estopped=estopped),
                                     exercise(frozen_methods()[name], name, estopped=estopped))

    def test_every_observed_failure_prefix_preserves_partial_state(self):
        for name in ('stats', '_publish_polar_authority'):
            reference = frozen_methods()[name]
            count = len(exercise(reference, name)[0])
            for fail_at in range(1, count + 1):
                with self.subTest(caller=name, event=fail_at), \
                        patch.object(manager_module.time, 'perf_counter', return_value=11.):
                    self.assertEqual(exercise(getattr(manager_module.BackendManager, name), name, fail_at=fail_at),
                                     exercise(reference, name, fail_at=fail_at))


if __name__ == '__main__':
    unittest.main()
