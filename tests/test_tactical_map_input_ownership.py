"""Real-window regression for liquid map geometry and complete pointer ownership.

Before the correction these assertions deliberately expose input falling into
combat control. Only final backend/control sinks are inert; rendering, input
routing, pointer ownership, coordinates and Qt events are production paths.
"""
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_tactical_map_contract as contract
from tactical_map_contract_fixture import scene, set_setting
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent, QPainter, QTransform
from rm_client.backend.safety import ControlState
from rm_client.ui.polar.pointer_interaction import PointerInteraction
from rm_client.ui.polar.primitives import Hotspot


@contextmanager
def restore_attribute(target, name, value):
    """Restore a public field through its descriptor without deleting it."""
    previous = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, previous)


def mouse(kind, point, button=Qt.MouseButton.LeftButton):
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
    return QMouseEvent(kind, point, point, button if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
                       buttons, Qt.KeyboardModifier.NoModifier)


class TacticalMapInputOwnershipTests(unittest.TestCase):
    setUpClass = classmethod(contract.TacticalMapContractTests.setUpClass.__func__)
    tearDownClass = classmethod(contract.TacticalMapContractTests.tearDownClass.__func__)

    @contextmanager
    def surface(self, **options):
        with scene(self.window, hold=options.pop('hold', True), progress=options.pop('progress', 1.), **options), \
             restore_attribute(self.window, 'home_visible', False), \
             patch.object(self.window, '_pointer_interaction', PointerInteraction(), create=True), \
             patch.object(self.window, '_control_mouse_point', QPointF(900., 700.)), \
             patch.object(self.window, '_tactical_map_hit_geometry', None, create=True):
            self.assertTrue(self.window.combat_control_input_allowed())
            yield self.window

    def paint(self, *, width=960, height=640, dpr=1., scale=1., translate=(0., 0.)):
        window = self.window
        image = QImage(round(width * dpr), round(height * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr); image.fill(0)
        painter = QPainter(image)
        painter.translate(*translate); painter.scale(scale, scale)
        geometry = []
        original = window.draw_official_map
        def capture(p, rect, config):
            result = original(p, rect, config)
            geometry.append((QRectF(result), QTransform(p.combinedTransform())))
            return result
        try:
            with patch.object(window, 'draw_official_map', side_effect=capture):
                window.draw_tactical_map_hud(painter, width, height)
        finally:
            painter.end()
        self.assertEqual(len(geometry), 1)
        return geometry[0]

    def blank_point(self, rect, transform):
        for x, y in ((.37, .23), (.23, .37), (.61, .43), (.43, .61), (.75, .81)):
            local = QPointF(rect.left() + rect.width() * x, rect.top() + rect.height() * y)
            screen = transform.map(local)
            if not any(h.rect.contains(screen) or transform.mapRect(h.rect).contains(screen) for h in self.window.hotspots):
                return screen, x, 1. - y
        self.fail('Fixture needs a visible point without a registered map hotspot')

    def test_liquid_publishes_fresh_layout_rect_and_blue_mirror(self):
        for side in ('Red', 'Blue'):
            with self.subTest(side=side), self.surface(liquid=True, side=side) as window:
                rect, _ = self.paint()
                self.assertEqual(window.tactical_map_field_rect, rect)
                self.assertIs(window.tactical_map_field_layout, window.match_mode_manager.current.field)
                self.assertEqual(window.tactical_map_field_mirrored, side == 'Blue')

    def test_blank_liquid_press_and_release_never_reach_combat(self):
        for rejected in (False, True):
            with self.subTest(rejected=rejected), self.surface(liquid=True) as window:
                rect, transform = self.paint(); point, nx, ny = self.blank_point(rect, transform)
                with patch.object(window.backend, 'publish_map_click_cmd', return_value=(not rejected, 'blocked')) as publish, \
                     patch.object(window.control, 'set_mouse_button') as control, patch.object(window, 'toast'):
                    window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point))
                    window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, point))
                self.assertEqual(publish.call_count, 1)
                self.assertEqual(publish.call_args.kwargs['map_x'], round(nx * 28, 3))
                self.assertEqual(publish.call_args.kwargs['map_y'], round(ny * 15, 3))
                control.assert_not_called()

    def test_publisher_exception_still_owns_drag_release_and_clears_old_anchor(self):
        for liquid in (False, True):
            for stage in ('publisher', 'settings', 'toast', 'hotspot'):
                with self.subTest(liquid=liquid, stage=stage), self.surface(liquid=liquid) as window:
                    self.assert_failure_sequence(window, stage)

    def assert_failure_sequence(self, window, stage):
        rect, transform = self.paint(); point, _, _ = self.blank_point(rect, transform)
        target, method = {
            'publisher': (window.backend, 'publish_map_click_cmd'),
            'settings': (window, 'map_click_command_values'),
            'toast': (window, 'toast'),
            'hotspot': (window, 'dispatch'),
        }[stage]
        if stage == 'hotspot':
            window.hotspots.append(Hotspot(QRectF(point.x()-3, point.y()-3, 6, 6), 'map_robot', 7))
        with patch.object(window.backend, 'publish_map_click_cmd', return_value=(False, 'blocked')), \
             patch.object(target, method, side_effect=RuntimeError('map publisher failed')), \
             patch.object(window.control, 'set_mouse_button') as control, \
             patch.object(window.control, 'add_mouse_motion') as motion:
            with self.assertRaisesRegex(RuntimeError, 'map publisher failed'):
                window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point))
            window.mouseMoveEvent(mouse(QEvent.Type.MouseMove, QPointF(700, 500)))
            window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, QPointF(700, 500)))
            window.mouseMoveEvent(mouse(QEvent.Type.MouseMove, QPointF(710, 510)))
            motion.assert_not_called()
            window.mouseMoveEvent(mouse(QEvent.Type.MouseMove, QPointF(713, 514)))
            self.assertEqual(motion.call_count, 1)
            self.assertEqual(motion.call_args.args[:2], (3., 4.))
        control.assert_not_called()

    def test_classic_to_liquid_switch_replaces_previous_geometry_and_mirroring(self):
        with self.surface(liquid=False, side='Red') as window:
            old_rect, _ = self.paint(width=1280, height=720)
            window.registry.get('LiquidTacticalMap').enabled = True
            window.tactical_map_state.update(.08)
            with patch.object(window, 'alliance_side', return_value='Blue'):
                new_rect, transform = self.paint(width=640, height=360)
            self.assertNotEqual(old_rect, new_rect)
            self.assertEqual(window.tactical_map_field_rect, new_rect)
            self.assertTrue(window.tactical_map_field_mirrored)
            point, nx, ny = self.blank_point(new_rect, transform)
            with patch.object(window.backend, 'publish_map_click_cmd', return_value=(True, '')) as publish, patch.object(window, 'toast'):
                window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point))
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(publish.call_args.kwargs['map_x'], round((1. - nx) * 28, 3))
            self.assertEqual(publish.call_args.kwargs['map_y'], round((1. - ny) * 15, 3))

    def test_scale_translation_resize_and_dpr_use_logical_mouse_coordinates(self):
        for liquid in (False, True):
            for width, height, dpr, scale in ((960, 640, 1., 1.), (1280, 720, 1.25, .82),
                                            (800, 600, 2., 1.1), (640, 360, 2.5, .75)):
                with self.subTest(liquid=liquid, size=(width, height), dpr=dpr, scale=scale), self.surface(liquid=liquid) as window:
                    rect, transform = self.paint(width=width, height=height, dpr=dpr, scale=scale, translate=(13.5, 7.25))
                    point, nx, ny = self.blank_point(rect, transform)
                    with patch.object(window.backend, 'publish_map_click_cmd', return_value=(True, '')) as publish, patch.object(window, 'toast'):
                        window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point))
                    self.assertEqual(publish.call_count, 1)
                    self.assertEqual(publish.call_args.kwargs['map_x'], round(nx * 28, 3))
                    self.assertEqual(publish.call_args.kwargs['map_y'], round(ny * 15, 3))

    def test_map_hotspot_keeps_priority_but_owns_its_release(self):
        with self.surface(liquid=True) as window:
            rect, transform = self.paint(); point, _, _ = self.blank_point(rect, transform)
            window.hotspots.append(Hotspot(QRectF(point.x()-3, point.y()-3, 6, 6), 'map_robot', 7))
            with patch.object(window.backend, 'publish_map_click_cmd') as publish, \
                 patch.object(window.control, 'set_mouse_button') as control, patch.object(window, 'dispatch') as dispatch:
                window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point))
                window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, point))
            dispatch.assert_called_once()
            self.assertEqual(dispatch.call_args.args[:2], ('map_robot', 7))
            publish.assert_not_called(); control.assert_not_called()

    def test_reverse_after_press_keeps_ownership_and_right_release_does_not_steal_it(self):
        with self.surface(liquid=True) as window:
            rect, transform = self.paint(); point, _, _ = self.blank_point(rect, transform)
            with patch.object(window.backend, 'publish_map_click_cmd', return_value=(False, 'blocked')) as publish, \
                 patch.object(window.control, 'set_mouse_button') as control, patch.object(window, 'toast'):
                window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point))
                self.assertTrue(window.pointer_interaction.background_release_owned)
                window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, point, Qt.MouseButton.RightButton))
                self.assertTrue(window.pointer_interaction.background_release_owned)
                control.reset_mock()  # The unrelated right release retains existing right-control behavior.
                window.handle_tactical_map_hold_event(QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Tab,
                    Qt.KeyboardModifier.NoModifier), False)
                window.tactical_map_state.update(.015)
                window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, QPointF(750, 550)))
                control.assert_not_called()
            self.assertFalse(window.pointer_interaction.background_release_owned)
            self.assertEqual(publish.call_count, 1)

    def test_collapsed_and_right_clicks_preserve_normal_combat_routing(self):
        for liquid in (False, True):
            for hold, progress, button in ((False, 0., Qt.MouseButton.LeftButton),
                                           (False, .62, Qt.MouseButton.LeftButton),
                                           (True, 1., Qt.MouseButton.RightButton)):
                with self.subTest(liquid=liquid, hold=hold, button=button), self.surface(liquid=liquid, hold=hold, progress=progress) as window:
                    window.tactical_map_hold = hold
                    rect, transform = self.paint(); point, _, _ = self.blank_point(rect, transform)
                    with patch.object(window.backend, 'publish_map_click_cmd') as publish, patch.object(window.control, 'set_mouse_button') as control:
                        window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point, button))
                        window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, point, button))
                    publish.assert_not_called()
                    self.assertEqual([call.args[:2] for call in control.call_args_list], [(1 if button == Qt.MouseButton.LeftButton else 3, True),
                                                                                       (1 if button == Qt.MouseButton.LeftButton else 3, False)])

    def test_visible_field_edges_reject_outside_and_accept_inside(self):
        for liquid in (False, True):
            with self.subTest(liquid=liquid), self.surface(liquid=liquid) as window:
                rect, transform = self.paint(scale=.82, translate=(13.5, 7.25), dpr=2.)
                window.hotspots.clear()
                for x, y, inside in ((rect.left() - .1, rect.center().y(), False),
                                     (rect.right() + .1, rect.center().y(), False),
                                     (rect.center().x(), rect.top() - .1, False),
                                     (rect.center().x(), rect.bottom() + .1, False),
                                     (rect.left() + .1, rect.center().y(), True),
                                     (rect.right() - .1, rect.center().y(), True),
                                     (rect.center().x(), rect.top() + .1, True),
                                     (rect.center().x(), rect.bottom() - .1, True)):
                    with self.subTest(inside=inside, local=(x, y)):
                        point = transform.map(QPointF(x, y))
                        with patch.object(window.backend, 'publish_map_click_cmd', return_value=(True, '')) as publish, \
                             patch.object(window.control, 'set_mouse_button') as control, patch.object(window, 'toast'):
                            window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point))
                            window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, point))
                        self.assertEqual(publish.call_count, int(inside))
                        self.assertEqual(control.call_count, 0 if inside else 2)

    def test_disabling_map_or_failed_paint_cannot_reuse_previous_hit_snapshot(self):
        for liquid in (False, True):
            for failure in (False, True):
                with self.subTest(liquid=liquid, failure=failure), self.surface(liquid=liquid) as window:
                    rect, transform = self.paint(); point, _, _ = self.blank_point(rect, transform)
                    image = QImage(960, 640, QImage.Format.Format_ARGB32_Premultiplied)
                    painter = QPainter(image)
                    try:
                        if failure:
                            with patch.object(window, 'draw_official_map', side_effect=RuntimeError('no current map')):
                                with self.assertRaisesRegex(RuntimeError, 'no current map'):
                                    window.draw_tactical_map_hud(painter, 960, 640)
                        else:
                            window.registry.get('TacticalMap').enabled = False
                            window.draw_tactical_map_hud(painter, 960, 640)
                    finally:
                        painter.end()
                    window.hotspots.clear()
                    with patch.object(window.backend, 'publish_map_click_cmd', return_value=(False, 'blocked')) as publish, patch.object(window, 'toast'):
                        self.assertFalse(window.publish_map_click_from_screen(point))
                    publish.assert_not_called()

    def test_round_mask_corners_are_not_part_of_the_visible_field_hit(self):
        with self.surface(liquid=True) as window:
            rect, transform = self.paint(width=960, height=494, dpr=2.)
            self.assertTrue(window._map_round_cache, 'The actual draw must have used the rounded bitmap')
            corner = transform.map(rect.topLeft() + QPointF(.1, .1))
            with patch.object(window.backend, 'publish_map_click_cmd') as publish:
                self.assertFalse(window.publish_map_click_from_screen(corner))
            publish.assert_not_called()

    def test_opening_progress_and_mirror_switches_follow_the_current_draw(self):
        for liquid in (False, True):
            for progress in (.17, .65, 1.):
                for module, setting in ((None, None), ('TacticalMap', 'Mirror Blue'), ('AllianceProfile', 'Mirror Tactical Map')):
                    with self.subTest(liquid=liquid, progress=progress, setting=setting), self.surface(liquid=liquid, side='Blue', progress=progress) as window:
                        if module is not None:
                            set_setting(window.registry, module, setting, False)
                        rect, transform = self.paint(scale=.82, dpr=1.5)
                        point, nx, ny = self.blank_point(rect, transform)
                        with patch.object(window.backend, 'publish_map_click_cmd', return_value=(False, 'blocked')) as publish, \
                             patch.object(window.control, 'set_mouse_button') as control, patch.object(window, 'toast'):
                            window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point))
                            window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, point))
                        mirrored = module is None
                        self.assertEqual(publish.call_args.kwargs['map_x'], round((1. - nx if mirrored else nx) * 28, 3))
                        self.assertEqual(publish.call_args.kwargs['map_y'], round((1. - ny if mirrored else ny) * 15, 3))
                        control.assert_not_called()

    def test_real_backend_locked_or_disabled_never_reaches_native_publisher(self):
        cases = ((False, ControlState.LOCKED, 'mock-only'),
                 (True, ControlState.LOCKED, 'LOCKED'),
                 (True, ControlState.QUALIFICATION, 'qualification token'),
                 (True, ControlState.MATCH_ARMED, 'capability'))
        for liquid in (False, True):
            for allowed, state, reason in cases:
                with self.subTest(liquid=liquid, allowed=allowed, state=state), self.surface(liquid=liquid) as window:
                    rect, transform = self.paint(); point, _, _ = self.blank_point(rect, transform)
                    client = Mock()
                    with patch.object(window.backend.mqtt, 'client', client), \
                         patch.object(window.backend.mqtt, 'connected', True), patch.object(window.backend.mqtt, 'mock_output', False), \
                         patch.object(window.backend.safety, 'allow_mqtt_publish', allowed), \
                         patch.object(window.backend.safety, 'control_state', state), \
                         patch.object(window.backend.safety, '_authorized_capabilities', frozenset()), \
                         patch.object(window.backend, 'publish_map_click_cmd', wraps=window.backend.publish_map_click_cmd) as publish, \
                         patch.object(window.control, 'set_mouse_button') as control, patch.object(window, 'toast') as toast:
                        window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point))
                        window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, point))
                    self.assertEqual(publish.call_count, 1)
                    self.assertIn(reason, toast.call_args.args[0])
                    client.publish.assert_not_called(); control.assert_not_called()


if __name__ == '__main__':
    unittest.main()
