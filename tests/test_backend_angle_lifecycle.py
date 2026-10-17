"""Direct settings synchronization cannot reopen a completed teardown owner."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from rm_client.backend.manager import BackendManager
from rm_client.model import RobotState, build_default_registry


def until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('angle lifecycle did not settle')
        time.sleep(.001)


class Stream:
    is_open = True
    in_waiting = 0
    def reset_input_buffer(self):
        pass
    def read(self, size):
        return b''
    def close(self):
        self.is_open = False


class BackendAngleLifecycleTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {'POLAR_USER_DATA_ROOT': root.name}), patch.object(
                BackendManager, '_build_commit', return_value='TEST'):
            self.backend = BackendManager(workspace=root.name, state=RobotState())
        self.addCleanup(self.backend.shutdown)
        with patch('rm_client.serial_ports.port_snapshot', return_value=[]):
            self.registry = build_default_registry()
        self.registry.get('RobotLink').get_setting('Port').value = 'Stub'
        self.module = self.registry.get('ControllerAngles')
        self.module.enabled = True
        self.module.get_setting('Angle RX Port').value = 'TEST_RX'
        self.opened = []
        def factory(**kwargs):
            stream = Stream()
            self.opened.append(stream)
            return stream
        self.backend.controller_angles.serial_factory = factory
        self.backend._sync_controller_angles(self.registry)
        until(lambda: len(self.opened) == 1)

    def settle(self):
        def complete():
            self.backend.poll_lifecycle()
            return not self.backend.lifecycle_pending
        until(complete)

    def test_pending_transition_cannot_restart_an_already_closed_angle_owner(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        stop = self.backend.udp.stop
        def blocked_stop():
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test did not release UDP')
            stop()
        with patch.object(self.backend.udp, 'stop', side_effect=blocked_stop):
            self.backend.request_scenario('LabDebug', start=False)
            self.assertTrue(entered.wait(1))
            until(lambda: not self.opened[0].is_open)
            self.assertTrue(self.backend.controller_angles.wait_closed(1))
            old_worker = self.backend.controller_angles._thread
            self.module.get_setting('Angle RX Port').value = 'NEXT_RX'
            for _ in range(100):
                self.backend._sync_controller_angles(self.registry)
            self.assertTrue(self.backend.lifecycle_pending)
            self.assertEqual(len(self.opened), 1)
            self.assertIs(self.backend.controller_angles._thread, old_worker)
            self.assertEqual(self.backend.controller_angles.owned_ports, ())
            release.set()
            self.settle()
        self.backend._sync_controller_angles(self.registry)
        until(lambda: len(self.opened) == 2)
        self.assertTrue(self.opened[1].is_open)

    def test_final_transition_keeps_direct_configuration_closed(self):
        self.backend.request_shutdown()
        self.settle()
        self.assertTrue(self.backend.shutdown_completion.may_exit)
        self.module.get_setting('Angle RX Port').value = 'NEXT_RX'
        self.backend._sync_controller_angles(self.registry)
        self.assertEqual(len(self.opened), 1)
        self.assertFalse(self.opened[0].is_open)
        self.assertEqual(self.backend.controller_angles.owned_ports, ())

    def test_failed_transition_keeps_direct_configuration_closed(self):
        with patch.object(self.backend.udp, 'stop', side_effect=OSError('failed UDP close')):
            self.backend.request_scenario('LabDebug', start=False)
            self.settle()
        self.assertTrue(self.backend.lifecycle_snapshot.failures)
        self.module.get_setting('Angle RX Port').value = 'NEXT_RX'
        self.backend._sync_controller_angles(self.registry)
        self.assertEqual(len(self.opened), 1)
        self.assertFalse(self.opened[0].is_open)
        self.assertEqual(self.backend.controller_angles.owned_ports, ())


if __name__ == '__main__':
    unittest.main()
