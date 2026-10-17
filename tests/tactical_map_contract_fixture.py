"""Frozen map source and real Qt raster/command recorder for extraction checks.

Only time, animation progress and native device boundaries are controlled. The
map methods, Qt paths/images/painter, fonts and hotspot clipping stay real.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
from pathlib import Path
from types import MethodType, ModuleType, SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QBrush, QFont, QGradient, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from rm_client.match_modes import MATCH_MODES
from rm_client.model import RobotState, build_default_registry
from rm_client.ui.animation import MapIslandState
from rm_client.ui.polar.renderers import tactical_map as current

REFERENCE = Path(__file__).with_name('fixtures') / 'tactical_map_71ebb0f.py.txt'
REFERENCE_SHA256 = '3a4d7cdef2cf3299da9b32f390538430e9a5e643e62e1db967258a63a0e09b89'


def frozen_module():
    source = REFERENCE.read_bytes()
    assert hashlib.sha256(source).hexdigest() == REFERENCE_SHA256, 'Frozen source changed'
    module = ModuleType('rm_client.ui.polar.renderers._map_71ebb0f')
    module.__package__ = 'rm_client.ui.polar.renderers'
    exec(compile(source, str(REFERENCE), 'exec'), module.__dict__)
    return module


FROZEN = frozen_module()


def pixels(image):
    return hashlib.sha256(bytes(image.constBits())).hexdigest()


def value(item):
    if isinstance(item, QRectF):
        return ('rect', *item.getRect())
    if isinstance(item, QPointF):
        return ('point', item.x(), item.y())
    if isinstance(item, QSize):
        return ('size', item.width(), item.height())
    if isinstance(item, QColor):
        return ('color', *item.getRgb())
    if isinstance(item, QFont):
        return ('font', item.toString())
    if isinstance(item, QImage):
        return ('image', item.width(), item.height(), item.devicePixelRatioF(), pixels(item))
    if isinstance(item, QTransform):
        return ('transform', *(getattr(item, f'm{r}{c}')() for r in range(1, 4) for c in range(1, 4)))
    if isinstance(item, QPainterPath):
        return ('path', item.fillRule().value, tuple((item.elementAt(i).x, item.elementAt(i).y,
                                                   item.elementAt(i).type.value) for i in range(item.elementCount())))
    if isinstance(item, QPen):
        return ('pen', value(item.brush()), item.widthF(), item.style().value,
                item.capStyle().value, item.joinStyle().value, tuple(item.dashPattern()))
    if isinstance(item, QGradient):
        return value(QBrush(item))
    if isinstance(item, QBrush):
        gradient = item.gradient()
        if gradient is None:
            return ('brush', item.style().value, value(item.color()))
        shape = tuple((name, value(getattr(gradient, name)())) for name in
                      ('start', 'finalStop', 'center', 'focalPoint', 'radius', 'angle')
                      if hasattr(gradient, name))
        return ('gradient', gradient.type().value, gradient.spread().value, gradient.coordinateMode().value,
                tuple((position, value(color)) for position, color in gradient.stops()), shape)
    if isinstance(item, QPolygonF):
        return ('polygon', tuple(value(point) for point in item))
    if isinstance(item, Enum):
        return item.value
    if is_dataclass(item):
        return tuple((field.name, value(getattr(item, field.name))) for field in fields(item))
    if isinstance(item, dict):
        return tuple((key, value(element)) for key, element in item.items())
    if isinstance(item, (tuple, list)):
        return tuple(value(element) for element in item)
    if isinstance(item, float) and item != item:
        return 'nan'
    return item


class RasterTrace(QPainter):
    """A real QPainter with observed mutations and optional exact-call failure."""
    def __init__(self, image, fail_at=None):
        super().__init__(image)
        self.calls = []
        self.depth = 0
        self.fail_at = fail_at

    def __getattribute__(self, name):
        target = super().__getattribute__(name)
        if not name.startswith(('set', 'draw', 'fill')) and name not in ('save', 'restore', 'translate', 'scale', 'rotate'):
            return target
        def call(*args, **kwargs):
            self.calls.append((name, value(args), value(kwargs)))
            if len(self.calls) == self.fail_at:
                raise RuntimeError(f'map paint fault {self.fail_at}: {name}')
            result = target(*args, **kwargs)
            if name == 'save':
                self.depth += 1
            elif name == 'restore':
                self.depth -= 1
            return result
        return call

    def finish(self):
        while self.depth > 0:
            QPainter.restore(self)
            self.depth -= 1
        QPainter.end(self)


def patterned_map():
    image = QImage(112, 60, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor('#527cb4'))
    painter = QPainter(image)
    painter.fillRect(QRectF(0, 0, 56, 30), QColor('#d98274'))
    painter.fillRect(QRectF(56, 30, 56, 30), QColor('#99b578'))
    painter.end()
    return image


@contextmanager
def implementation(window, module):
    with ExitStack() as stack:
        for name, method in vars(module.TacticalMapMixin).items():
            if callable(method):
                stack.enter_context(patch.object(window, name, MethodType(method, window)))
            elif name.isupper():
                stack.enter_context(patch.object(window, name, deepcopy(method)))
        yield


def set_setting(registry, module, setting, item):
    target = registry.get(module).get_setting(setting)
    assert target is not None, (module, setting)
    target.value = item


def populated_state():
    state = RobotState()
    state.robot_id = 3
    state.map_robots = [
        {'id': 3, 'x': 4.5, 'y': 3.25, 'team': 'ally', 'hp': 333, 'max': 400, 'name': 'Own'},
        {'id': 7, 'x': 25.1, 'y': 12.25, 'team': 'enemy', 'hp': 90, 'max': 600, 'name': 'Enemy', 'highlight': True},
        {'id': 2, 'x': -3, 'y': 20, 'team': 'ally', 'hp': 0, 'max': 0},
    ]
    state.map_markers = [
        {'x': 5, 'y': 9, 'kind': 'control', 'pulse': True, 'label': 'Attack'},
        {'x': 15, 'y': 5, 'kind': 'supply', 'pulse': False, 'label': 'Supply'},
    ]
    state.radar_detections = [
        {'target_pos_x': 2300, 'target_pos_y': 900, 'is_high_light': 1},
        {'target_pos_x': 7, 'target_pos_y': 4, 'is_high_light': 0},
        {'target_pos_x': 'bad', 'target_pos_y': 1},
    ]
    state.radar_detections_at = 98.25
    state.active_penalty = {'penalty_type': 2, 'total_penalty_num': 3, 'penalty_effect_sec': 12}
    state.penalties = [{'penalty_type': 1}, {'penalty_type': 2}, {'penalty_type': 3, 'total_penalty_num': 2}]
    state.buff_rows = [{'buff_type': 1, 'robot_id': 3, 'active': True, 'name': 'Attack', 'remaining': 18}]
    state.special_mechanisms = [{'mechanism_id': 2, 'time_sec': 24}]
    return state


@contextmanager
def scene(window, module=current, *, liquid=False, density='CombatCompact', mode='RMUC7v7',
          side='Red', progress=0.0, hold=False, map_size='Wide', status='Off', missing=False):
    registry = build_default_registry()
    registry.get('TacticalMap').enabled = True
    registry.get('LiquidTacticalMap').enabled = liquid
    set_setting(registry, 'HUD', 'Density', density)
    set_setting(registry, 'HUD', 'Visual Tone', 'PolarLegacy')
    set_setting(registry, 'TacticalMap', 'Map Size', map_size)
    set_setting(registry, 'TacticalMap', 'Status Display', status)
    config = deepcopy(MATCH_MODES[mode])
    image = QImage() if missing else patterned_map()
    map_path = str(window.paths.resource_root / config.field.asset_path)
    state = MapIslandState()
    state.container.content.progress = progress
    replacements = {
        'registry': registry, 'state': populated_state(), 'tactical_map_state': state,
        'tactical_map_hold': hold, 'tactical_map_field_rect': QRectF(7, 9, 101, 103),
        'tactical_map_field_layout': None, 'tactical_map_field_mirrored': False,
        '_tactical_map_hit_geometry': None,
        'map_images': {map_path: image}, 'map_display_cache': {}, '_map_round_cache': {},
        'hotspots': [], '_hotspot_clip': None, 'hover_point': QPointF(-100, -100),
        'alliance_side': lambda: side, 'minecraft_hud_active': lambda: False,
        'anim': SimpleNamespace(value=lambda key, target, *args, **kwargs: target),
        '_hud_style_palette_cache': None,
    }
    with implementation(window, module), ExitStack() as stack:
        for name, item in replacements.items():
            stack.enter_context(patch.object(window, name, item, create=True))
        stack.enter_context(patch.object(window.match_mode_manager, 'current', config))
        stack.enter_context(patch.object(module.time, 'perf_counter', return_value=100.0))
        yield window


def render(window, module=current, *, width=960, height=640, dpr=1.0, scale=1.0, fail_at=None, **options):
    with scene(window, module, **options):
        image = QImage(round(width * dpr), round(height * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        painter = RasterTrace(image, fail_at)
        error = None
        painted_field = None
        original_map = window.draw_official_map
        def observe_map(p, rect, config):
            nonlocal painted_field
            result = original_map(p, rect, config)
            painted_field = {
                'field_rect': value(result), 'field_key': config.field.key,
                # Both fixture mirror settings are explicitly at their true defaults.
                'mirrored': bool(config.field.mirror_for_blue and options.get('side', 'Red') == 'Blue'),
            }
            return result
        try:
            painter.translate(2.5, 1.25)
            painter.scale(scale, scale)
            with patch.object(window, 'draw_official_map', side_effect=observe_map):
                window.draw_tactical_map_hud(painter, width, height)
        except Exception as exc:
            error = (type(exc).__name__, str(exc))
        observed = {
            'commands': painter.calls, 'hotspots': value(window.hotspots),
            'painted_field': painted_field,
            'field_rect': value(window.tactical_map_field_rect),
            'field_key': getattr(window.tactical_map_field_layout, 'key', None),
            'mirrored': window.tactical_map_field_mirrored,
            'container': value(window.tactical_map_state),
            'error': error, 'save_depth': painter.depth,
            'radar_diagnostics': (window.radar_map_actual, window.radar_map_reason),
        }
        painter.finish()
        observed['pixels'] = pixels(image)
        return observed
