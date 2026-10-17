"""Original economy transaction/selection effects, including interrupted prefixes."""
from dataclasses import replace
import hashlib
from itertools import product
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from PySide6.QtCore import QPointF, QRectF
from economy_interaction_fixture import BASELINE_PATH, Case, OFFER, REQUEST, run

FIXTURE_SHA256 = "8f6bf8a7de0bf1240df5e6672a25f4d2c09a275f0a9d4784f01fdc9457d942b6"


def scenarios():
    for selected, index, category, offers in product(("small", "large", "stale"), (0, 7), ("ammo", "missing"),
                                                   ((OFFER, replace(OFFER, key="large")), ())):
        yield Case(selected=selected, selected_index=index, category=category, offers=offers), "economy_selected_offer", ()
    for key, selected in product(("small", "large", "stale"), ("small", "large", "stale")):
        yield Case(selected=selected, armed_key="small"), "select_economy_offer", (key,)
    for enabled, status_only, pending, error in product((False, True), (False, True), (False, True), ("", "ValueError", "RuntimeError")):
        offer = replace(OFFER, enabled=enabled, status_only=status_only, reason="blocked" if not enabled else "")
        yield Case(pending=pending, submit_error=error, armed_key="small", hold_key="small"), "submit_economy_offer", (offer,)
    for enabled, status_only, confirmation, arm, now in product((False, True), (False, True), ("double", "hold", "none"),
                                                               ("", "small", "other"), (100., 101., 101.0001)):
        offer = replace(OFFER, enabled=enabled, status_only=status_only, confirmation=confirmation)
        yield Case(armed_key=arm, now=now), "activate_economy_offer", (offer,)
    for enabled, status_only, rect in product((False, True), (False, True), (None, QRectF(1., 2., 3., 4.))):
        yield Case(), "begin_economy_hold", (replace(OFFER, enabled=enabled, status_only=status_only), rect)
    for key, now, point, offers in product(("", "small", "stale"), (99.3, 99.999, 100., 101.),
                                          (None, QPointF(5., 5.), QPointF(30., 30.)), ((OFFER,), ())):
        yield Case(hold_key=key, now=now, offers=offers), "finish_economy_hold", (point,)
    for mock, scenario, token, topic, accepted in product((False, True), ("OfficialEvent", "TeamBench"), (None, "capability"),
                                                        ("AirSupportCommand", "CommonCommand", "future"), (False, True)):
        yield Case(scenario=scenario, token=token, accepted=accepted), "send_economy_request", (replace(REQUEST, mock=mock, topic=topic),)
    for scenario, session in product(("OfficialEvent", "TeamBench"), (False, True)):
        yield Case(scenario=scenario, session=session), "start_economy_qualification_test", ()


class EconomyInteractionCharacterization(unittest.TestCase):
    def test_reference_integrity(self):
        self.assertEqual(hashlib.sha256(BASELINE_PATH.read_bytes()).hexdigest(), FIXTURE_SHA256)

    def test_complete_effects_and_partial_state_match_original(self):
        for case, method, args in scenarios():
            with self.subTest(case=case, method=method, args=args):
                expected = run(case, method, args, frozen=True)
                self.assertEqual(run(case, method, args), expected)

    def test_every_observable_failure_prefix_matches_original(self):
        for case, method, args in scenarios():
            expected = run(case, method, args, frozen=True)
            calls = sum(item[0] != "write" for item in expected[0])
            for fail_at in range(1, calls + 1):
                with self.subTest(case=case, method=method, fail_at=fail_at):
                    self.assertEqual(run(case, method, args, fail_at=fail_at),
                                     run(case, method, args, frozen=True, fail_at=fail_at))

    def test_legacy_confirmation_field_reads_keep_short_circuit_and_failure_order(self):
        # Production window fields are initialized scalars. This also preserves
        # compatibility with legacy hosts that observe or interrupt reads.
        for enabled, status_only, confirmation, key, now in product(
            (False, True), (False, True), ("double", "hold"), ("small", "other"), (100., 102.),
        ):
            case = Case(armed_key=key, now=now)
            offer = replace(OFFER, enabled=enabled, status_only=status_only, confirmation=confirmation)
            args = (case, "activate_economy_offer", (offer,))
            expected = run(*args, frozen=True, observe_confirmation_reads=True)
            self.assertEqual(run(*args, observe_confirmation_reads=True), expected)
            count = sum(item[0] != "write" for item in expected[0])
            for fail_at in range(1, count + 1):
                with self.subTest(enabled=enabled, status_only=status_only, confirmation=confirmation,
                                  key=key, now=now, fail_at=fail_at):
                    self.assertEqual(run(*args, fail_at=fail_at, observe_confirmation_reads=True),
                                     run(*args, frozen=True, fail_at=fail_at, observe_confirmation_reads=True))

    def test_frozen_reference_rejects_confirmation_budget_mutations(self):
        held = Case(hold_key="small", hold_started=0., now=.595)
        armed = Case(armed_key="", now=100.)
        for constant, changed, case, method, args in (
            ("HOLD_SECONDS", .59, held, "finish_economy_hold", (None,)),
            ("DOUBLE_CONFIRM_SECONDS", 3.8, armed, "activate_economy_offer", (OFFER,)),
        ):
            with self.subTest(constant=constant):
                original = run(case, method, args, frozen=True)
                self.assertEqual(run(case, method, args), original)
                with patch("rm_client.ui.economy_interaction." + constant, changed):
                    self.assertNotEqual(run(case, method, args), original)

    def test_qualification_is_consumed_before_publication_and_relocked_on_failure(self):
        case = Case(token="one shot")
        trace, state, _, error = run(case, "send_economy_request", (REQUEST,), fail_at=1)
        self.assertEqual([item[0] for item in trace], ["write", "publish.common", "control.lock"])
        self.assertIsNone(state[0]["_economy_qualification_token"])
        self.assertEqual(error, ("RuntimeError", "injected publish.common"))

    def test_short_or_outside_hold_cannot_submit_and_release_clears_capture(self):
        for case, point in ((Case(hold_key="small", now=99.999), None),
                            (Case(hold_key="small", now=100.), QPointF(21., 21.))):
            trace, state, _, error = run(case, "finish_economy_hold", (point,))
            self.assertIsNone(error)
            self.assertFalse(any(item[0] == "submit" for item in trace))
            self.assertEqual(state[0]["economy_hold_key"], "")
            self.assertEqual(state[0]["economy_hold_rect"], (0., 0., 0., 0.))


if __name__ == "__main__":
    unittest.main()
