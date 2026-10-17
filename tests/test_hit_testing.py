"""Pure hit policy: identity, short-circuit ordering, and live target lists."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.hit_testing import topmost_hotspot

class HitTestingTests(unittest.TestCase):
    def hit(self, name, *, button=None, contains=True):
        return NS(name=name, button=button, rect=NS(contains=lambda point: contains))

    def test_last_painted_eligible_target_retains_identity(self):
        bottom = self.hit('bottom')
        right = self.hit('right', button=3)
        outside = self.hit('outside', contains=False)
        self.assertIs(topmost_hotspot([bottom, right, outside], object(), button=1), bottom)
        self.assertIs(topmost_hotspot([bottom, right, outside], object(), button=3), right)
        self.assertIs(topmost_hotspot([bottom, right, outside], object()), right)
        self.assertIsNone(topmost_hotspot([], object()))

    def test_hover_does_not_read_button_and_press_rejects_before_geometry(self):
        class HoverTarget:
            @property
            def button(self): raise AssertionError('hover must not read button')
            rect = NS(contains=lambda point: True)
        target = HoverTarget()
        self.assertIs(topmost_hotspot([target], object()), target)
        class WrongButton:
            button = 3
            @property
            def rect(self): raise AssertionError('ineligible geometry must not be read')
        self.assertIsNone(topmost_hotspot([WrongButton()], object(), button=1))

    def test_success_does_not_evaluate_lower_target(self):
        class NeverRead:
            @property
            def button(self): raise AssertionError('lower button read')
            @property
            def rect(self): raise AssertionError('lower geometry read')
        upper = self.hit('upper')
        for button in (None, 1):
            self.assertIs(topmost_hotspot([NeverRead(), upper], object(), button=button), upper)

    def test_live_list_replacement_is_seen_without_snapshot(self):
        original, replacement = self.hit('old'), self.hit('new')
        outside = self.hit('outside', contains=False)
        targets = [original, outside]
        def replace(point):
            targets[0] = replacement
            return False
        outside.rect.contains = replace
        self.assertIs(topmost_hotspot(targets, object(), button=1), replacement)

    def test_live_point_is_read_after_each_geometry_lookup(self):
        trace = []
        point = ['initial']
        class Target:
            button = None
            @property
            def rect(self):
                trace.append('rect')
                point[0] = 'updated'
                return self
            def contains(self, current):
                trace.append(('contains', current))
                return True
        def current_point():
            trace.append('point')
            return point[0]
        target = Target()
        self.assertIs(topmost_hotspot([target], current_point), target)
        self.assertEqual(trace, ['rect', 'point', ('contains', 'updated')])

    def test_contains_failures_propagate_without_reading_the_next_target(self):
        bottom = self.hit('bottom')
        top = self.hit('top')
        def fail(point): raise RuntimeError('geometry failed')
        top.rect.contains = fail
        with self.assertRaisesRegex(RuntimeError, 'geometry failed'):
            topmost_hotspot([bottom, top], object(), button=1)

if __name__ == '__main__': unittest.main()
