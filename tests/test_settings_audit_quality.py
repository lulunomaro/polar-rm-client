"""Module-aware static audit and real mouse-setting consumer regressions.

Run: python tests/test_settings_audit_quality.py
No window, GL context, hardware, socket, or actual command output is created.
"""
from __future__ import annotations

from pathlib import Path
import sys
import textwrap
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.test_settings_are_read import module_helper_reads


class ModuleHelperAuditTests(unittest.TestCase):
    def reads(self, source):
        return module_helper_reads(textwrap.dedent(source), "RobotLink")

    def test_helper_reads_follow_the_actual_module_lookup(self):
        self.assertEqual(self.reads('''
            def consume(self):
                link = self.registry.get('RobotLink')
                self.get_setting_number(link, 'Mouse Sensitivity', 1.0)
                self.setting_bool(link, 'Invert Mouse Y', False)
                self.setting_mode(self.registry.get('RobotLink'), 'Port', 'Auto')
        '''), {'Mouse Sensitivity', 'Invert Mouse Y', 'Port'})

    def test_comments_docstrings_and_ui_labels_are_not_consumers(self):
        self.assertEqual(self.reads('''
            """RobotLink: self.get_setting_number(link, 'Mouse Sensitivity', 1)"""
            PAGES = {'RobotLink': ('Mouse Sensitivity', 'Invert Mouse Y')}
            def consume(self):
                link = self.registry.get('RobotLink')
                # self.get_setting_number(link, 'Mouse Sensitivity', 1)
                return 'self.setting_bool(link, "Invert Mouse Y", False)'
        '''), set())

    def test_another_module_and_different_function_cannot_supply_evidence(self):
        self.assertEqual(self.reads('''
            def first(self):
                link = self.registry.get('RobotLink')
            def second(self):
                link = self.registry.get('MQTTLink')
                self.get_setting_number(link, 'Port', 0)
                def nested():
                    self.setting_bool(link, 'Mouse Sensitivity', False)
        '''), set())

    def test_reassigned_alias_is_not_assumed_to_still_mean_robotlink(self):
        self.assertEqual(self.reads('''
            def consume(self):
                link = self.registry.get('RobotLink')
                link = self.registry.get('MQTTLink')
                self.get_setting_number(link, 'Port', 0)
        '''), set())

    def test_real_input_module_wires_all_three_mouse_preferences(self):
        source = (ROOT / 'rm_client/ui/polar/mixins/input_dispatch_mixin.py').read_text(encoding='utf-8')
        expected = {'Mouse Sensitivity', 'Invert Mouse Y', 'Mouse Wheel Scale'}
        self.assertLessEqual(expected, module_helper_reads(source, 'RobotLink'))
        # Retaining declarations/documentation cannot hide removing the reads.
        disconnected = source.replace('self.get_setting_number(link,', 'unwired(link,')
        disconnected = disconnected.replace('self.setting_bool(link,', 'unwired(link,')
        self.assertFalse(expected & module_helper_reads(disconnected, 'RobotLink'))


class MousePreferenceConsumerTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QPointF
        from rm_client.control import KeyboardControlBridge
        from rm_client.model import RobotState, build_default_registry
        from rm_client.ui.polar.mixins.input_dispatch_mixin import InputDispatchMixin
        from rm_client.ui.polar.mixins.persistence_mixin import PersistenceMixin
        from rm_client.ui.polar_app import PolarRMWindow

        class Host(InputDispatchMixin, PersistenceMixin):
            setting_bool = PolarRMWindow.setting_bool

        self.host = host = Host()
        host.registry = build_default_registry()
        host.state = RobotState()
        host.control = KeyboardControlBridge()
        host.control.send_from_state = Mock()  # Keep transformed input local.
        host.backend = NS(mqtt=NS(mock_output=True))
        host.combat_control_input_allowed = lambda: True
        host.handle_escape_menu_mouse = lambda *args: False
        host.handle_student_mouse = lambda *args: False
        host.quick_action_wheel = NS(active=False)
        host.overlay_coordinator = NS(radial_active=False)
        host.frame_scheduler = Mock()
        host.hover_tooltip = host._startup_last_hover_action = ''
        host.hover_point = host._control_mouse_point = QPointF(0, 0)
        host.hotspots = host.gui_scroll_viewports = []
        host.economy_visible = host.home_visible = host.escape_menu_visible = False
        host.student_ui = NS(visible=False)
        host.match_chat = NS(input_open=False)
        host.active_clickgui_mode = lambda: 'JelloSigma'
        host.drag = None

    def set_preference(self, name, value):
        self.host.registry.get('RobotLink').get_setting(name).value = value

    def test_live_sensitivity_and_inversion_reach_real_control_transform(self):
        from PySide6.QtCore import QPointF

        self.set_preference('Mouse Sensitivity', 2.5)
        self.set_preference('Invert Mouse Y', False)
        self.host.mouseMoveEvent(NS(position=lambda: QPointF(4, 6)))
        self.assertEqual((self.host.control.mouse_x, self.host.control.mouse_y), (10, -15))
        self.host.control.reset_inputs()
        self.set_preference('Mouse Sensitivity', 0.5)
        self.set_preference('Invert Mouse Y', True)
        self.host.mouseMoveEvent(NS(position=lambda: QPointF(8, 12)))
        self.assertEqual((self.host.control.mouse_x, self.host.control.mouse_y), (2, 3))
        self.host.control.send_from_state.assert_not_called()

    def test_live_wheel_scale_reaches_real_control_accumulator(self):
        from PySide6.QtCore import QPoint, QPointF

        event = NS(position=lambda: QPointF(0, 0), pixelDelta=lambda: QPoint(0, 0),
                   angleDelta=lambda: QPoint(0, 120), accept=Mock())
        for scale, expected in ((2.5, 300), (0.5, 60)):
            with self.subTest(scale=scale):
                self.host.control.reset_inputs()
                self.set_preference('Mouse Wheel Scale', scale)
                self.host.wheelEvent(event)
                self.assertEqual(self.host.control.mouse_z, expected)
        self.assertEqual(self.host.control.send_from_state.call_count, 2)


if __name__ == '__main__':
    unittest.main()
