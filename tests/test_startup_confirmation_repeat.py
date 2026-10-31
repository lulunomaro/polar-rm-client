"""Production launcher key routing with real Qt events and inert page effects."""
import os
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from test_clickgui_background_dismiss import APP, Host
from rm_client.ui.polar.mixins.input_dispatch_mixin import InputDispatchMixin
from rm_client.ui.polar.renderers.startup_scene import StartupSceneMixin
from rm_client.robot_profiles import profiles_for_match
from rm_client.match_modes import MatchModeManager


class StartupConfirmationRepeatTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.host.home_visible = True
        self.host.gui_visible = False
        self.host.wallpaper_picker_visible = False
        self.host.startup_step = 'scenario'
        self.host.scenario_manager = NS(current=NS(key='CampusSimulation'))
        self.host.match_mode_manager = MatchModeManager('RMUC7v7', season=2026)
        self.host.current_robot_profile_key = lambda: 'Hero'
        self.host.startup_page_after_scenario = lambda: 'match'
        self.host.startup_page_before_robot = lambda: 'match'
        self.host.reset_startup_page_animation = Mock()
        self.host.begin_startup_page_transition = lambda page: setattr(self.host, 'startup_step', page)
        self.actions = []
        self.host.apply_scenario = lambda key, **kw: self.actions.append(('scenario', key))
        self.host.apply_match_mode = lambda key, **kw: self.actions.append(('match', key))
        def finish(key, **kw):
            self.actions.append(('robot', key))
            self.host.home_visible = False
        self.host.apply_robot_profile = finish
        self.host.handle_startup_key = MethodType(StartupSceneMixin.handle_startup_key, self.host)

    def tearDown(self):
        self.host.hide()
        self.host.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def key(self, code, *, repeat=False, release=False):
        APP.sendEvent(self.host, QKeyEvent(
            QEvent.Type.KeyRelease if release else QEvent.Type.KeyPress,
            code, Qt.KeyboardModifier.NoModifier, '', repeat))

    def test_held_confirmation_cannot_advance_to_another_page(self):
        for code in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            with self.subTest(key=code):
                self.host.home_visible = True
                self.host.startup_step = 'scenario'
                self.actions.clear()
                self.key(code)
                self.assertEqual(self.host.startup_step, 'match')
                for _ in range(4):
                    self.key(code, repeat=True, release=True)
                    self.key(code, repeat=True)
                    self.assertTrue(self.host.home_visible)
                    self.assertEqual(self.host.startup_step, 'match')
                self.assertEqual(self.actions, [('scenario', 'CampusSimulation')])

    def test_repeated_confirmation_on_each_page_has_no_apply_or_save(self):
        for page in ('scenario', 'match', 'robot'):
            for code in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                with self.subTest(page=page, key=code):
                    self.host.startup_step = page
                    self.host.home_visible = True
                    self.actions.clear()
                    self.key(code, repeat=True)
                    self.assertEqual(self.actions, [])
                    self.assertEqual(self.host.startup_step, page)
                    self.assertTrue(self.host.home_visible)
        self.host.auto_save_config.assert_not_called()

    def test_new_physical_confirmation_still_advances_each_page(self):
        for code in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.host.home_visible = True
            self.host.startup_step = 'scenario'
            self.actions.clear()
            for expected in ('match', 'robot', 'robot'):
                self.key(code)
                self.key(code, release=True)
                self.assertEqual(self.host.startup_step, expected)
            self.assertFalse(self.host.home_visible)
            self.assertEqual([kind for kind, key in self.actions], ['scenario', 'match', 'robot'])

    def test_other_navigation_repeat_semantics_are_unchanged(self):
        self.host.startup_step = 'robot'
        self.key(Qt.Key.Key_Backspace, repeat=True)
        self.assertEqual(self.host.startup_step, 'match')
        self.key(Qt.Key.Key_Backspace, repeat=True)
        self.assertEqual(self.host.startup_step, 'scenario')
        self.assertEqual(self.actions, [])

    def test_held_numeric_selection_cannot_cross_pages(self):
        for expected in ('match', 'robot', 'robot'):
            self.key(Qt.Key.Key_1)
            self.assertEqual(self.host.startup_step, expected)
            before = list(self.actions)
            for _ in range(4):
                self.key(Qt.Key.Key_1, repeat=True, release=True)
                self.key(Qt.Key.Key_1, repeat=True)
                self.assertEqual(self.host.startup_step, expected)
                self.assertEqual(self.actions, before)
            self.key(Qt.Key.Key_1, release=True)
        self.assertFalse(self.host.home_visible)
        self.assertEqual(self.actions, [('scenario', 'OfficialEvent'),
                                       ('match', 'RMUC7v7'), ('robot', 'Hero')])

    def test_supported_numeric_selections_ignore_repeat_but_accept_physical_press(self):
        selections = {
            'scenario': ('OfficialEvent', 'CampusSimulation', 'LabDebug'),
            'match': ('RMUC7v7', 'RMUL3v3'),
            'robot': tuple(profile.key for profile in profiles_for_match('RMUC7v7')[:9]),
        }
        for page, values in selections.items():
            for number, value in enumerate(values, 1):
                with self.subTest(page=page, number=number):
                    self.host.startup_step = page
                    self.host.home_visible = True
                    self.actions.clear()
                    self.host.auto_save_config.reset_mock()
                    code = getattr(Qt.Key, f'Key_{number}')
                    self.key(code, repeat=True)
                    self.assertEqual(self.actions, [])
                    self.assertEqual(self.host.startup_step, page)
                    self.assertTrue(self.host.home_visible)
                    self.host.auto_save_config.assert_not_called()
                    self.key(code)
                    self.assertEqual(self.actions, [(page, value)])
                    self.host.auto_save_config.assert_called_once_with()

    def test_unmapped_numbers_remain_unhandled_including_match_three(self):
        for page, number in (('scenario', 0), ('scenario', 4), ('match', 3), ('robot', 0)):
            for repeat in (False, True):
                with self.subTest(page=page, number=number, repeat=repeat):
                    self.host.startup_step = page
                    code = getattr(Qt.Key, f'Key_{number}')
                    event = QKeyEvent(QEvent.Type.KeyPress, code,
                                      Qt.KeyboardModifier.NoModifier, '', repeat)
                    self.assertFalse(self.host.handle_startup_key(event))
                    self.assertEqual(self.host.startup_step, page)
        self.assertEqual(self.actions, [])
        self.host.auto_save_config.assert_not_called()

    def test_repeats_after_wizard_exit_do_not_trigger_combat_or_module_actions(self):
        # Run the real outer-router callees, with inert control outputs.
        self.host.gui_timeline.progress = 0.0
        self.host.control.resolve_slot.return_value = ()
        for name in ('handle_control_key_event', 'toggle_bound_module_for_event'):
            setattr(self.host, name, MethodType(getattr(InputDispatchMixin, name), self.host))
        module = self.host.registry.get('ArmorAutoAim')
        for code, binding in ((Qt.Key.Key_Return, 'Enter'), (Qt.Key.Key_Enter, 'Enter'),
                              (Qt.Key.Key_1, '1')):
            with self.subTest(key=code):
                module.keybind = binding
                self.host.startup_step = 'robot'
                self.host.home_visible = True
                self.actions.clear()
                self.key(code)
                self.assertFalse(self.host.home_visible)
                before = module.enabled
                self.host.auto_save_config.reset_mock()
                self.host.control.reset_mock()
                for _ in range(4):
                    self.key(code, repeat=True, release=True)
                    self.key(code, repeat=True)
                self.assertEqual(module.enabled, before)
                self.assertEqual(self.actions, [('robot', 'Hero')])
                self.host.auto_save_config.assert_not_called()
                self.host.control.set_key.assert_not_called()
                self.host.control.resolve_slot.assert_not_called()
                self.key(code, release=True)
                self.key(code)
                self.assertNotEqual(module.enabled, before)
                self.host.control.set_key.assert_not_called()


if __name__ == '__main__':
    unittest.main()
