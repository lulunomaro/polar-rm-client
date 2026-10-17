"""Independent neutral releases use inert doubles, never physical outputs."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.control import KeyboardControlBridge


class ControlReleaseAllFailureTests(unittest.TestCase):
    def bridge(self, failures=()):
        bridge = KeyboardControlBridge()
        trace = []
        command = object()
        errors = {name: RuntimeError(name + ' failed') for name in failures}
        def action(name, result=None):
            def run(*args):
                trace.append(name)
                if name in errors:
                    raise errors[name]
                return result
            return Mock(side_effect=run)
        bridge.reset_inputs = action('reset')
        bridge.send_from_state = action('publish', command)
        bridge.release_direct_dt7 = action('dt7')
        bridge.release_custom_controller = action('custom')
        return bridge, trace, command, errors

    def test_success_preserves_order_and_return_value(self):
        bridge, trace, command, _ = self.bridge()
        state, registry = object(), object()
        self.assertIs(bridge.release_all(state, registry), command)
        self.assertEqual(trace, ['reset', 'publish', 'dt7', 'custom'])
        bridge.send_from_state.assert_called_once_with(state, registry)

    def test_failed_publication_still_releases_both_native_routes(self):
        bridge, trace, _, errors = self.bridge(['publish'])
        with self.assertRaises(RuntimeError) as raised:
            bridge.release_all(object(), object())
        self.assertIs(raised.exception, errors['publish'])
        self.assertEqual(trace, ['reset', 'publish', 'dt7', 'custom'])

    def test_failed_reset_skips_ordinary_command_but_releases_native_routes(self):
        bridge, trace, _, errors = self.bridge(['reset'])
        with self.assertRaises(RuntimeError) as raised:
            bridge.release_all(object(), object())
        self.assertIs(raised.exception, errors['reset'])
        self.assertEqual(trace, ['reset', 'dt7', 'custom'])

    def test_each_native_failure_preserves_other_release(self):
        for failure in ('dt7', 'custom'):
            with self.subTest(failure=failure):
                bridge, trace, _, errors = self.bridge([failure])
                with self.assertRaises(RuntimeError) as raised:
                    bridge.release_all(object(), object())
                self.assertIs(raised.exception, errors[failure])
                self.assertEqual(trace, ['reset', 'publish', 'dt7', 'custom'])

    def test_multiple_failures_preserve_first_exception(self):
        bridge, trace, _, errors = self.bridge(['publish', 'dt7', 'custom'])
        with self.assertRaises(RuntimeError) as raised:
            bridge.release_all(object(), object())
        self.assertIs(raised.exception, errors['publish'])
        self.assertEqual(trace, ['reset', 'publish', 'dt7', 'custom'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
