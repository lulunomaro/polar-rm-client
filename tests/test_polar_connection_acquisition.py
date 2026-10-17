"""Offline characterization of open diagnostics and rejected native ownership."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend.polar_connection import PolarConnectionConfig, acquire_polar_connection
from rm_client.backend.polar_transport import PolarLinkTransport
from rm_client.backend.serial_stream import StreamOpenError


class Handle:
    def __init__(self, on_close=lambda: None, *, is_open=True):
        self.is_open = is_open
        self.on_close = on_close
        self.close_count = 0

    def close(self):
        self.close_count += 1
        self.on_close()
        self.is_open = False


class ConnectionAcquisitionTests(unittest.TestCase):
    def test_unconfigured_endpoints_never_attempt_native_open(self):
        for mode, port, resolved, code in (
            ('serial', 'Auto', '', 'NO_ROBOT_USB'),
            ('serial', 'Stub', 'Stub', 'PORT_STUB'),
            ('serial', 'None', 'none', 'PORT_STUB'),
            ('tcp', 'ignored', '', 'NO_BRIDGE_HOST'),
            ('wifi', 'ignored', '', 'NO_BRIDGE_HOST'),
            ('bridge', 'ignored', '', 'NO_BRIDGE_HOST'),
        ):
            with self.subTest(mode=mode, port=port):
                transport = PolarLinkTransport(port=port)
                transport.transport = mode
                with patch('rm_client.backend.polar_transport.resolve_polar_port', return_value=resolved), \
                        patch('rm_client.backend.serial_stream.open_stream') as opener:
                    transport.start()
                opener.assert_not_called()
                self.assertEqual(transport.last_error_code, code)
                self.assertEqual(transport.resolved_port, '')
                self.assertTrue(transport.cleanup_complete)

    def test_failed_open_maps_diagnostics_without_losing_endpoint(self):
        for mode, code, endpoint in (
            ('serial', 'PORT_BUSY', 'fake-device'),
            ('tcp', 'BRIDGE_UNREACHABLE', 'tcp://fake.invalid:50570'),
            ('wifi', 'BRIDGE_UNREACHABLE', 'tcp://fake.invalid:50570'),
            ('bridge', 'BRIDGE_UNREACHABLE', 'tcp://fake.invalid:50570'),
        ):
            with self.subTest(mode=mode):
                transport = PolarLinkTransport(port='Auto')
                transport.transport, transport.host = mode, 'fake.invalid'
                with patch('rm_client.backend.polar_transport.resolve_polar_port', return_value='fake-device'), \
                        patch('rm_client.backend.serial_stream.open_stream', side_effect=OSError('refused')):
                    transport.start()
                self.assertEqual(transport.resolved_port, endpoint)
                self.assertEqual(transport.last_error_code, code)
                self.assertIn('refused', transport.last_error)
                self.assertTrue(transport.cleanup_complete)

    def test_unopened_handle_is_closed_before_reservation_release(self):
        transport = PolarLinkTransport(port='fake')
        observations = []
        handle = Handle(lambda: observations.append((transport._connecting, transport._serial)), is_open=False)
        with patch('rm_client.backend.serial_stream.open_stream', return_value=handle):
            transport.start()
        self.assertEqual(observations, [(True, None)])
        self.assertEqual(transport.last_error_code, 'PORT_OPEN_FAILED')
        self.assertTrue(transport.cleanup_complete)

    def test_partially_opened_resource_error_retains_owner_until_close(self):
        transport = PolarLinkTransport(port='fake')
        handle = Handle()
        with patch('rm_client.backend.serial_stream.open_stream', side_effect=StreamOpenError(handle, OSError('setup failed'))):
            transport.start()
        self.assertEqual(handle.close_count, 1)
        self.assertEqual(transport.last_error_code, 'PORT_BUSY')
        self.assertTrue(transport.cleanup_complete)

    def test_rejected_close_failure_blocks_replacement_until_explicit_cleanup(self):
        transport = PolarLinkTransport(port='fake')
        def fail_close():
            self.assertTrue(transport._connecting)
            raise OSError('close refused')
        handle = Handle(fail_close)
        def open_then_reconfigure(*args, **kwargs):
            transport.baud += 1
            return handle
        with patch('rm_client.backend.serial_stream.open_stream', side_effect=open_then_reconfigure) as opener:
            transport.start()
            transport.start()
        self.assertEqual(opener.call_count, 1)
        self.assertIs(transport._serial, handle)
        self.assertFalse(transport.connected)
        self.assertFalse(transport.cleanup_complete)
        self.assertEqual(transport.last_error_code, 'STREAM_CLOSE_FAILED')
        handle.on_close = lambda: None
        transport.stop()
        self.assertTrue(transport.cleanup_complete)
        self.assertEqual(handle.close_count, 2)

    def test_configuration_snapshot_includes_mock_mode(self):
        transport = PolarLinkTransport(port='fake')
        handle = Handle()
        def open_then_reconfigure(*args, **kwargs):
            transport.mock_writes = not transport.mock_writes
            return handle
        with patch('rm_client.backend.serial_stream.open_stream', side_effect=open_then_reconfigure):
            transport.start()
        self.assertEqual(handle.close_count, 1)
        self.assertIsNone(transport._thread)
        self.assertTrue(transport.cleanup_complete)


class DetachedAcquisitionTests(unittest.TestCase):
    def test_success_keeps_open_owner_and_exact_captured_arguments(self):
        handle = Handle()
        config = PolarConnectionConfig('wifi', 'ignored', 9600, 'fake.invalid', 1234, None, True)
        with patch('rm_client.backend.serial_stream.open_stream', return_value=handle) as opener:
            result = acquire_polar_connection(config, resolve_port=lambda _: self.fail('unexpected serial resolution'))
        opener.assert_called_once_with('wifi', port='tcp://fake.invalid:1234', baud=9600,
                                       host='fake.invalid', tcp_port=1234, serial_opener=None)
        self.assertIs(result.resource, handle)
        self.assertEqual(result.error_code, '')
        self.assertEqual(handle.close_count, 0)
        self.assertTrue(handle.is_open)
        with self.assertRaises(FrozenInstanceError):
            config.host = 'replacement.invalid'

    def test_partial_open_failure_returns_owner_without_closing(self):
        handle = Handle()
        config = PolarConnectionConfig('tcp', '', 115200, 'fake.invalid', 1234, None, False)
        with patch('rm_client.backend.serial_stream.open_stream', side_effect=StreamOpenError(handle, OSError('setup failed'))):
            result = acquire_polar_connection(config, resolve_port=lambda _: '')
        self.assertIs(result.resource, handle)
        self.assertEqual(result.error_code, 'BRIDGE_UNREACHABLE')
        self.assertEqual(handle.close_count, 0)

    def test_enumeration_failure_does_not_open_or_invent_an_endpoint(self):
        config = PolarConnectionConfig('serial', 'Auto', 115200, '', 1234, None, True)
        def fail_resolution(port):
            raise OSError('enumeration failed')
        with patch('rm_client.backend.serial_stream.open_stream') as opener:
            result = acquire_polar_connection(config, resolve_port=fail_resolution)
        opener.assert_not_called()
        self.assertIsNone(result.resource)
        self.assertEqual(result.resolved_port, '')
        self.assertEqual(result.error_code, 'PORT_BUSY')
        self.assertIn('enumeration failed', result.error)


if __name__ == '__main__':
    unittest.main()
