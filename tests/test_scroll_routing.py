"""Pure wheel target policy, without Qt, window, transport or scroll mutation."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.scroll_routing import ScrollFamily, scroll_target_at


def viewport(mode, *, hit=True, maximum=10., key='key'):
    return dict(rect=NS(contains=lambda point: hit), mode=mode, key=key, maximum=maximum)


class ScrollRoutingTests(unittest.TestCase):
    def test_mode_owners_and_unknown_fallback(self):
        expected = {
            'jello_dropdown': (ScrollFamily.JELLO_DROPDOWN, None, True),
            'jello_panel': (ScrollFamily.JELLO_PANEL, None, True),
            'jello_settings': (ScrollFamily.JELLO_SETTINGS, None, True),
            'old': (ScrollFamily.LEGACY_PANEL, 'old_panels', False),
            'new': (ScrollFamily.LEGACY_PANEL, 'new_panels', False),
            'dropdown': (ScrollFamily.LEGACY_PANEL, 'dropdown_panels', False),
            'polar_multi': (ScrollFamily.LEGACY_PANEL, 'polar_windows', False),
            'polar_settings': (ScrollFamily.SETTINGS_MOTION, None, False),
            'polar_unified': (ScrollFamily.UNIFIED_MOTION, None, False),
            'unknown': (ScrollFamily.UNIFIED_MOTION, None, False),
        }
        for mode, values in expected.items():
            with self.subTest(mode=mode):
                plan = scroll_target_at([viewport(mode)], None)
                self.assertEqual((plan.family, plan.panel_attribute, plan.is_jello), values)
                self.assertEqual((plan.key, plan.maximum), ('key', 10.))

    def test_last_hit_wins_and_missed_metadata_is_never_read(self):
        missed = {'rect': NS(contains=lambda point: False)}
        plan = scroll_target_at([viewport('old'), viewport('new'), missed], None)
        self.assertEqual(plan.panel_attribute, 'new_panels')
        self.assertIsNone(scroll_target_at([missed], None))
        self.assertIsNone(scroll_target_at([], None))

    def test_metadata_read_order_and_lazy_live_list(self):
        reads = []
        class Metadata(dict):
            def __getitem__(self, key):
                reads.append(key)
                return super().__getitem__(key)
        target = Metadata(viewport('old', maximum='17'))
        targets = [viewport('new'), {'rect': NS()}]
        def replace(point):
            targets[0] = target
            return False
        targets[1]['rect'].contains = replace
        plan = scroll_target_at(targets, None)
        self.assertEqual(plan.maximum, 17.)
        self.assertEqual(plan.panel_attribute, 'old_panels')
        self.assertEqual(reads, ['rect','mode','key','maximum'])

    def test_invalid_maximum_and_missing_metadata_keep_exceptions(self):
        with self.assertRaises(ValueError):
            scroll_target_at([viewport('old', maximum='bad')], None)
        with self.assertRaises(KeyError):
            scroll_target_at([{'rect': NS(contains=lambda point: True)}], None)

    def test_unknown_unhashable_mode_still_uses_legacy_fallback(self):
        plan = scroll_target_at([viewport([])], None)
        self.assertIs(plan.family, ScrollFamily.UNIFIED_MOTION)

    def test_negative_maximum_is_not_silently_normalized_by_selection(self):
        self.assertEqual(scroll_target_at([viewport('old', maximum=-4.)], None).maximum,-4.)

if __name__ == '__main__': unittest.main()
