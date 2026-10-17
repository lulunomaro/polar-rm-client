"""Isolated integration tests: fake host/transport only, no application startup."""
from __future__ import annotations
import ast
import logging
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from rm_client.model import build_default_registry
from rm_client.top_match_bar import LifeState

# Execute only these audited queue/observer methods against an in-memory fake;
# importing BackendManager would load optional serial/video runtime dependencies.
SOURCE = Path(__file__).resolve().parents[1] / "rm_client/backend/manager.py"
TREE = ast.parse(SOURCE.read_text())
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "BackendManager")
NAMES = {"apply_updates", "_notify_referee_view"}
FUNCS = [n for n in CLASS.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
MODULE = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + FUNCS, type_ignores=[])
NAMESPACE = {"_LOG": logging.getLogger("test.top_match_bar")}
exec(compile(ast.fix_missing_locations(MODULE), str(SOURCE), "exec"), NAMESPACE)


class FakeBackend:
    apply_updates = NAMESPACE["apply_updates"]
    _backend_transition_blocks_work = lambda self: False
    _notify_referee_view = NAMESPACE["_notify_referee_view"]

    def __init__(self, observer=None):
        self.referee_view_observer = observer
        self._referee_view_error = ""
        self._apply_error_count = self._official_state_updates_count = 0
        self._last_apply_error = ""
        self.queue = [("/GameStatus", "GameStatus", {"stage_countdown_sec": 85}, 10.0)]
        self.mqtt = NS(drain_decoded=self.drain)
        self.order = []

    def drain(self):
        result, self.queue = self.queue, []
        self.order.append("drain")
        return result

    def _begin_official_state(self, state, timestamp):
        self.order.append("begin")

    def _apply_one(self, state, name, data):
        state.applied = True
        self.order.append("apply")

    def stats(self):
        return {"test": True}

    def sync_frontend_state(self, state):
        self.order.append("sync")


class QueueIntegrationTests(unittest.TestCase):
    def test_default_module_is_enabled_and_has_no_binding(self):
        module = build_default_registry().get("TopMatchBar")
        self.assertIsNotNone(module)
        self.assertTrue(module.enabled)
        self.assertEqual(module.keybind, "None")
        self.assertFalse(module.get_setting("Source Motion Preview").value)
        self.assertEqual(module.get_setting("Score Metric").value, "Referee Score")
        self.assertTrue(module.get_setting("Show Health").value)
        self.assertTrue(module.get_setting("BF6 Progress").value)
        self.assertEqual(module.get_setting("Match Scope").value, "RMUC Only")

    def test_observer_after_authoritative_apply_without_second_drain(self):
        state = NS(backend_stats={}, applied=False)
        backend = FakeBackend()
        def observe(name, data, stamp):
            self.assertTrue(state.applied)
            backend.order.append("observe")
            self.assertEqual(stamp, 10.0)
        backend.referee_view_observer = observe
        self.assertEqual(backend.apply_updates(state), 1)
        self.assertEqual(backend.order, ["drain", "begin", "apply", "observe", "sync"])
        self.assertEqual(backend._apply_error_count, 0)
        self.assertEqual(backend.apply_updates(state), 0)

    def test_observer_failure_cannot_turn_valid_packet_into_apply_error(self):
        def fail(*args):
            raise RuntimeError("display only")
        backend = FakeBackend(fail)
        state = NS(backend_stats={})
        self.assertEqual(backend.apply_updates(state), 1)
        self.assertEqual(backend._official_state_updates_count, 1)
        self.assertEqual(backend._apply_error_count, 0)
        self.assertTrue(state.applied)
        self.assertIn("display only", backend._referee_view_error)
        self.assertEqual(backend.order[-1], "sync")

    def test_observer_absent_keeps_existing_behavior(self):
        backend = FakeBackend()
        self.assertEqual(backend.apply_updates(NS(backend_stats={})), 1)
        self.assertEqual(backend.order, ["drain", "begin", "apply", "sync"])

    def test_observer_error_logging_failure_preserves_authoritative_count(self):
        def fail(*args):
            raise RuntimeError("display only")
        backend = FakeBackend(fail)
        with patch.object(NAMESPACE["_LOG"], "warning", side_effect=RuntimeError("logger unavailable")):
            self.assertEqual(backend.apply_updates(NS(backend_stats={})), 1)
        self.assertEqual(backend._official_state_updates_count, 1)
        self.assertEqual(backend._apply_error_count, 0)
        self.assertEqual(backend.order[-1], "sync")

    def test_unprintable_observer_error_preserves_authoritative_count(self):
        class UnprintableError(Exception):
            def __str__(self):
                raise ValueError("cannot stringify")
        def fail(*args):
            raise UnprintableError()
        backend = FakeBackend(fail)
        self.assertEqual(backend.apply_updates(NS(backend_stats={})), 1)
        self.assertEqual(backend._apply_error_count, 0)
        self.assertEqual(backend._referee_view_error, "UnprintableError")

    def test_failed_authoritative_packet_is_not_observed(self):
        seen = []
        backend = FakeBackend(lambda *args: seen.append(args))
        def reject(*args):
            raise ValueError("invalid packet")
        backend._apply_one = reject
        self.assertEqual(backend.apply_updates(NS(backend_stats={})), 0)
        self.assertEqual(backend._official_state_updates_count, 0)
        self.assertEqual(backend._apply_error_count, 1)
        self.assertFalse(seen)

    def test_only_current_queue_data_is_shared(self):
        backend = FakeBackend()
        data = {"a": 1}
        def observe(name, incoming, stamp):
            incoming["a"] = 9
        backend.referee_view_observer = observe
        backend._notify_referee_view("sample", data, 10)
        self.assertEqual(data, {"a": 1})


