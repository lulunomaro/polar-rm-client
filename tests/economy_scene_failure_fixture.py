"""Compare draw/event ordering and partial UI state against the fixed scene."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from PySide6.QtCore import QRectF
from rm_client.economy import TRANSACTION_LABELS
from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.polar.mixins import economy_mixin as current
from economy_render_fixture import EconomyHost, RecordingPainter, record
from test_economy_presentation import varied_host

BASELINE_PATH = Path(__file__).with_name("fixtures") / "economy_scene_baseline.py"
NAMESPACE = vars(current).copy()
NAMESPACE["TRANSACTION_LABELS"] = TRANSACTION_LABELS
exec(compile(BASELINE_PATH.read_text(), str(BASELINE_PATH), "exec"), NAMESPACE)
REFERENCE = NAMESPACE["ReferenceEconomyScenes"]


class Recorder:
    def __init__(self, fail_at):
        self.fail_at = fail_at
        self.events = []

    def emit(self, kind, args, kwargs):
        self.events.append((kind, record(args), record(kwargs)))
        if len(self.events) == self.fail_at:
            raise RuntimeError(f"injected {kind}")

    def wrap(self, kind, callback):
        def invoke(*args, **kwargs):
            self.emit(kind, args, kwargs)
            return callback(*args, **kwargs)
        return invoke


def run_scene(*, renderer="gpu", case="normal", width=1600, height=900, progress=1., fail_at=None, frozen=False):
    host = varied_host(case)
    host.economy_island.progress = progress
    host._hotspot_clip = QRectF(1., 2., 600., 700.) if renderer == "compat" else None
    recorder = Recorder(fail_at)
    painter = RecordingPainter()
    retained_commands = []

    class Commands(ClickGuiDrawList):
        def __init__(self):
            super().__init__()
            retained_commands.append(self)

        def text(self, *args, **kwargs):
            recorder.emit("gpu.text", args, kwargs)
            return super().text(*args, **kwargs)

        def rect(self, *args, **kwargs):
            recorder.emit("gpu.rect", args, kwargs)
            return super().rect(*args, **kwargs)

        def image(self, *args, **kwargs):
            recorder.emit("gpu.image", args, kwargs)
            return super().image(*args, **kwargs)

    for name in ("font", "add_hotspot", "record_glass", "liquid_press_amount", "anim_duration"):
        setattr(host, name, recorder.wrap(name, getattr(host, name)))
    host.gpu_compositor = NS(add_glass=host.record_glass)
    host.anim = NS(value=recorder.wrap("animation", lambda key, target, *args, **kwargs: target))
    for name in ("draw_glass_text", "_economy_dim_around", "_economy_island_well", "_economy_glass_row"):
        original = getattr(host, name)
        def paint_call(p, *args, _name=name, _callback=original, **kwargs):
            recorder.emit(_name, args, kwargs)
            return _callback(p, *args, **kwargs)
        setattr(host, name, paint_call)
    for name in ("save", "restore", "setOpacity", "fillRect", "setClipRect", "setClipPath"):
        original = getattr(painter, name)
        if name == "setClipPath":
            def clip_call(path, _callback=original):
                recorder.emit("painter.setClipPath", (path.boundingRect(),), {})
                return _callback(path)
            setattr(painter, name, clip_call)
        else:
            setattr(painter, name, recorder.wrap("painter." + name, original))

    def primitive(kind):
        def invoke(p, *args, **kwargs):
            recorder.emit(kind, args, kwargs)
            p.calls.append((kind, args, kwargs))
        return invoke

    def reload_paint(owner, p, commands):
        recorder.emit("reload.paint", (commands.commands,), {})
        p.calls.append(("reload", commands.commands))

    language_query = current.chinese_enabled
    def query_language(registry):
        recorder.emit("locale.query", (), {})
        return language_query(registry)
    substitutions = {
        "chinese_enabled": query_language,
        "ClickGuiDrawList": Commands,
        "draw_economy_glass": primitive("glass"),
        "draw_economy_glass_capsule": primitive("capsule"),
        "rounded_rect": primitive("rounded_rect"),
        "text_width": lambda p, font, text: len(text) * font.pointSizeF() * .5,
        "paint_reload_commands": reload_paint,
    }
    error = None
    with ExitStack() as stack:
        for name, replacement in substitutions.items():
            stack.enter_context(patch.object(current, name, replacement))
        stack.enter_context(patch.dict(NAMESPACE, substitutions))
        stack.enter_context(patch.object(current.time, "perf_counter", return_value=100.))
        implementation = REFERENCE if frozen else current.EconomyMixin
        try:
            if renderer == "gpu":
                implementation.build_gpu_economy_workspace(host, width, height)
            else:
                implementation.draw_economy_overlay_compat(host, painter, width, height, progress)
        except Exception as exc:
            error = (type(exc).__name__, str(exc))
    state = {
        "selected": (host.economy_category, host.economy_selected_key, host.economy_selected),
        "offers_scroll": vars(host.economy_offers_scroll),
        "detail_scroll": vars(host.economy_detail_scroll),
        "hotspots": host.hotspots,
        "clip": host._hotspot_clip,
        "painter": (painter._stack, painter._opacity, painter.calls),
        "glass": host.glass,
        "commands": [commands.commands for commands in retained_commands],
    }
    return record(recorder.events), record(state), error
