"""Rollback resolution is fallible work and cannot bypass later recovery."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.configuration.transactions import ConfigurationApplyError, apply_configuration_transaction


class RollbackResolutionTests(unittest.TestCase):
    def test_failing_participant_lookup_cannot_skip_other_restoration_or_safety(self):
        setting = SimpleNamespace(value='original', options=['original'])
        module = SimpleNamespace(enabled=False, keybind='None', settings=[setting])
        registry = SimpleNamespace(modules=[module])
        trace = []

        class BrokenParticipant:
            def commit(self):
                trace.append('commit broken')
                raise OSError('partial participant commit')

            @property
            def rollback(self):
                trace.append('resolve broken')
                raise OSError('rollback resolver unavailable')

        healthy = SimpleNamespace(
            commit=lambda: trace.append('commit healthy'),
            rollback=lambda: trace.append('restore healthy'),
        )

        def update():
            setting.value = 'candidate'
            trace.append('update')

        def restore_runtime():
            self.assertEqual(setting.value, 'original')
            trace.append('restore runtime')
            setting.value = 'normalized by runtime'

        with self.assertRaises(ConfigurationApplyError) as caught:
            apply_configuration_transaction(
                registry, quiesce=lambda: trace.append('quiesce'), update=update,
                restore_runtime=restore_runtime, fail_closed=lambda: trace.append('fail closed'),
                participants=(healthy, BrokenParticipant()),
            )
        self.assertEqual(setting.value, 'original')
        self.assertEqual(trace, ['quiesce', 'update', 'commit healthy', 'commit broken',
                                 'resolve broken', 'restore healthy', 'restore runtime', 'fail closed'])
        self.assertEqual(str(caught.exception.cause), 'partial participant commit')
        self.assertEqual([str(error) for error in caught.exception.restore_errors],
                         ['rollback resolver unavailable'])
        self.assertFalse(caught.exception.restored)
        self.assertIsNone(caught.exception.safety_error)


    def test_success_never_resolves_rollback_callbacks(self):
        trace = []

        class Participant:
            def commit(self):
                trace.append('commit')

            @property
            def rollback(self):
                raise AssertionError('rollback resolved after success')

        apply_configuration_transaction(
            SimpleNamespace(modules=[]), quiesce=lambda: trace.append('quiesce'),
            update=lambda: trace.append('update'), restore_runtime=lambda: self.fail('restore'),
            fail_closed=lambda: self.fail('safety'), participants=(Participant(),),
        )
        self.assertEqual(trace, ['quiesce', 'update', 'commit'])

    def test_lookup_runtime_and_safety_errors_remain_distinct(self):
        def fail(message):
            raise OSError(message)

        class Participant:
            @property
            def rollback(self):
                return fail('lookup')

        with self.assertRaises(ConfigurationApplyError) as caught:
            apply_configuration_transaction(
                SimpleNamespace(modules=[]), quiesce=lambda: fail('quiesce'),
                update=lambda: self.fail('update after failed quiesce'),
                restore_runtime=lambda: fail('runtime'), fail_closed=lambda: fail('safety'),
                participants=(Participant(),),
            )
        self.assertEqual(str(caught.exception.cause), 'quiesce')
        self.assertEqual([str(error) for error in caught.exception.restore_errors], ['lookup', 'runtime'])
        self.assertEqual(str(caught.exception.safety_error), 'safety')
        self.assertFalse(caught.exception.restored)


if __name__ == '__main__':
    unittest.main(verbosity=2)
