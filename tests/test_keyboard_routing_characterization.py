"""Frozen differential traces for keyboard priority and interrupted editor input.

The reference is checked in with its source provenance, so these tests work in
an offline shallow/public checkout. This suite does not refresh any reference.
"""
from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import inspect
from itertools import combinations, product
from pathlib import Path
import random
import sys
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from PySide6.QtCore import Qt
from keyboard_routing_harness import BASELINE, BASELINE_PATH, Case, Event, Harness, Key, current, run_case

FIXTURE_SHA256 = "cb71cca8ba3e5cab8cde3d965c06e325aa00652bdbc1ee0f1f53590e621f4dc9"
K = Qt.Key
FLAGS = ("lifecycle_pending", "configuration_pending", "closing", "escape", "binding", "home",
         "wallpaper", "startup_handles", "hero", "student", "chat", "wheel", "radial", "economy",
         "search", "gui", "settings", "legacy", "blocks", "minecraft", "slot_handles")
KEYS = (K.Key_F12, K.Key_Escape, K.Key_F8, K.Key_F9, K.Key_F7, K.Key_F11, K.Key_Shift,
        K.Key_Return, K.Key_Enter, K.Key_Backspace, K.Key_Up, K.Key_Down, K.Key_Left, K.Key_Right,
        K.Key_PageUp, K.Key_PageDown, K.Key_Tab, K.Key_W, K.Key_X, K.Key_1, K.Key_9, K.Key_Z)


def labels(result):
    return [entry[0] for entry in result[0]]


