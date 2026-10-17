"""Pure confirmation presentation; no Qt, hardware, clock, or transport."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.economy import EconomyOffer
from rm_client.ui.economy_presentation import EconomyConfirmationState

OFFER = EconomyOffer("buy", "ammo", "Buy", "Buy", "Rules", "item", 1, 30, 1, 1, True)


class ConfirmationTests(unittest.TestCase):
    def state(self, **changes):
        values = dict(selected=OFFER, pending=False, hold_key="", hold_started=10., armed_key="", armed_until=20.)
        values.update(changes)
        return EconomyConfirmationState(**values)

    def test_unselected_precedes_pending(self):
        view = self.state(selected=None, pending=True).present(now=10.)
        self.assertEqual((view.text, view.enabled, view.progress, view.held_progress), ("请先选择商品", False, 0., None))

    def test_pending_precedes_disabled_reason(self):
        view = self.state(selected=replace(OFFER, enabled=False, reason="Unavailable"), pending=True).present(now=10.)
        self.assertEqual((view.text, view.enabled, view.action), ("请求处理中…", False, "economy_confirm"))

    def test_disabled_and_status_only_never_offer_confirmation(self):
        for offer in (replace(OFFER, enabled=False), replace(OFFER, status_only=True), replace(OFFER, enabled=False, reason="Unavailable")):
            view = self.state(selected=offer).present(now=10.)
            self.assertFalse(view.enabled)
            self.assertEqual(view.text, offer.reason or "当前不可用")
            self.assertEqual(view.progress, 0.)

    def test_double_confirmation_retains_exact_expiry_boundary(self):
        state = self.state(armed_key=OFFER.key)
        self.assertEqual(state.present(now=19.).text, "再点一次确认（1.0s）")
        self.assertEqual(state.present(now=20.).text, "再点一次确认（0.0s）")
        self.assertEqual(state.present(now=20.0001).text, "确认购买 · 30 金币")
        self.assertEqual(self.state(armed_key="another").present(now=19.).text, "确认购买 · 30 金币")

    def test_hold_fraction_is_clamped_and_clock_is_explicit(self):
        state = self.state(selected=replace(OFFER, confirmation="hold"), hold_key=OFFER.key)
        for now, progress in ((9., 0.), (10., 0.), (10.3, .5), (10.6, 1.), (20., 1.)):
            with self.subTest(now=now):
                view = state.present(now=now)
                self.assertAlmostEqual(view.progress, progress)
                self.assertAlmostEqual(view.held_progress, progress)
                self.assertTrue(view.enabled)
                self.assertEqual(view.action, "economy_confirm_hold")
        self.assertEqual(state.present(now=10.3).text, "确认中 50%")

    def test_unmatched_hold_cannot_reuse_another_products_progress(self):
        view = self.state(selected=replace(OFFER, confirmation="hold"), hold_key="other").present(now=11.)
        self.assertEqual((view.text, view.progress, view.held_progress), ("按住 0.6 秒确认", 0., None))

    def test_availability_change_resets_active_progress_but_preserves_compat_fill(self):
        offer = replace(OFFER, confirmation="hold")
        for changes in ({"pending": True}, {"selected": replace(offer, enabled=False)}, {"selected": replace(offer, status_only=True)}):
            state = self.state(selected=offer, hold_key=OFFER.key)
            view = replace(state, **changes).present(now=10.3)
            self.assertFalse(view.enabled)
            self.assertEqual(view.progress, 0.)
            self.assertAlmostEqual(view.held_progress, .5)

    def test_snapshot_and_result_are_immutable_and_repeatable(self):
        state = self.state()
        result = state.present(now=12.)
        self.assertEqual(result, state.present(now=12.))
        with self.assertRaises(FrozenInstanceError):
            state.pending = True
        with self.assertRaises(FrozenInstanceError):
            result.enabled = False


if __name__ == "__main__":
    unittest.main()
