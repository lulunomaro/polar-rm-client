"""Pure pending-edit owner contracts and dependency isolation."""
import subprocess
import sys
import unittest
from unittest.mock import Mock

from rm_client.ui.preference_draft import PreferenceDraft


class PreferenceDraftTests(unittest.TestCase):
    def setUp(self):
        self.draft = PreferenceDraft()
        self.key = ('setting', 'RobotLink', 'Mouse Sensitivity')
        self.draft.accept({self.key: 1})

    def test_accept_copies_snapshot_and_exposes_read_only_baseline(self):
        snapshot = {self.key: 2}
        self.draft.accept(snapshot)
        snapshot[self.key] = 4
        self.assertEqual(self.draft.baseline[self.key], 2)
        with self.assertRaises(TypeError):
            self.draft.baseline[self.key] = 5

    def test_forced_intent_short_circuits_observation(self):
        self.draft.force_edits([self.key])
        read = Mock(side_effect=AssertionError('forced value observed'))
        self.assertTrue(self.draft.is_pending(self.key, read))
        read.assert_not_called()
        self.assertEqual(self.draft.edits({self.key: 1}), {self.key: 1})

    def test_accept_clears_forced_intent_but_follow_live_does_not(self):
        self.draft.force_edits([self.key])
        self.draft.follow_live(self.key, 2)
        self.assertEqual(self.draft.edits({self.key: 2}), {self.key: 2})
        self.draft.accept({self.key: 2})
        self.assertEqual(self.draft.edits({self.key: 2}), {})

    def test_edit_partitions_retain_input_order_and_all_categories(self):
        keys = [self.key, ('toggle', 'HUD'), ('bind', 'TacticalMap'), ('audio', 'device_id')]
        self.draft.accept(dict.fromkeys(keys, 0))
        values = dict.fromkeys(reversed(keys), 1)
        self.assertEqual(list(self.draft.edits(values)), list(reversed(keys)))

    def test_clean_observation_occurs_exactly_once_and_failures_propagate(self):
        read = Mock(return_value=1)
        self.assertFalse(self.draft.is_pending(self.key, read))
        read.assert_called_once_with()
        read = Mock(side_effect=ValueError('unreadable widget'))
        with self.assertRaisesRegex(ValueError, 'unreadable widget'):
            self.draft.is_pending(self.key, read)
        self.assertEqual(self.draft.baseline[self.key], 1)

    def test_missing_baseline_still_fails_instead_of_accepting_new_fields(self):
        key = ('setting', 'unknown', 'new')
        with self.assertRaises(KeyError):
            self.draft.edits({key: 1})
        with self.assertRaises(KeyError):
            self.draft.is_pending(key, lambda: 1)

    def test_import_does_not_require_qt_model_or_backend(self):
        code = '''
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('PySide6', 'tkinter', 'rm_client.model', 'rm_client.backend')):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from rm_client.ui.preference_draft import PreferenceDraft
assert PreferenceDraft().edits({}) == {}
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