class KeyboardRoutingDifferentialTests(unittest.TestCase):
    def assert_same(self, case, keys, *, fail_at=None):
        expected = run_case(case, keys, baseline=True, fail_at=fail_at,
                            correct_economy_repeat_release=True)
        actual = run_case(case, keys, fail_at=fail_at)
        self.assertEqual(actual, expected)
        if fail_at is None:
            self.assertIsNone(actual[-1], actual)
        return actual

    def test_frozen_fixture_integrity(self):
        self.assertEqual(hashlib.sha256(BASELINE_PATH.read_bytes()).hexdigest(), FIXTURE_SHA256)

    def test_every_key_repeat_and_single_owner(self):
        cases = [Case()] + [Case(**{flag: True}) for flag in FLAGS]
        cases += [Case(home=True, wallpaper=True), Case(home=True, startup_handles=True),
                  Case(gui=True, settings=True), Case(gui=True, legacy=True),
                  Case(escape=True, pending_releases=(K.Key_W,)),
                  Case(right_shift=True), Case(economy=True, categories=(), offers=()),
                  Case(economy=True, category="missing", selected="missing")]
        for case, code, repeat, phase in product(cases, KEYS, (False, True), ("press", "release")):
            with self.subTest(case=case, code=code, repeat=repeat, phase=phase):
                self.assert_same(case, [Key(code, text="中z" if code == K.Key_Z else "", repeat=repeat, phase=phase)])

    def test_competing_owners_keep_original_priority(self):
        for first, second in combinations(FLAGS, 2):
            case = Case(**{first: True, second: True}, bindings={
                "HeroLob": "X", "MatchChat": "X", "QuickActionWheel": "X",
                "EconomyPanel": "X", "ProjectileShop": "X", "TacticalMap": "X", "Armor": "X"})
            for code, repeat in product((K.Key_F12, K.Key_Escape, K.Key_F8, K.Key_Return, K.Key_X), (False, True)):
                with self.subTest(owners=(first, second), code=code, repeat=repeat):
                    self.assert_same(case, [Key(code, repeat=repeat)])

    def test_generated_bindings_modifiers_and_empty_editor_states(self):
        rng = random.Random(20261016)
        modules = ("HeroLob", "MatchChat", "QuickActionWheel", "EconomyPanel", "ProjectileShop", "TacticalMap", "Armor")
        modifiers = (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier,
                     Qt.KeyboardModifier.ShiftModifier,
                     Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        for number in range(1200):
            case = Case(**{flag: rng.random() < .13 for flag in FLAGS},
                        deploy_mode=rng.randrange(2), right_shift=bool(rng.randrange(2)),
                        bindings={name: rng.choice(("None", "X", "W", "Tab", "Return", "F12")) for name in modules},
                        disabled=tuple(name for name in modules if rng.random() < .2),
                        missing=tuple(name for name in modules if name != "Armor" and rng.random() < .15),
                        pending_releases=tuple(code for code in KEYS if rng.random() < .1),
                        categories=rng.choice(((), ("ammo",), ("ammo", "support", "revive"))),
                        offers=rng.choice(((), ("small",), ("small", "medium", "large"))),
                        selected=rng.choice(("small", "medium", "unknown")),
                        category=rng.choice(("ammo", "support", "unknown")),
                        confirmation=rng.choice(("hold", "double", "none")),
                        selected_available=bool(rng.randrange(2)), chat_send_succeeds=bool(rng.randrange(2)))
            keys = [Key(rng.choice(KEYS), text=rng.choice(("", "a", "中🙂", "\n", "a\tb")),
                        repeat=bool(rng.randrange(2)), modifiers=rng.choice(modifiers),
                        phase=rng.choice(("press", "release"))) for _ in range(3)]
            with self.subTest(number=number):
                self.assert_same(case, keys)

    def test_exception_prefixes_preserve_already_applied_effects(self):
        examples = (
            (Case(), Key(K.Key_F12)),
            (Case(), Key(K.Key_F12, modifiers=Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)),
            (Case(chat=True), Key(K.Key_Return)),
            (Case(chat=True), Key(K.Key_Z, "中z")),
            (Case(economy=True), Key(K.Key_Left)),
            (Case(economy=True), Key(K.Key_Down)),
            (Case(economy=True), Key(K.Key_Return)),
            (Case(bindings={"QuickActionWheel": "X"}), Key(K.Key_X)),
            (Case(hero=True), Key(K.Key_F8)),
            (Case(bindings={"Armor": "X"}), Key(K.Key_X)),
        )
        for case, key in examples:
            reference = self.assert_same(case, [key])
            for fail_at in range(1, len(reference[0]) + 1):
                with self.subTest(case=case, key=key, fail_at=fail_at):
                    self.assert_same(case, [key], fail_at=fail_at)

    def test_repeated_and_interrupted_press_release_sequences(self):
        sequences = (
            (Case(), [Key(K.Key_W), Key(K.Key_W, repeat=True), Key(K.Key_Escape),
                      Key(K.Key_W, phase="release"), Key(K.Key_Escape), Key(K.Key_Escape, phase="release"),
                      Key(K.Key_W), Key(K.Key_W, phase="release")]),
            (Case(pending_releases=(K.Key_W,)), [Key(K.Key_W, repeat=True), Key(K.Key_W, repeat=True, phase="release"),
                       Key(K.Key_W), Key(K.Key_W, phase="release")]),
            (Case(bindings={"QuickActionWheel": "X"}), [Key(K.Key_X), Key(K.Key_X, repeat=True),
                       Key(K.Key_X, repeat=True, phase="release"), Key(K.Key_X, phase="release")]),
            (Case(bindings={"TacticalMap": "M"}), [Key(K.Key_Tab), Key(K.Key_Tab, repeat=True),
                       Key(K.Key_Tab, repeat=True, phase="release"), Key(K.Key_Tab, phase="release")]),
            (Case(chat=True), [Key(K.Key_Z, "hello中"), Key(K.Key_Backspace, repeat=True),
                       Key(K.Key_Return), Key(K.Key_Return, phase="release")]),
        )
        for case, keys in sequences:
            with self.subTest(case=case): self.assert_same(case, keys)

    def test_text_getter_is_only_consulted_by_the_existing_text_branches(self):
        cases = ((Case(chat=True), (K.Key_Escape, K.Key_Return, K.Key_Enter, K.Key_Backspace,
                                    K.Key_Up, K.Key_Down, K.Key_PageUp, K.Key_PageDown)),
                 (Case(search=True), (K.Key_Escape, K.Key_Return, K.Key_Enter, K.Key_Backspace)),
                 (Case(chat=True, closing=True), KEYS))
        with patch.object(Event, "text", side_effect=RuntimeError("text getter failed")):
            for case, codes in cases:
                for code in codes:
                    with self.subTest(case=case, code=code):
                        self.assert_same(case, [Key(code)])
            for case in (Case(chat=True), Case(search=True)):
                keys = [Key(K.Key_Z)]
                expected = run_case(case, keys, baseline=True)
                self.assertEqual(run_case(case, keys), expected)
                self.assertEqual(expected[-1], ("RuntimeError", "text getter failed"))
                self.assertNotIn("event.accept", labels(expected))
                self.assertNotIn("frame.force", labels(expected))

    def test_search_finish_does_not_read_or_rewrite_the_query(self):
        for implementation in (BASELINE, current.InputDispatchMixin):
            for code in (K.Key_Return, K.Key_Enter, K.Key_Escape):
                host = Harness(Case(search=True))
                del host.search_query
                event = Event(Key(code), host)
                implementation.keyPressEvent(host, event)
                self.assertFalse(host.search_focus)
                self.assertNotIn("search_query", host.__dict__)

    def test_category_query_failure_preserves_its_prior_field_writes(self):
        for implementation in (BASELINE, current.InputDispatchMixin):
            host = Harness(Case(economy=True))
            def fail_selected():
                raise RuntimeError("selected offer query failed")
            host.economy_selected_offer = fail_selected
            event = Event(Key(K.Key_Left), host)
            with self.assertRaisesRegex(RuntimeError, "selected offer query failed"):
                implementation.keyPressEvent(host, event)
            self.assertEqual(host.economy_category, "ammo")
            self.assertEqual(host.economy_selected_key, "")
            self.assertEqual((host.economy_offers_scroll.position, host.economy_offers_scroll.target), (17., 23.))
            self.assertFalse(event.accepted)
            self.assertNotIn("frame.force", [entry[0] for entry in host.trace])

    def test_priority_mutation_is_detected(self):
        # Demonstrate that the differential gate can reject a real dispatch
        # reordering; do not mutate production source or the frozen reference.
        source = textwrap.dedent(inspect.getsource(current.InputDispatchMixin.keyPressEvent))
        tree = ast.parse(source)
        method = tree.body[0]
        branches = {ast.unparse(node.test): index for index, node in enumerate(method.body) if isinstance(node, ast.If)}
        chat_index = branches["self.match_chat.input_open"]
        wheel_index = branches["self.quick_action_wheel.active or self.overlay_coordinator.radial_active"]
        method.body[chat_index], method.body[wheel_index] = method.body[wheel_index], method.body[chat_index]
        scope = dict(current.__dict__)
        exec(compile(ast.fix_missing_locations(tree), "<keyboard-priority-mutation>", "exec"), scope)
        case, keys = Case(chat=True, wheel=True), [Key(K.Key_Z, "important")]
        expected = run_case(case, keys, baseline=True)
        with patch.object(current.InputDispatchMixin, "keyPressEvent", scope["keyPressEvent"]):
            self.assertNotEqual(run_case(case, keys), expected)


class KeyboardSafetyAndEditorContracts(unittest.TestCase):
    def test_estop_precedes_all_modal_and_lifecycle_owners(self):
        case = Case(**{flag: True for flag in FLAGS})
        result = run_case(case, [Key(K.Key_F12)])
        self.assertEqual(labels(result), ["pointer.cancel", "backend.trigger_estop", "control.reset_inputs",
                                         "control.clear_pending", "toast", "event.accept"])
        self.assertEqual(result[2:], ([True], None))

    def test_estop_release_is_denied_during_each_lifecycle_guard(self):
        modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        for field in ("lifecycle_pending", "configuration_pending", "closing"):
            result = run_case(Case(**{field: True}), [Key(K.Key_F12, modifiers=modifiers)])
            self.assertEqual(labels(result), ["pointer.cancel", "lifecycle.guard", "event.accept"])
            self.assertEqual(result[2:], ([True], None))

    def test_autorepeated_f12_never_releases_or_triggers_estop(self):
        modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        result = run_case(Case(lifecycle_pending=True), [Key(K.Key_F12, repeat=True, modifiers=modifiers)])
        self.assertEqual(labels(result), ["pointer.cancel", "lifecycle.guard", "event.accept"])

    def test_chat_submit_still_closes_on_failed_send_in_order(self):
        result = run_case(Case(chat=True, chat_send_succeeds=False), [Key(K.Key_Return)])
        self.assertEqual(labels(result)[-4:], ["chat.send_input", "close_match_chat", "frame.force", "event.accept"])
        self.assertEqual(result[1]["chat"][:2], (False, "draft"))

    def test_economy_repeat_submit_retains_mutating_selection_query_without_action(self):
        result = run_case(Case(economy=True, selected="missing"), [Key(K.Key_Return, repeat=True)])
        self.assertEqual(labels(result)[-4:], ["economy.categories", "economy.offers", "economy.selected", "economy.offers"])
        self.assertEqual(result[1]["economy"][1], "small")
        self.assertFalse(result[2][0])

    def test_control_and_module_binding_both_run_for_same_key(self):
        result = run_case(Case(bindings={"Armor": "X"}), [Key(K.Key_X)])
        self.assertLess(labels(result).index("control.handle"), labels(result).index("registry.toggle"))
        self.assertEqual(result[1]["held"], ["X"])
        self.assertIn(("Armor", False, "X"), result[1]["modules"])

    def test_repeat_map_release_preserves_hold_until_physical_release(self):
        result = run_case(Case(), [Key(K.Key_Tab), Key(K.Key_Tab, repeat=True, phase="release")])
        self.assertTrue(result[1]["views"][-1])
        result = run_case(Case(), [Key(K.Key_Tab), Key(K.Key_Tab, phase="release")])
        self.assertFalse(result[1]["views"][-1])


if __name__ == "__main__":
    unittest.main()
