"""Pure normal-feed sequence admission, provenance, and freshness contracts."""
from pathlib import Path
import math
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.normal_video_timeline import NormalVideoField, NormalVideoTimeline


class NormalVideoTimelineTests(unittest.TestCase):
    def test_import_needs_no_qt_window_or_native_transport(self):
        result = subprocess.run([sys.executable, "-c", """
import sys
import rm_client.ui.polar.normal_video_timeline
assert not any(name.startswith(('PySide6', 'rm_client.backend', 'rm_client.vision'))
               for name in sys.modules)
assert 'rm_client.ui.polar_app' not in sys.modules
"""], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_requested_mode_remains_old_until_adapter_publishes_selection(self):
        owner = NormalVideoTimeline(requested_mode="webcam", frame_source="webcam")
        self.assertTrue(owner.mode_changed("udp video"))
        self.assertEqual(owner.requested_mode, "webcam")
        self.assertTrue(owner.clear_frame())
        self.assertEqual(owner.requested_mode, "webcam")
        owner.select_mode("udp video")
        self.assertFalse(owner.mode_changed("udp video"))
        self.assertEqual(owner.frame_source, "none")

    def test_mode_policy_does_not_silently_normalize_supplied_values(self):
        owner = NormalVideoTimeline(requested_mode="webcam")
        self.assertTrue(owner.mode_changed(" Webcam "))
        owner.select_mode(" Webcam ")
        self.assertEqual(owner.requested_mode, " Webcam ")
        self.assertIsNone(owner.readiness_without_backend(" Webcam "))

    def test_capture_admission_rejects_same_older_and_initial_zero(self):
        owner = NormalVideoTimeline()
        for sequence in (0, -1):
            self.assertFalse(owner.consume_capture(sequence))
        self.assertTrue(owner.consume_capture(12))
        self.assertFalse(owner.consume_capture(12))
        self.assertFalse(owner.consume_capture(11))
        self.assertEqual(owner.capture_sequence, 12)
        self.assertEqual(owner.display_sequence, 0)
        self.assertEqual(owner.frame_source, "none")

    def test_capture_publication_is_separate_from_consumption(self):
        owner = NormalVideoTimeline()
        owner.consume_capture(12)
        self.assertEqual(owner.frame_source, "none")
        owner.publish_capture_frame()
        self.assertEqual(owner.frame_source, "webcam")
        self.assertEqual(owner.display_sequence, 1)

    def test_backend_newness_retains_same_older_future_and_nan_comparisons(self):
        owner = NormalVideoTimeline(backend_admitted_at=12.)
        for frame_at in (11., 12., -1.):
            self.assertFalse(owner.backend_frame_is_new(frame_at))
        self.assertTrue(owner.backend_frame_is_new(13.))
        self.assertTrue(owner.backend_frame_is_new(math.nan))
        owner.backend_admitted_at = math.nan
        self.assertTrue(owner.backend_frame_is_new(1.))
        with self.assertRaises(TypeError):
            owner.backend_frame_is_new(None)

    def test_backend_admission_advances_display_before_publishing_frame_source(self):
        owner = NormalVideoTimeline(frame_source="webcam", capture_sequence=2)
        owner.admit_backend_frame(12.)
        self.assertEqual(owner.backend_admitted_at, 12.)
        self.assertEqual(owner.display_sequence, 1)
        self.assertEqual(owner.capture_sequence, 2)
        self.assertEqual(owner.frame_source, "webcam")
        owner.publish_backend_frame()
        self.assertEqual(owner.frame_source, "backend")
        self.assertEqual(owner.display_sequence, 1)

    def test_backend_freshness_requires_mode_source_positive_time_and_age(self):
        owner = NormalVideoTimeline(backend_admitted_at=12., requested_mode="udp", frame_source="backend")
        self.assertTrue(owner.backend_is_fresh("udp", 14., 2.))
        self.assertFalse(owner.backend_is_fresh("udp", 14.01, 2.))
        self.assertFalse(owner.backend_is_fresh("native", 13., 2.))
        owner.frame_source = "webcam"
        self.assertFalse(owner.backend_is_fresh("udp", 13., 2.))
        owner.frame_source = "backend"
        for timestamp in (0., -1., math.nan):
            owner.backend_admitted_at = timestamp
            self.assertFalse(owner.backend_is_fresh("udp", 13., 2.))

    def test_future_timestamp_stays_fresh_and_nan_clock_stays_not_fresh(self):
        owner = NormalVideoTimeline(backend_admitted_at=20., requested_mode="udp", frame_source="backend")
        self.assertTrue(owner.backend_is_fresh("udp", 10., 2.))
        self.assertFalse(owner.backend_is_fresh("udp", math.nan, 2.))

    def test_empty_and_webcam_readiness_use_published_provenance(self):
        for requested in ("", "webcam"):
            owner = NormalVideoTimeline(requested_mode=requested, frame_source="webcam")
            self.assertTrue(owner.readiness_without_backend(""))
            self.assertTrue(owner.readiness_without_backend("webcam"))
            owner.frame_source = "none"
            self.assertFalse(owner.readiness_without_backend("webcam"))
        owner = NormalVideoTimeline(requested_mode="udp", frame_source="webcam")
        self.assertFalse(owner.readiness_without_backend("webcam"))

    def test_synthetic_readiness_is_false_and_backend_readiness_is_deferred(self):
        owner = NormalVideoTimeline(requested_mode="synthetic", frame_source="webcam")
        self.assertFalse(owner.readiness_without_backend("synthetic"))
        for source in ("udp video", "native 0x0310", "unknown"):
            self.assertIsNone(owner.readiness_without_backend(source))

    def test_clear_retires_frame_provenance_without_resetting_source_counters(self):
        owner = NormalVideoTimeline(7, 14, 12., "udp", "backend")
        self.assertTrue(owner.clear_frame())
        self.assertEqual(owner, NormalVideoTimeline(7, 14, 0., "udp", "none"))
        self.assertFalse(owner.clear_frame())

    def test_clear_can_retire_webcam_without_a_backend_timestamp(self):
        owner = NormalVideoTimeline(frame_source="webcam")
        self.assertTrue(owner.clear_frame())
        self.assertEqual(owner.frame_source, "none")

    def test_clear_preserves_noop_negative_timestamp_and_retires_nan(self):
        owner = NormalVideoTimeline(backend_admitted_at=-1.)
        self.assertFalse(owner.clear_frame())
        self.assertEqual(owner.backend_admitted_at, -1.)
        owner.backend_admitted_at = math.nan
        self.assertTrue(owner.clear_frame())
        self.assertEqual(owner.backend_admitted_at, 0.)

    def test_source_switches_never_mix_capture_and_display_sequence_spaces(self):
        owner = NormalVideoTimeline(requested_mode="webcam")
        self.assertTrue(owner.consume_capture(5))
        owner.publish_capture_frame()
        owner.clear_frame()
        owner.select_mode("udp")
        for frame_at in range(1, 20):
            owner.admit_backend_frame(frame_at)
            owner.publish_backend_frame()
        owner.clear_frame()
        owner.select_mode("webcam")
        self.assertTrue(owner.consume_capture(6))
        owner.publish_capture_frame()
        self.assertEqual(owner.capture_sequence, 6)
        self.assertEqual(owner.display_sequence, 21)

    def test_counter_failure_keeps_the_preceding_backend_timestamp_publication(self):
        owner = NormalVideoTimeline(display_sequence=None)
        with self.assertRaises(TypeError):
            owner.admit_backend_frame(14.)
        self.assertEqual(owner.backend_admitted_at, 14.)
        self.assertEqual(owner.frame_source, "none")

    def test_counter_failure_keeps_the_preceding_capture_source_publication(self):
        owner = NormalVideoTimeline(display_sequence=None)
        with self.assertRaises(TypeError):
            owner.publish_capture_frame()
        self.assertEqual(owner.frame_source, "webcam")

    def test_compatibility_aliases_are_fixed_and_bidirectional(self):
        class Host:
            source = NormalVideoField("frame_source")
            sequence = NormalVideoField("display_sequence")
        host = Host()
        host._normal_video_timeline = NormalVideoTimeline()
        host.source = "backend"
        self.assertEqual(host._normal_video_timeline.frame_source, "backend")
        host._normal_video_timeline.display_sequence = 23
        self.assertEqual(host.sequence, 23)
        with self.assertRaises(ValueError):
            NormalVideoField("frame_image")


if __name__ == "__main__":
    unittest.main()
