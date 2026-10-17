"""Actual cached-frame orchestration over real Qt raster and inert GL edges.

This records resource calls and externally published cache/interaction state;
it does not implement a second renderer or claim native GPU pixel coverage.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import importlib.util
from pathlib import Path
from types import MethodType, SimpleNamespace as NS
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor

from rm_client.ui import gpu_frame_compositor as compositor
from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.polar.primitives import Hotspot
from rm_client.ui.polar_app import EconomyScenePhase, PolarRMWindow
import test_framebuffer_generation as framebuffer


ROOT = Path(__file__).resolve().parents[1]
reference = importlib.util.spec_from_file_location(
    "cached_frame_baseline", ROOT / "tests/fixtures/cached_frame_baseline.py")
baseline = importlib.util.module_from_spec(reference)
reference.loader.exec_module(baseline)


def canonical(value):
    if isinstance(value, QRectF):
        return tuple(value.getRect())
    if isinstance(value, QColor):
        return value.getRgb()
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return tuple((key, canonical(item)) for key, item in sorted(value.items()))
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(canonical(item) for item in value))
    if isinstance(value, (tuple, list)):
        return tuple(canonical(item) for item in value)
    if is_dataclass(value):
        return canonical(asdict(value))
    return type(value).__name__


class RecordedFrame:
    def __init__(self, scenario="hud", *, original=False, fail_at=None, rejection=False):
        self.case = framebuffer.FramebufferGenerationTests()
        self.case.setUp()
        self.window = w = self.case.w
        self.compositor = c = self.case.c
        self.events = []
        self.fail_at = fail_at
        self.rejection = rejection
        self.failed = False
        self.original = original
        self.scenario = scenario
        method = (baseline.CachedFrameBaseline._paint_gpu_frame_cached
                  if original else PolarRMWindow._paint_gpu_frame_cached)
        w._paint_gpu_frame_cached = MethodType(method, w)
        w._startup_static_page_key = None
        w._gpu_frame_recording = False
        w.hotspots = [Hotspot(QRectF(1, 2, 3, 4), "existing")]
        w.gui_scroll_viewports = []
        w._hotspot_clip = None
        w.startup_step = "scenario"
        w.startup_transition_from = None
        w.startup_transition_to = None
        w.startup_transition_started = 0.
        w.anim_duration = lambda seconds: seconds
        w.scenario_manager = NS(current=NS(key="training"))
        w.match_mode_manager = NS(current=NS(key="simulation"))
        w.setting_mode = lambda module, name, default: default
        w.active_clickgui_mode = lambda: "JelloSigma"
        w.gui_timeline.blur_radius = lambda: 13.
        w.paint_gpu_startup_interaction_layer = self.record("startup.interaction", True)
        w._finalize_economy_restore = self.record("restore.commit")
        w.build_gpu_startup_launcher = self.builder("startup.layout")
        w.build_gpu_economy_workspace = self.builder("economy.layout")
        w.build_gpu_clickgui = self.builder("clickgui.layout")
        w.clickgui_gpu.prepare_frame = self.record("atlas.prepare")
        w.clickgui_gpu.render = self.record("commands.render", True)
        w.paint_gpu_background = self.record("background", True)
        w.paint_gpu_control_backdrop = self.record("control", False)
        w.paint_gpu_vision_layer = self.record("vision", True)
        w.paint_overlay = self.record("hud.static")
        w.paint_hud_motion_overlay = self.record("hud.motion")
        w.paint_clickgui_overlay = self.paint_clickgui
        w.paint_priority_interaction_layer = self.record("priority", True)
        w.paint_gpu_target_hud_layer = self.record("target", True)
        w.frame_scheduler.force = self.record("schedule.force")
        for name in ("begin_gpu_hud_command_collection", "begin_gpu_island_icon_collection",
                     "begin_gpu_quick_action_wheel_collection", "flush_gpu_hud_commands",
                     "flush_gpu_island_icons", "flush_gpu_quick_action_wheel"):
            setattr(w, name, self.record(name))
        for name in ("ensure_size", "begin_frame", "clear_target_layer", "clear_vision_layer",
                     "clear_scene_layers", "set_backdrop_treatment", "clear_static_glass",
                     "begin_static_glass", "end_static_glass", "push_glass_owner",
                     "pop_glass_owner", "paint_device", "clear_current", "release_layer",
                     "prepare_blur", "render_glass", "prepare_treated_backdrop", "present",
                     "invalidate_transient_layers"):
            setattr(c, name, self.wrap(name, getattr(c, name)))
        self.configure(scenario)

    def close(self):
        self.case.doCleanups()

    def configure(self, scenario):
        w = self.window
        if scenario.startswith("startup"):
            w.home_visible = True
            if "transition" in scenario:
                w.startup_transition_from = "home"
                w.startup_transition_to = "scenario"
                w.startup_transition_started = 9.9
        if scenario in ("economy", "economy_fade", "economy_compat"):
            w.economy_visible = scenario != "economy_fade"
            w.economy_island.progress = .5
            w.clickgui_gpu.available = scenario != "economy_compat"
        if scenario in ("clickgui", "clickgui_fade", "clickgui_compat"):
            w.gui_visible = scenario != "clickgui_fade"
            w.gui_timeline.progress = .5
            if scenario == "clickgui_compat":
                w.setting_mode = lambda module, name, default: "Compatibility Painter"
        if scenario == "student":
            w.student_ui.visible = True
        if scenario == "escape":
            w.escape_menu_visible = True
        if scenario == "chat":
            w.match_chat.input_open = True
        if scenario == "radial":
            w.quick_action_wheel.visible = True
        if scenario == "restore":
            w._economy_restore_pending_generation = 7
            w._economy_generation = 7
            w.economy_phase = EconomyScenePhase.CLOSING
        if scenario == "stale_restore":
            w._economy_restore_pending_generation = 6
            w._economy_generation = 7
            w.economy_phase = EconomyScenePhase.CLOSING
        if scenario == "defer_static":
            w._should_defer_hud_static_rebuild = lambda key: True

    def event(self, name, args, kwargs):
        self.events.append((name, canonical(args), canonical(kwargs)))
        if self.fail_at == name and not self.failed:
            self.failed = True
            if self.rejection:
                return False
            raise TypeError("injected " + name)
        return True

    def record(self, name, result=None):
        def operation(*args, **kwargs):
            return result if self.event(name, args, kwargs) else False
        return operation

    def wrap(self, name, method):
        def operation(*args, **kwargs):
            if not self.event(name, args, kwargs):
                return False
            return method(*args, **kwargs)
        return operation

    def builder(self, name):
        def build(*args, **kwargs):
            self.event(name, args, kwargs)
            commands = ClickGuiDrawList()
            commands.rect(QRectF(11, 12, 130, 44), 7, QColor("#1188dd"))
            self.window.hotspots.append(Hotspot(QRectF(11, 12, 130, 44), name))
            self.window.gui_scroll_viewports.append({"key": name, "rect": QRectF(9, 8, 7, 6)})
            return commands
        return build

    def paint_clickgui(self, *args, **kwargs):
        self.event("clickgui.paint", args, kwargs)
        frame = kwargs.get("gpu_frame")
        if frame is not None:
            frame.publish_interactions(self.window)

    def run(self, *, now=10.):
        with patch("time.perf_counter", return_value=now), patch.object(compositor, "_LOG"):
            try:
                accepted = self.window._paint_gpu_frame_cached()
            except Exception as error:
                accepted = (type(error).__name__, str(error))
        w, c = self.window, self.compositor
        result = dict(
            accepted=accepted,
            static_key=w._gpu_static_ui_key,
            startup_key=w._startup_static_page_key,
            motion_ready=w._gpu_motion_layer_ready,
            updated_at=w._gpu_motion_layer_updated_at,
            frame_recording=w._gpu_frame_recording,
            restore=w._economy_restore_pending_generation,
            hotspots=canonical(w.hotspots),
            viewports=canonical(w.gui_scroll_viewports),
            glass_owner=canonical(c._glass_owner_stack),
            recording_static=c._recording_static_glass,
            faults=c.diagnostics.faults,
            error=c.last_error,
        )
        events, self.events = tuple(self.events), []
        return events, result
