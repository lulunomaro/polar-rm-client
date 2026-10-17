"""Ordered angle-admission effects compared with the immutable pre-extraction method."""
from dataclasses import asdict, replace
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.backend.controller_angles import AngleRelayConfig, explicit_port, port_key
from rm_client.backend.manager import BackendManager

FIXTURE = ROOT / 'tests/fixtures/angle_port_admission_baseline.txt'
BASELINE_SHA256 = 'b080a712bf2ace4cd36a68e6c7a4a13a2909be330054d36ed7d455f73e28259a'


def frozen_method():
    source = FIXTURE.read_bytes()
    if hashlib.sha256(source).hexdigest() != BASELINE_SHA256:
        raise AssertionError('historical angle-admission method changed')
    namespace = dict(Any=object, AngleRelayConfig=AngleRelayConfig, replace=replace,
                     explicit_port=explicit_port, port_key=port_key)
    exec(compile(source, str(FIXTURE), 'exec'), namespace)
    return namespace['_sync_controller_angles']


def run(method, *, angle=None, robot=None, prior=None, serial=None,
        enabled=True, robot_enabled=True, estop=False, blocked=False,
        absent_registry=False, absent_robot=False, absent_angle=False):
    trace = []
    angle_values = {'Angle RX Port': 'RX', 'Angle TX Port': 'TX',
                    'Forward Angles': True, 'Angle Mock Writes': False}
    angle_values.update(angle or {})
    robot_values = {'Link Mode': 'tcp', 'Allow Drive': True,
                    'Custom Controller Egress': False, 'Direct DT7 Egress': False}
    robot_values.update(robot or {})

    def module(name, values, active):
        def get_setting(key):
            trace.append(('setting', name, key))
            return SimpleNamespace(value=values[key]) if key in values else None
        return SimpleNamespace(enabled=active, get_setting=get_setting)

    modules = {}
    if not absent_angle:
        modules['ControllerAngles'] = module('angle', angle_values, enabled)
    if not absent_robot:
        modules['RobotLink'] = module('robot', robot_values, robot_enabled)

    def get(name):
        trace.append(('registry', name))
        return modules.get(name)

    def configure(config, authorized):
        trace.append(('configure', asdict(config), authorized))

    def disarm(reason):
        trace.append(('disarm', reason))

    status = {'fixture': 'status', 'armed': False}
    def stats():
        trace.append(('stats',))
        return status

    manager = object.__new__(BackendManager)
    manager._backend_transition_blocks_work = lambda: blocked
    manager.safety = SimpleNamespace(estop_active=estop)
    manager.controller_angles = SimpleNamespace(configure=configure, disarm=disarm, stats=stats)
    manager.state = SimpleNamespace(backend_stats={})
    for name, values in (prior or {}).items():
        setattr(manager, '_' + name + '_link', SimpleNamespace(**values))
    if serial is not None:
        manager.serial = SimpleNamespace(**serial)
    result = method(manager, None if absent_registry else SimpleNamespace(get=get))
    return trace, result, manager.state.backend_stats


class AnglePortAdmissionCharacterizationTests(unittest.TestCase):
    def compare(self, **case):
        self.assertEqual(run(BackendManager._sync_controller_angles, **case),
                         run(frozen_method(), **case))

    def test_gates_forwarding_and_absent_sources(self):
        for enabled in (False, True):
            for robot_enabled in (False, True):
                for estop in (False, True):
                    for forward in (False, True):
                        for mock in (False, True):
                            with self.subTest(enabled=enabled, robot=robot_enabled,
                                              estop=estop, forward=forward, mock=mock):
                                self.compare(enabled=enabled, robot_enabled=robot_enabled,
                                             estop=estop, angle={'Forward Angles': forward,
                                                               'Angle Mock Writes': mock},
                                             robot={'Link Mode': 'serial', 'Port': 'TX'})
        for flag in ('blocked', 'absent_registry', 'absent_robot', 'absent_angle'):
            with self.subTest(flag=flag):
                self.compare(**{flag: True})

    def test_ordered_configured_and_retained_port_claims(self):
        for port in ('RX', ' rx ', 'TX', 'Auto', ' auto ', 'Stub', 'None', '', 'OTHER'):
            for source in ('robot', 'cc', 'dt7', 'prior_cc', 'prior_dt7', 'serial'):
                with self.subTest(port=port, source=source):
                    case = {}
                    if source == 'robot':
                        case['robot'] = {'Link Mode': 'serial', 'Port': port}
                    elif source in ('cc', 'dt7'):
                        case['robot'] = ({'Custom Controller Egress': True, 'CC Mock Writes': False,
                                          'CC Port': port} if source == 'cc' else
                                         {'Direct DT7 Egress': True, 'DT7 Real TX': True,
                                          'DT7 Port': port})
                    elif source.startswith('prior_'):
                        case['prior'] = {source[6:]: {'mock_output': False, 'port': port}}
                    else:
                        case['serial'] = {'running': True, 'port': port}
                    self.compare(**case)
        # First conflict is diagnostic policy, not an unordered set operation.
        self.compare(robot={'Link Mode': 'serial', 'Port': 'RX',
                            'Custom Controller Egress': True, 'CC Mock Writes': False, 'CC Port': 'RX'},
                     prior={'cc': {'mock_output': False, 'port': 'RX'}},
                     serial={'running': True, 'port': 'RX'})

    def test_posix_aliases_are_observed_without_opening_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'device'
            alias = Path(directory) / 'alias'
            alias.symlink_to(target)
            case = dict(angle={'Angle RX Port': str(target)},
                        prior={'cc': {'mock_output': False, 'port': str(alias)}})
            self.compare(**case)
            trace, _, _ = run(BackendManager._sync_controller_angles, **case)
            self.assertTrue(any(item[0] == 'disarm' and 'CC prior writer' in item[1]
                                for item in trace))

    def test_alias_observation_short_circuit_and_failure_prefix(self):
        from rm_client.backend import manager as manager_module
        for enabled in (False, True):
            for first in ('Auto', 'RX', 'OTHER'):
                outcomes = []
                for current in (False, True):
                    observed = []
                    primary = OSError('late alias observation rejected')
                    def normalize(port):
                        observed.append(port)
                        if port == 'LATE':
                            raise primary
                        return port_key(port)
                    method = BackendManager._sync_controller_angles if current else frozen_method()
                    if not current:
                        method.__globals__['port_key'] = normalize
                    with patch.object(manager_module, 'port_key', normalize):
                        try:
                            result = run(method, enabled=enabled,
                                robot={'Link Mode': 'serial', 'Port': first},
                                prior={'cc': {'mock_output': False, 'port': 'LATE'}})
                        except OSError as error:
                            self.assertIs(error, primary)
                            result = 'same primary failure'
                    outcomes.append((observed, result))
                with self.subTest(enabled=enabled, first=first):
                    self.assertEqual(outcomes[0], outcomes[1])

    def test_handoff_requires_literal_true_and_resolved_port_precedes_requested(self):
        for handoff in (None, False, True, 1, 'ready'):
            for mock in (False, True):
                with self.subTest(handoff=handoff, mock=mock):
                    self.compare(prior={'cc': {'mock_output': mock, 'closed': True,
                                               'port_handoff_ready': handoff,
                                               'resolved_port': 'RX', 'port': 'OTHER'}})
        for resolved in ('', None, 'OTHER'):
            self.compare(prior={'dt7': {'mock_output': False, 'resolved_port': resolved,
                                       'port': 'RX'}})


if __name__ == '__main__':
    unittest.main()
