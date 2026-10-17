"""Confirmation intent decisions: no Qt, transport, application state or clock reads at import."""
from dataclasses import FrozenInstanceError, replace
from itertools import product
import math
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.economy_values import EconomyOffer
from rm_client.ui.economy_interaction import (
    ActivationAction, DOUBLE_CONFIRM_SECONDS, HOLD_SECONDS, HoldCompletion,
    complete_economy_hold, plan_economy_activation, unavailable_offer_reason,
)

OFFER = EconomyOffer("small", "ammo", "Buy", "Buy", "Rules", "item", 1, 30, 1, 1, True)


class EconomyInteractionTests(unittest.TestCase):
    def test_unavailable_and_status_only_rejections_do_not_consult_clock(self):
        def forbidden_clock():
            self.fail("Unavailable input must not read the confirmation clock")
        for enabled, status_only, reason in product((False, True), (False, True), ("", "official rejection")):
            if enabled and not status_only:
                continue
            offer = replace(OFFER, enabled=enabled, status_only=status_only, reason=reason)
            action = plan_economy_activation(offer, read_armed_key=lambda: "small", read_armed_until=lambda: 100., clock=forbidden_clock)
            self.assertIs(action.action, ActivationAction.REJECT)
            self.assertEqual(action.message, reason or "当前操作不可用")
            self.assertEqual(unavailable_offer_reason(offer), reason or "当前操作不可用")
        self.assertIsNone(unavailable_offer_reason(OFFER))

    def test_hold_only_activation_never_becomes_double_confirm(self):
        def forbidden_clock():
            self.fail("Hold prompt must not read the double-confirm clock")
        action = plan_economy_activation(replace(OFFER, confirmation="hold"), read_armed_key=lambda: "small", read_armed_until=lambda: 100., clock=forbidden_clock)
        self.assertIs(action.action, ActivationAction.REJECT)
        self.assertEqual(action.message, "请按住确认按钮 0.6 秒")

    def test_double_confirm_exact_deadline_and_offer_identity(self):
        for key, now, expected in (("small", 10., ActivationAction.SUBMIT), ("small", 10.00001, ActivationAction.ARM),
                                   ("other", 9., ActivationAction.ARM), ("", 9., ActivationAction.ARM)):
            sampled = []
            def clock():
                sampled.append(now)
                return now
            action = plan_economy_activation(OFFER, read_armed_key=lambda: key, read_armed_until=lambda: 10., clock=clock)
            self.assertIs(action.action, expected)
            self.assertEqual(sampled, [now])
            if expected is ActivationAction.ARM:
                self.assertEqual(action.armed_until, now + DOUBLE_CONFIRM_SECONDS)
            with self.assertRaises(FrozenInstanceError):
                action.armed_until = 20.

    def test_confirmation_queries_follow_lazy_original_order(self):
        for enabled, confirmation, key in product((False, True), ("double", "hold"), ("small", "other")):
            observed = []
            def read(name, value):
                observed.append(name)
                return value
            plan_economy_activation(
                replace(OFFER, enabled=enabled, confirmation=confirmation),
                read_armed_key=lambda: read("key", key), read_armed_until=lambda: read("expiry", 101.),
                clock=lambda: read("clock", 100.),
            )
            expected = [] if not enabled or confirmation == "hold" else ["clock", "key"]
            if expected and key == "small":
                expected.append("expiry")
            self.assertEqual(observed, expected)

    def test_clock_failure_propagates_without_constructing_an_intent(self):
        def clock():
            raise OSError("monotonic source failed")
        with self.assertRaisesRegex(OSError, "monotonic source failed"):
            plan_economy_activation(OFFER, read_armed_key=lambda: "small", read_armed_until=lambda: 10., clock=clock)

    def test_hold_exact_boundary_requires_current_offer_and_inside_release(self):
        for exists, inside in product((False, True), (False, True)):
            self.assertIs(complete_economy_hold(offer_exists=exists, inside=inside, elapsed=HOLD_SECONDS - .00001), HoldCompletion.TOO_SHORT)
            expected = HoldCompletion.SUBMIT if exists and inside else HoldCompletion.CANCEL
            self.assertIs(complete_economy_hold(offer_exists=exists, inside=inside, elapsed=HOLD_SECONDS), expected)
            self.assertIs(complete_economy_hold(offer_exists=exists, inside=inside, elapsed=100.), expected)

    def test_nonfinite_elapsed_preserves_original_comparison_semantics(self):
        self.assertIs(complete_economy_hold(offer_exists=True, inside=True, elapsed=math.nan), HoldCompletion.CANCEL)
        self.assertIs(complete_economy_hold(offer_exists=True, inside=True, elapsed=math.inf), HoldCompletion.SUBMIT)
        self.assertIs(complete_economy_hold(offer_exists=True, inside=True, elapsed=-math.inf), HoldCompletion.TOO_SHORT)

    def test_import_and_decisions_have_no_qt_model_or_backend_dependency(self):
        code = '''
import sys
from rm_client.ui.economy_interaction import HoldCompletion, complete_economy_hold
assert complete_economy_hold(offer_exists=True, inside=False, elapsed=1.) is HoldCompletion.CANCEL
assert not any(name.startswith(("PySide6", "rm_client.model", "rm_client.backend", "rm_client.ui.polar")) for name in sys.modules)
assert "rm_client.economy" not in sys.modules
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
