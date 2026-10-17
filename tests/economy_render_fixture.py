"""Deterministic economy adapter recorder, without widgets or a GL context.

Fonts and geometry are real Qt value objects. Painter/glass submission and text
measurement are controlled test doubles; these traces are not GPU/raster proof.
"""
from dataclasses import fields, is_dataclass
from enum import Enum
from types import SimpleNamespace as NS
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont

from rm_client.economy import EconomyOffer
from rm_client.model import RobotState, build_default_registry
from rm_client.ui.economy_workspace import EconomyWorkspaceLayer
from rm_client.ui.polar.mixins.economy_mixin import EconomyMixin
from rm_client.ui.polar.primitives import InertialScroll
from rm_client.ui.polar.text_metrics import economy_lerp_rect


def record(value):
    if isinstance(value, QRectF):
        return [record(v) for v in value.getRect()]
    if isinstance(value, QPointF):
        return [record(value.x()), record(value.y())]
    if isinstance(value, QColor):
        return list(value.getRgb())
    if isinstance(value, QFont):
        return [value.family(), value.pointSizeF(), int(value.weight()), value.italic()]
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: record(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): record(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [record(item) for item in value]
    if isinstance(value, float):
        return round(value, 9)
    return value


class RecordingPainter:
    def __init__(self):
        self.calls = []
        self._opacity = 1.0
        self._stack = []

    def save(self):
        self._stack.append(self._opacity)
        self.calls.append(("save",))

    def restore(self):
        self._opacity = self._stack.pop()
        self.calls.append(("restore",))

    def opacity(self):
        return self._opacity

    def setOpacity(self, value):
        self._opacity = value
        self.calls.append(("opacity", value))

    def fillRect(self, *args):
        self.calls.append(("fill", *args))

    def setClipRect(self, *args):
        self.calls.append(("clip", *args))

    def setClipPath(self, path):
        self.calls.append(("clip_path", path.boundingRect()))


class EconomyHost(EconomyMixin):
    economy_lerp_rect = staticmethod(economy_lerp_rect)

    def __init__(self, *, progress=1.0, category="ammo"):
        self.registry = build_default_registry()
        self.state = RobotState()
        self.state.remaining_economy = 400
        self.state.total_economy_obtained = 800
        self.economy_workspace_layer = EconomyWorkspaceLayer()
        self.economy_island = NS(progress=progress)
        self.economy_bridge = NS(latest=None, history=[])
        self.economy_category = category
        self.economy_visible = True
        self.economy_selected_key = category + "0"
        self.economy_selected = 0
        self.economy_hold_key = ""
        self.economy_hold_started = 0.0
        self.economy_armed_key = ""
        self.economy_armed_until = 0.0
        self.student_reload_expanded = False
        self.student_ui = NS(gate="Left", quantity=10)
        self.match_mode_manager = NS(current=NS(title="测试模式"))
        self.economy_offers_scroll = InertialScroll()
        self.economy_detail_scroll = InertialScroll()
        self.hover_point = QPointF(-10, -10)
        self.anim = NS(value=lambda key, target, *args, **kwargs: target)
        self.hotspots = []
        self._hotspot_clip = None
        self.glass = []
        self.gpu_compositor = NS(add_glass=self.record_glass)
        self.offers = [
            EconomyOffer(group + str(i), group, "商品标题 " + str(i), group + str(i),
                         "规则描述", "颗", 1, 10 + i, 1, i, True)
            for group, count in (("ammo", 7), ("recovery", 3), ("revive", 2), ("support", 2))
            for i in range(count)
        ]

    def record_glass(self, *args, **kwargs):
        self.glass.append((args, kwargs))

    def economy_offers(self, category=None):
        return [offer for offer in self.offers if category is None or offer.category == category]

    def font(self, family, size):
        font = QFont(family)
        font.setPointSizeF(size)
        return font

    def get_auto_ui_scale(self, *args, **kwargs):
        return 1.0

    def setting_mode(self, *args):
        return "Control Center"

    def anim_duration(self, value):
        return value

    def liquid_press_amount(self, rect):
        return 0.0

    def add_hotspot(self, rect, action, payload=None, button=None, tooltip=""):
        if self._hotspot_clip is not None:
            rect = rect.intersected(self._hotspot_clip)
        if not rect.isEmpty():
            self.hotspots.append((QRectF(rect), action, payload, button, tooltip))

    def draw_glass_text(self, painter, *args):
        painter.calls.append(("text", *args))

    draw_text = draw_glass_text

    def _economy_dim_around(self, painter, *args):
        painter.calls.append(("dim_around", *args))

    def _economy_island_well(self, painter, *args):
        painter.calls.append(("well", *args))

    def _economy_glass_row(self, painter, *args, **kwargs):
        painter.calls.append(("row", args, kwargs))


def gpu_trace(host, width, height):
    with patch("rm_client.ui.polar.mixins.economy_mixin.time.perf_counter", return_value=100.0):
        commands = host.build_gpu_economy_workspace(width, height)
    return record({"commands": commands.commands, "glass": host.glass})


def compat_trace(host, width, height, alpha):
    painter = RecordingPainter()
    def call(name):
        return lambda p, *args, **kwargs: p.calls.append((name, args, kwargs))
    module = "rm_client.ui.polar.mixins.economy_mixin."
    with patch(module + "time.perf_counter", return_value=100.0), \
         patch(module + "draw_economy_glass", side_effect=call("glass")), \
         patch(module + "draw_economy_glass_capsule", side_effect=call("capsule")), \
         patch(module + "rounded_rect", side_effect=call("rounded_rect")), \
         patch(module + "text_width", side_effect=lambda p, font, text: len(text) * font.pointSizeF() * .5), \
         patch(module + "paint_reload_commands", side_effect=lambda host, p, commands: p.calls.append(("reload", commands.commands))):
        host.draw_economy_overlay_compat(painter, width, height, alpha)
    assert not painter._stack, "compat painter save/restore balance changed"
    return record(painter.calls)