# Reuse the explicit binding bootstrap in the native rendering test, not app.py.
from top_match_bar_qt_test import QT_AVAILABLE
if QT_AVAILABLE:
    from top_match_bar_qt_test import QtCore, QtGui, QtWidgets
    from rm_client.ui.top_match_bar_controller import TopMatchBarController


@unittest.skipUnless(QT_AVAILABLE, "PySide6 absent; isolated Qt controller not verified on production binding")
class ControllerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.registry = build_default_registry()
        self.registry.get("TopMatchBar").enabled = True
        self.registry.get("TopMatchBar").get_setting("Match Scope").value = "All Match Modes"
        self.state = NS(match_mode="RMUL3v3", alliance_color="Blue", robot_id=103)
        self.mqtt = NS(host="127.0.0.1", port=3333, disconnected_at=0.0, connected=True,
                       receive_generation=0)
        self.owner = NS(
            registry=self.registry, state=self.state,
            backend=NS(mqtt=self.mqtt, scenario_key="OfficialEvent", referee=NS(profile_id="rm2026-v1.3.1")),
            home_visible=False, hero_deploy_ui=NS(active=False), overlay_coordinator=NS(modal=None),
            dynamic_island_state=NS(y=12.0, height=42.0),
            minecraft_hud_active=lambda: False, menu_overlay_active=lambda: False,
            tactical_map_rect=lambda w, h: QtCore.QRectF(12, 58, 170, 118),
            setting_bool=lambda mod, key, default=False: bool(mod.get_setting(key).value) if mod and mod.get_setting(key) else default,
            setting_mode=lambda mod, key, default: str(mod.get_setting(key).value) if mod and mod.get_setting(key) else default)
        self.controller = TopMatchBarController(self.owner)

    def paint(self, now=10):
        image = QtGui.QImage(1280, 720, QtGui.QImage.Format_ARGB32_Premultiplied)
        image.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(image)
        self.controller.paint(p, 1280, 720, now)
        p.end()
        return image

    def test_real_decoded_observation_reaches_frame_without_state_mutation(self):
        before = dict(vars(self.state))
        self.controller.observe_referee("GlobalUnitStatus", {"robot_health": [50, 50, 0, 50, 50], "__present__": (11,)}, 10)
        frame = self.controller.feed.snapshot(10)
        self.assertEqual(len(frame.left.cards), 3)
        self.assertEqual(frame.left.cards[1].life, LifeState.DEAD)
        self.assertEqual(frame.right.alive_text, "?")
        self.assertEqual(vars(self.state), before)

    def test_disconnect_discards_existing_observations(self):
        self.controller.observe_referee("GameStatus", {"stage_countdown_sec": 85}, 10)
        self.mqtt.connected = False
        self.mqtt.disconnected_at = 10.1
        self.paint(10.1)
        self.assertIsNone(self.controller.feed.snapshot(10.1).countdown)

    def test_profile_change_cannot_reuse_rm2026_fields(self):
        self.owner.backend.referee.profile_id = "rm2026-v1.1.0-student-readonly"
        self.controller.observe_referee("GameStatus", {"stage_countdown_sec": 85}, 10)
        self.assertIsNone(self.controller.feed.snapshot(10).countdown)

    def test_fast_reconnect_between_ui_frames_discards_old_epoch(self):
        self.registry.get("TopMatchBar").get_setting("Score Metric").value = "Referee Score"
        self.controller.observe_referee("GameStatus", {"stage_countdown_sec": 85, "blue_score": 3}, 10)
        self.controller.observe_referee("RobotDynamicStatus", {"current_health": 0}, 10)
        self.assertTrue(self.controller.feed.record_confirmed_elimination(
            event_id="old-connection-event", killer_id=103, victim_id=3,
            confirmed=True, source="referee"))
        old_feed = self.controller.feed
        # Disconnect and reconnect both finish before paint/observe runs again.
        # connected and disconnected_at match their previous sampled values.
        self.mqtt.connected = False
        self.mqtt.disconnected_at = 10.1
        self.mqtt.receive_generation += 1
        self.mqtt.connected = True
        self.mqtt.disconnected_at = 0.0
        self.paint(10.2)
        self.assertIsNot(self.controller.feed, old_feed)
        frame = self.controller.feed.snapshot(10.2)
        self.assertIsNone(frame.countdown)
        self.assertIsNone(frame.left.score)
        self.assertTrue(all(card.life == LifeState.UNKNOWN and card.kills is None
                            for team in (frame.left, frame.right) for card in team.cards))

    def test_first_post_reconnect_packet_cannot_revive_other_old_fields(self):
        self.controller.observe_referee("RobotDynamicStatus", {"current_health": 0}, 10)
        self.mqtt.receive_generation += 1
        self.controller.observe_referee("GameStatus", {"stage_countdown_sec": 84}, 10.2)
        frame = self.controller.feed.snapshot(10.2)
        self.assertEqual(frame.countdown, 84)
        self.assertTrue(all(card.life == LifeState.UNKNOWN for card in frame.left.cards))

    def test_module_toggle_waits_for_new_observation(self):
        self.controller.observe_referee("GameStatus", {"stage_countdown_sec": 85}, 10)
        module = self.registry.get("TopMatchBar")
        module.enabled = False
        self.paint()
        self.assertIsNone(self.controller.feed)
        module.enabled = True
        self.paint()
        self.assertIsNone(self.controller.feed.snapshot(10).countdown)

    def test_scene_and_modal_visibility(self):
        self.assertTrue(self.controller.visible())
        self.owner.home_visible = True
        self.assertFalse(self.controller.visible())
        self.owner.home_visible = False
        self.owner.hero_deploy_ui.active = True
        self.assertFalse(self.controller.visible())
        self.owner.hero_deploy_ui.active = False
        self.owner.overlay_coordinator.modal = "economy"
        self.assertFalse(self.controller.visible())
        self.owner.overlay_coordinator.modal = None
        self.owner.minecraft_hud_active = lambda: True
        self.assertTrue(self.controller.visible())
        self.assertTrue(self.controller.owns_progress())

    def test_layout_respects_map_and_dynamic_island(self):
        self.paint()
        layout = self.controller.last_layout
        self.assertIsNotNone(layout)
        self.assertGreaterEqual(layout.bounds.x, 192)
        self.assertGreaterEqual(layout.bounds.y, 62)
        self.assertLessEqual(layout.bounds.right, 1268)

    def test_default_scope_comes_from_current_mode_profiles(self):
        self.paint()
        self.assertEqual(len(self.controller.feed.rosters["Red"]), 3)
        self.state.match_mode = "RMUC7v7"
        self.paint()
        self.assertEqual(len(self.controller.feed.rosters["Red"]), 8)
        frame = self.controller.feed.snapshot(10)
        self.assertEqual([c.robot_id for c in frame.right.cards], [1, 2, 3, 4, 6, 7])
        self.assertEqual([c.robot_id for c in frame.right.systems], [8, 9])
        self.registry.get("TopMatchBar").get_setting("Roster Scope").value = "All Roles"
        self.paint()
        self.assertEqual(len(self.controller.feed.rosters["Red"]), 8)

    def test_explicit_uneven_roster_overrides_profile_candidates(self):
        self.controller.configure_rosters([1, 3], [101, 102, 103, 107], label="MATCH ROSTER")
        self.paint()
        self.assertEqual(self.controller.feed.rosters["Red"], (1, 3))
        self.assertEqual(len(self.controller.feed.rosters["Blue"]), 4)

    def test_repeated_open_close_does_not_grow_render_records(self):
        for _ in range(4):
            self.paint()
            self.owner.menu_overlay_active = lambda: True
            self.paint()
            self.assertFalse(self.controller.renderer._transitions)
            self.owner.menu_overlay_active = lambda: False
        self.paint()
        self.assertLessEqual(len(self.controller.renderer._transitions), 6)

    def test_no_persistent_timer_widget_or_input_hooks(self):
        source = Path(__file__).resolve().parents[1] / "rm_client/ui/top_match_bar_controller.py"
        text = source.read_text()
        self.assertNotIn("QTimer", text)
        self.assertNotIn("QWidget", text)
        self.assertNotIn("mousePressEvent", text)
        self.assertNotIn("keyPressEvent", text)

    def test_render_error_and_broken_logger_do_not_escape_hud(self):
        with patch.object(self.controller.renderer, "paint", side_effect=RuntimeError("display failed")), \
                patch("rm_client.ui.top_match_bar_controller._LOG.warning", side_effect=RuntimeError("logger failed")):
            self.paint()
        self.assertIsNone(self.controller.last_layout)
        self.assertIn("display failed", self.controller._last_error)
        self.assertFalse(self.controller.renderer._transitions)

    def test_unprintable_render_error_does_not_escape_hud(self):
        class UnprintableError(Exception):
            def __str__(self):
                raise ValueError("cannot stringify")
        with patch.object(self.controller.renderer, "paint", side_effect=UnprintableError()):
            self.paint()
        self.assertEqual(self.controller._last_error, "UnprintableError")

    def test_visibility_error_does_not_escape_hud(self):
        with patch.object(self.owner, "menu_overlay_active", side_effect=RuntimeError("visibility failed")):
            self.paint()
        self.assertIsNone(self.controller.last_layout)
        self.assertIn("visibility failed", self.controller._last_error)


if __name__ == "__main__":
    unittest.main()
