"""Pure capture-state behavior and legacy field adapters; no QWidget needed."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPointF
from rm_client.ui.polar.pointer_interaction import PointerField, PointerInteraction


class PointerInteractionTests(unittest.TestCase):
    def test_matching_release_dismisses_only_once(self):
        state = PointerInteraction()
        point = QPointF(10, 20)
        state.own_background_press(point)
        point.setX(100)
        result = state.release_background(QPointF(12, 20), left_button=True,
                                          gui_visible=True, background_target=True)
        self.assertTrue(result.consumed)
        self.assertTrue(result.dismiss)
        self.assertIsNone(state.background_press)
        self.assertFalse(state.release_background(QPointF(12, 20), left_button=True,
            gui_visible=True, background_target=True).consumed)

    def test_drag_out_and_back_cannot_dismiss_but_release_remains_consumed(self):
        state = PointerInteraction()
        state.own_background_press(QPointF(10, 20))
        self.assertTrue(state.track_owned_pointer(QPointF(18, 20)))
        self.assertTrue(state.track_owned_pointer(QPointF(10, 20)))
        result = state.release_background(QPointF(10, 20), left_button=True,
                                          gui_visible=True, background_target=True)
        self.assertTrue(result.consumed)
        self.assertFalse(result.dismiss)

    def test_keyboard_and_epoch_cancellation_do_not_leak_the_old_release(self):
        for cancel in ("cancel_background_press", "cancel_epoch"):
            with self.subTest(cancel=cancel):
                state = PointerInteraction(drag={"type": "slider"}, binding_module=SimpleNamespace(keybind="F1"))
                state.own_background_press(QPointF(10, 20))
                getattr(state, cancel)()
                result = state.release_background(QPointF(10, 20), left_button=True,
                                                  gui_visible=True, background_target=True)
                self.assertTrue(result.consumed)
                self.assertFalse(result.dismiss)
                if cancel == "cancel_epoch":
                    self.assertIsNone(state.drag)
                    self.assertIsNone(state.binding_module)

    def test_epoch_cancellation_retains_drag_release_without_background_press(self):
        state = PointerInteraction(drag={"type": "slider"})
        state.cancel_epoch()
        self.assertTrue(state.background_release_owned)
        self.assertTrue(state.track_owned_pointer(QPointF(0, 0)))
        state.cancel_epoch()
        self.assertTrue(state.background_release_owned)

    def test_right_release_does_not_consume_left_capture(self):
        state = PointerInteraction()
        state.own_background_press(QPointF())
        self.assertFalse(state.release_background(QPointF(), left_button=False,
            gui_visible=True, background_target=True).consumed)
        self.assertTrue(state.background_release_owned)

    def test_new_left_press_replaces_old_capture_but_right_press_keeps_release_ownership(self):
        state = PointerInteraction()
        state.own_background_press(QPointF())
        state.begin_press(left_button=False)
        self.assertIsNone(state.background_press)
        self.assertTrue(state.background_release_owned)
        state.begin_press(left_button=True)
        self.assertFalse(state.background_release_owned)

    def test_binding_completion_changes_only_the_captured_target(self):
        module = SimpleNamespace(name="Armor", keybind="None")
        state = PointerInteraction(binding_module=module)
        self.assertIs(state.finish_binding("F7"), module)
        self.assertEqual(module.keybind, "F7")
        self.assertIsNone(state.binding_module)
        self.assertIsNone(state.finish_binding("F8"))
        self.assertEqual(module.keybind, "F7")

    def test_legacy_mixin_hosts_get_independent_owners_and_round_trip_fields(self):
        from rm_client.ui.polar.mixins.input_dispatch_mixin import InputDispatchMixin
        first, second = InputDispatchMixin(), InputDispatchMixin()
        module = SimpleNamespace(name="Armor", keybind="None")
        first.binding_module = module
        first._jello_background_press = QPointF(1, 2)
        first._jello_background_release_owned = True
        self.assertIs(first.pointer_interaction.binding_module, module)
        self.assertEqual(first.pointer_interaction.background_press, QPointF(1, 2))
        self.assertIsNone(second.binding_module)
        self.assertIsNone(second._jello_background_press)
        self.assertFalse(second._jello_background_release_owned)
        first.pointer_interaction.cancel_epoch()
        self.assertIsNone(first.binding_module)
        self.assertIsNone(first._jello_background_press)
        self.assertTrue(first._jello_background_release_owned)

    def test_explicit_field_adapters_preserve_identity_without_cross_instance_state(self):
        class Host:
            drag = PointerField("drag")
            binding_module = PointerField("binding_module")
            def __init__(self):
                self.pointer_interaction = PointerInteraction()
        first, second = Host(), Host()
        drag = {"type": "slider"}
        first.drag = drag
        self.assertIs(first.drag, first.pointer_interaction.drag)
        self.assertIs(first.drag, drag)
        self.assertIsNone(second.drag)
        first.pointer_interaction.cancel_epoch()
        self.assertIsNone(first.drag)
        with self.assertRaises(ValueError):
            PointerField("not_a_field")


if __name__ == "__main__": unittest.main()
