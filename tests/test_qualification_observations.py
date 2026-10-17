"""Command vocabulary and measured qualification predicates require no Qt."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.configuration.commands import (
    ConfigCommandKind, QualificationCommand, resolve_config_command, resolve_qualification_command,
)
from rm_client.qualification_observations import (
    economy_evidence, estop_evidence, firmware_zero_evidence, hero_evidence,
    recovery_evidence, soak_evidence, video_evidence,
)


class CommandSelectionTests(unittest.TestCase):
    def test_qualification_vocabulary_resolves_exactly_to_its_existing_kind(self):
        self.assertEqual(len(QualificationCommand), 23)
        for command in QualificationCommand:
            selected = resolve_config_command(command.value)
            self.assertIs(selected.kind, ConfigCommandKind.QUALIFICATION)
            self.assertEqual(selected.name, command.value)
            self.assertIs(resolve_qualification_command(command.value), command)
            for candidate in (command.value.lower(), " " + command.value, command.value + " "):
                self.assertIsNone(resolve_config_command(candidate))
                self.assertIsNone(resolve_qualification_command(candidate))

    def test_sound_slots_are_explicitly_closed_and_do_not_accept_translated_labels(self):
        for slot in ("enable", "disable", "invalid"):
            selected = resolve_config_command(f"Import {slot.title()} Sound")
            self.assertEqual(selected.sound_slot, slot)
            self.assertIs(selected.kind, ConfigCommandKind.SOUND_IMPORT)
            selected = resolve_config_command(f"Preview {slot.title()}")
            self.assertEqual(selected.sound_slot, slot)
            self.assertIs(selected.kind, ConfigCommandKind.SOUND_PREVIEW)
        for candidate in ("Import ARM Sound", "Preview Fire", "现场资格检查", "解锁比赛控制", ""):
            self.assertIsNone(resolve_config_command(candidate))

    def test_editor_precedence_is_data_not_a_side_effect(self):
        for name in ("Open Keybind Manager", "Open Crosshair Editor", "Import Crosshair Code",
                     "Import Crosshair File", "Crosshair Import Details"):
            self.assertIs(resolve_config_command(name).kind, ConfigCommandKind.EDITOR)
        self.assertIs(resolve_config_command("Release E-Stop").kind, ConfigCommandKind.SAFETY)
        self.assertIs(resolve_config_command("Arm Angle Forward").kind, ConfigCommandKind.ANGLES)


class QualificationObservationTests(unittest.TestCase):
    def test_video_uses_both_sixty_second_limits_and_new_frames(self):
        values = dict(source="udp", session_exists=True, session_age=60.0, decoded_frames=11,
                      baseline_frames=10, fresh=True, continuous_seconds=60.0)
        result = video_evidence(**values)
        self.assertTrue(result.passed)
        self.assertEqual(result.key, "codec_field_test")
        self.assertEqual(result.measured["frames_since_baseline"], 1)
        for change in ({"session_exists": False}, {"session_age": 59.999}, {"continuous_seconds": 59.999},
                       {"fresh": False}, {"decoded_frames": 10}, {"source": "camera"}):
            with self.subTest(change=change):
                self.assertFalse(video_evidence(**(values | change)).passed)
        self.assertTrue(video_evidence(**(values | {"source": "native_0310"})).passed)
        self.assertEqual(video_evidence(**(values | {"decoded_frames": 0})).measured["frames_since_baseline"], 0)

    def test_estop_requires_a_new_event_and_successful_zero_publication(self):
        self.assertTrue(estop_evidence(estop_at=11, baseline_estop_at=10, zero_published=True).passed)
        self.assertFalse(estop_evidence(estop_at=10, baseline_estop_at=10, zero_published=True).passed)
        self.assertFalse(estop_evidence(estop_at=11, baseline_estop_at=10, zero_published=False).passed)

    def test_firmware_zero_requires_observation_in_new_epoch_with_inclusive_limit(self):
        for milliseconds, passed in ((0.0, False), (0.1, True), (500.0, True), (500.01, False)):
            result = firmware_zero_evidence(estop_at=11, baseline_estop_at=10, observed_ms=milliseconds)
            self.assertEqual(result.passed, passed)
            self.assertEqual(result.measured, {"zero_observed_ms": milliseconds})
            self.assertEqual(result.threshold, {"maximum_ms": 500, "physical_observer_required": True})
        self.assertFalse(firmware_zero_evidence(estop_at=10, baseline_estop_at=10, observed_ms=10).passed)

    def test_recovery_requires_all_new_observations_and_current_locked_state(self):
        values = dict(reconnect_count=2, baseline_reconnect_count=1, disconnect_lock_at=11,
                      reconnected_locked_at=12, baseline_at=10, control_locked=True)
        self.assertTrue(recovery_evidence(**values).passed)
        for change in ({"reconnect_count": 1}, {"disconnect_lock_at": 10},
                       {"reconnected_locked_at": 10}, {"control_locked": False}):
            self.assertFalse(recovery_evidence(**(values | change)).passed)

    def test_hero_requires_two_new_revisions_fresh_status_and_stowed_mode(self):
        values = dict(revision=4, baseline_revision=2, observed_at=11, baseline_at=10, deploy_mode=0)
        self.assertTrue(hero_evidence(**values).passed)
        for change in ({"revision": 3}, {"observed_at": 10}, {"deploy_mode": 1}):
            self.assertFalse(hero_evidence(**(values | change)).passed)

    def test_economy_requires_successful_real_request_real_transport_and_new_status(self):
        values = dict(succeeded=True, request_is_mock=False, request_at=100, baseline_at=100,
                      revision=3, baseline_revision=2, transport_is_mock=False)
        self.assertTrue(economy_evidence(**values).passed)
        for change in ({"succeeded": False}, {"request_is_mock": True}, {"request_at": 99.99},
                       {"revision": 2}, {"transport_is_mock": True}):
            self.assertFalse(economy_evidence(**(values | change)).passed)

    def test_soak_requires_observed_start_and_longer_of_match_or_seven_minutes(self):
        self.assertFalse(soak_evidence(started_at=0, now=10000, match_duration=300).passed)
        self.assertFalse(soak_evidence(started_at=10, now=429.99, match_duration=300).passed)
        self.assertTrue(soak_evidence(started_at=10, now=430, match_duration=300).passed)
        self.assertFalse(soak_evidence(started_at=10, now=430, match_duration=600).passed)
        result = soak_evidence(started_at=10, now=610, match_duration=600)
        self.assertTrue(result.passed)
        self.assertEqual(result.threshold, {"minimum_seconds": 600})
        self.assertEqual(result.measured, {"seconds": 600})


if __name__ == "__main__":
    unittest.main()
