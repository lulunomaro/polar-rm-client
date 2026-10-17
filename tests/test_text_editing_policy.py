"""Qt-free text policies and explicit chat effect ordering."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.match_chat import MatchChatController
from rm_client.ui.polar.text_editing import TextKey, chat_edit, search_edit


class TextEditingPolicyTests(unittest.TestCase):
    def test_chat_printable_payload_and_special_key_precedence(self):
        for key in TextKey:
            for text in ("", "hello中🙂", "a\tb", "\n", " "):
                with self.subTest(key=key, text=text):
                    action = chat_edit(key, text)
                    expected = text if key is TextKey.OTHER and text.isprintable() else ""
                    self.assertEqual(action.text, expected)

    def test_chat_actions_use_only_the_named_editor_and_close_ports(self):
        expected = {
            TextKey.CANCEL: [("close",)], TextKey.SUBMIT: [("send_input",), ("close",)],
            TextKey.BACKSPACE: [("backspace",)], TextKey.UP: [("history_older",)],
            TextKey.DOWN: [("history_newer",)], TextKey.PAGE_UP: [("scroll", 5)],
            TextKey.PAGE_DOWN: [("scroll", -5)], TextKey.OTHER: [("insert", "text")],
        }
        for key, calls in expected.items():
            trace = []
            editor = Mock(spec=("send_input", "backspace", "history_older", "history_newer", "scroll", "insert"))
            for name in ("send_input", "backspace", "history_older", "history_newer", "scroll", "insert"):
                getattr(editor, name).side_effect = lambda *args, name=name: trace.append((name, *args))
            chat_edit(key, "text").apply(editor, close=lambda: trace.append(("close",)))
            self.assertEqual(trace, calls)

    def test_false_send_result_still_closes_but_raised_send_does_not(self):
        editor, close = Mock(send_input=Mock(return_value=False)), Mock()
        chat_edit(TextKey.SUBMIT, "").apply(editor, close=close)
        close.assert_called_once_with()
        close.reset_mock()
        editor.send_input.side_effect = RuntimeError("send failed")
        with self.assertRaisesRegex(RuntimeError, "send failed"):
            chat_edit(TextKey.SUBMIT, "").apply(editor, close=close)
        close.assert_not_called()

    def test_real_chat_editor_keeps_input_limits_and_history(self):
        editor = MatchChatController()
        editor.open_input()
        chat_edit(TextKey.OTHER, "中" * 300).apply(editor, close=editor.close_input)
        self.assertEqual(len(editor.input_text), 256)
        chat_edit(TextKey.SUBMIT, "ignored").apply(editor, close=editor.close_input)
        self.assertFalse(editor.input_open)
        self.assertEqual(len(editor.sent_history[0]), 256)
        editor.open_input()
        chat_edit(TextKey.UP, "ignored").apply(editor, close=editor.close_input)
        self.assertEqual(editor.input_text, "中" * 256)
        chat_edit(TextKey.DOWN, "ignored").apply(editor, close=editor.close_input)
        self.assertEqual(editor.input_text, "")

    def test_search_cancel_submit_and_unprintable_keys_do_not_change_query(self):
        for key in (TextKey.CANCEL, TextKey.SUBMIT):
            edit = search_edit(key, "ignored")
            self.assertTrue(edit.finish)
            self.assertFalse(edit.changes_query)
        for text in ("", "\n", "a\tb"):
            edit = search_edit(TextKey.OTHER, text)
            self.assertFalse(edit.finish)
            self.assertFalse(edit.changes_query)

    def test_search_backspace_and_printable_navigation_payloads(self):
        for query in ("", "a", "中文🙂"):
            edit = search_edit(TextKey.BACKSPACE, "ignored")
            self.assertTrue(edit.changes_query)
            self.assertEqual(edit.apply(query), query[:-1])
        for key in (TextKey.OTHER, TextKey.UP, TextKey.DOWN, TextKey.PAGE_UP, TextKey.PAGE_DOWN):
            edit = search_edit(key, "中")
            self.assertTrue(edit.changes_query)
            self.assertFalse(edit.finish)
            self.assertEqual(edit.apply("prefix"), "prefix中")

    def test_policy_module_has_no_window_qt_backend_or_event_dependency(self):
        import ast
        from rm_client.ui.polar import text_editing
        tree = ast.parse(Path(text_editing.__file__).read_text())
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertEqual(imported, {"__future__", "dataclasses", "enum", "typing"})
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr in
                             {"backend", "registry", "accept", "keybind_matches", "overlay_coordinator"}
                             for node in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main()
