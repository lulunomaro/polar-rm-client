"""Pure native continuity policy: exhaustive byte-ring and trust boundaries."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.native_stream_policy import (
    NativeVideoSequenceDecision, assess_native_video_sequence, localization_invalidated,
)


class NativeStreamPolicyTests(unittest.TestCase):
    def test_every_byte_sequence_pair_matches_forward_distance_reference(self):
        # Independent expression in terms of advance from the previous packet,
        # rather than the production distance from the next expected packet.
        for previous in range(256):
            for current in range(256):
                advance = (current - previous) % 256
                accepted = 1 <= advance <= 128
                expected_missing = advance - 1 if accepted else 0
                decision = assess_native_video_sequence(
                    sequence=current, modulus=256, reset=False, previous_sequence=previous,
                )
                self.assertEqual(decision, NativeVideoSequenceDecision(
                    current, False, expected_missing, accepted,
                ), (previous, current))

    def test_reset_and_first_chunk_do_not_consult_previous_arithmetic(self):
        for sequence in (0, 1, 127, 255):
            self.assertEqual(assess_native_video_sequence(
                sequence=sequence, modulus=256, reset=True, previous_sequence="invalid",
            ), NativeVideoSequenceDecision(sequence, True))
            self.assertEqual(assess_native_video_sequence(
                sequence=sequence, modulus=256, reset=False, previous_sequence=None,
            ), NativeVideoSequenceDecision(sequence, False))

    def test_normalization_and_odd_modulus_keep_existing_boundaries(self):
        self.assertEqual(assess_native_video_sequence(
            sequence="-1", modulus="256", reset=False, previous_sequence=None,
        ).sequence, 255)
        for modulus in (0, -1, 1):
            self.assertEqual(assess_native_video_sequence(
                sequence=99, modulus=modulus, reset=False, previous_sequence=7,
            ), NativeVideoSequenceDecision(0, False))
        self.assertEqual(assess_native_video_sequence(
            sequence=2, modulus=5, reset=False, previous_sequence=0,
        ), NativeVideoSequenceDecision(2, False, dropped_chunks=1))
        self.assertEqual(assess_native_video_sequence(
            sequence=3, modulus=5, reset=False, previous_sequence=0,
        ), NativeVideoSequenceDecision(3, False, accept_payload=False))

    def test_decisions_are_immutable_and_cannot_hold_a_host(self):
        decision = assess_native_video_sequence(
            sequence=1, modulus=256, reset=False, previous_sequence=0,
        )
        with self.assertRaises(FrozenInstanceError):
            decision.sequence = 2
        self.assertEqual(decision.__slots__, ("sequence", "reset", "dropped_chunks", "accept_payload"))

    def test_telemetry_backward_jump_boundary_and_natural_wrap(self):
        for previous, current, invalidated in (
            (0, 0, False), (10, 10, False), (10, 11, False), (10, 9, True),
            (0xFFFFFFFF, 0, False), (0x80000000, 0, False), (0x7FFFFFFF, 0, True),
        ):
            with self.subTest(previous=previous, current=current):
                self.assertEqual(localization_invalidated(previous, current, "READY"), invalidated)

    def test_status_markers_revoke_trust_case_insensitively_without_broadening_matches(self):
        for marker in ("localization lost", "localisation lost", "relocalizing",
                       "relocalising", "pose reset", "map reset"):
            self.assertTrue(localization_invalidated(1, 2, "prefix " + marker + " suffix"))
        for status in ("READY", "localization found", "pose restored", "reset", "map ready"):
            self.assertFalse(localization_invalidated(1, 2, status))

    def test_import_is_independent_of_qt_state_decoder_and_wire_parser(self):
        program = '''
import importlib.abc
import sys
class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("PySide6", "rm_client.model", "rm_client.backend.manager",
                   "rm_client.backend.video_backend", "rm_client.native_viewer_telemetry")
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("unexpected runtime import: " + fullname)
sys.meta_path.insert(0, BlockRuntime())
from rm_client.backend.native_stream_policy import assess_native_video_sequence
assert assess_native_video_sequence(sequence=0, modulus=256, reset=False,
                                    previous_sequence=255).accept_payload
'''
        result = subprocess.run([sys.executable, "-c", program], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
