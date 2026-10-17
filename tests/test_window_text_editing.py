"""Real window/editor wiring with real QKeyEvents and inert native starts.

The window is not shown: offscreen input behavior does not qualify native GL,
physical presentation, a live chat transport, or robot/economy output.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("CI", "true")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from rm_client.backend.mqtt_backend import RmMqttBackend
from rm_client.backend.serial_backend import RmSerialBackend
from rm_client.backend.video_backend import RmVideoBackend
from rm_client.ui.polar_app import PolarRMWindow
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

APP = QApplication.instance() or QApplication([])
K = Qt.Key


class WindowTextEditingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="polar-keyboard-")
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": directory.name, "POLAR_AUTOSTART": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        for backend in (RmMqttBackend, RmSerialBackend, RmVideoBackend):
            start = patch.object(backend, "start")
            start.start()
            self.addCleanup(start.stop)
        self.window = PolarRMWindow()
        self.addCleanup(self.close_window)
        settle_window_lifecycle(self.window, APP)
        self.window.timer.stop()
        self.window.ui_audio_enabled = False
        self.window.home_visible = False
        self.window.gui_visible = False
        self.window.handle_control_key_event = Mock(return_value=False)
        self.window.toggle_bound_module_for_event = Mock(return_value=False)

    def close_window(self):
        close_window_safely(self.window, APP)
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def key(self, code, text="", *, repeat=False, release=False):
        kind = QEvent.Type.KeyRelease if release else QEvent.Type.KeyPress
        event = QKeyEvent(kind, code, Qt.KeyboardModifier.NoModifier, text, repeat)
        event.setAccepted(False)
        if release: self.window.keyReleaseEvent(event)
        else: self.window.keyPressEvent(event)
        return event

    def test_real_chat_submit_history_and_search_take_their_own_turn(self):
        window = self.window
        window.registry.get("MatchChat").enabled = True
        window.open_match_chat()
        window.search_focus = True
        window.search_query = "search"
        self.assertTrue(window.match_chat.input_open)
        self.assertTrue(self.key(K.Key_Z, "中文🙂").isAccepted())
        self.assertEqual(window.match_chat.input_text, "中文🙂")
        self.assertEqual(window.search_query, "search")
        self.key(K.Key_Backspace, repeat=True)
        self.key(K.Key_Return, "\r")
        self.assertFalse(window.match_chat.input_open)
        self.assertFalse(window.overlay_coordinator.chat_input)
        self.assertEqual(window.match_chat.sent_history[-1], "中文")
        self.key(K.Key_Z, " query")
        self.assertEqual(window.search_query, "search query")
        self.key(K.Key_Escape)
        self.assertFalse(window.search_focus)
        self.assertFalse(window.escape_menu_visible)
        window.open_match_chat()
        self.key(K.Key_Up)
        self.assertEqual(window.match_chat.input_text, "中文")
        self.key(K.Key_Escape)
        self.assertEqual(window.match_chat.input_text, "中文")
        window.handle_control_key_event.assert_not_called()
        window.toggle_bound_module_for_event.assert_not_called()

    def test_pending_profile_and_closing_reject_text_before_editor_mutation(self):
        window = self.window
        window.match_chat.open_input()
        window.match_chat.input_text = "kept"
        with patch.object(window, "_pending_configuration", object(), create=True):
            self.assertTrue(self.key(K.Key_Z, "lost").isAccepted())
            self.assertEqual(window.match_chat.input_text, "kept")
        # The public guard uses this lifecycle owner's live closing snapshot.
        with patch.object(window._window_lifecycle, "closing", True):
            self.assertTrue(self.key(K.Key_Backspace).isAccepted())
            self.assertEqual(window.match_chat.input_text, "kept")
        self.key(K.Key_Z, " now")
        self.assertEqual(window.match_chat.input_text, "kept now")

    def test_real_economy_selection_precedes_search_and_repeat_cannot_activate(self):
        window = self.window
        categories = window.economy_categories()
        self.assertGreater(len(categories), 1)
        window.economy_visible = True
        window.economy_category = categories[0]
        window.economy_selected_key = "stale"
        window.economy_offers_scroll.position = 17.
        window.economy_offers_scroll.target = 23.
        window.search_focus = True
        window.search_query = "untouched"
        self.key(K.Key_Right)
        self.assertEqual(window.economy_category, categories[1])
        self.assertIn(window.economy_selected_key, [offer.key for offer in window.economy_offers(categories[1])])
        self.assertEqual((window.economy_offers_scroll.position, window.economy_offers_scroll.target), (0., 0.))
        self.assertEqual(window.search_query, "untouched")
        with patch.object(window, "begin_economy_hold") as hold, patch.object(window, "activate_economy_offer") as activate:
            window.economy_selected_key = "stale"
            self.key(K.Key_Return, repeat=True)
            self.assertNotEqual(window.economy_selected_key, "stale")
            hold.assert_not_called()
            activate.assert_not_called()
        window.handle_control_key_event.assert_not_called()
        window.toggle_bound_module_for_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
