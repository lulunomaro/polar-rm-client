from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import struct
import time
from typing import Callable

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QImage, QOpenGLContext, QPainter
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLPaintDevice,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)


from .render_diagnostics import RenderFault, RenderFaultDiagnostics
from .framebuffer_storage import FramebufferStorage, framebuffer_slot
from .glass_commands import GlassCommandBuffer, glass_draw_geometry

_LOG = logging.getLogger(__name__)


_GL_BLEND = 0x0BE2
_GL_COLOR_BUFFER_BIT = 0x00004000
_GL_DEPTH_TEST = 0x0B71
_GL_FLOAT = 0x1406
_GL_FRAMEBUFFER = 0x8D40
_GL_FRAMEBUFFER_SRGB = 0x8DB9
_GL_FUNC_ADD = 0x8006
_GL_ONE = 1
_GL_ONE_MINUS_SRC_ALPHA = 0x0303
_GL_RGBA8 = 0x8058
_GL_SCISSOR_TEST = 0x0C11
_GL_TEXTURE0 = 0x84C0
_GL_TEXTURE_2D = 0x0DE1
_GL_TRIANGLE_STRIP = 0x0005
_GL_RGBA = 0x1908
_GL_UNSIGNED_BYTE = 0x1401
_GL_UNPACK_ALIGNMENT = 0x0CF5
_GL_PACK_ALIGNMENT = 0x0D05


class BackdropLayer(str, Enum):
    SCENE = "SceneBackdrop"
    CONTROL = "ControlBackdrop"
    COMBINED = "CombinedBackdrop"


class LensMaterial(str, Enum):
    STRONG = "LensStrong"
    REGULAR = "LensRegular"
    ISLAND = "LensIsland"


class GlassOwner(str, Enum):
    """Identifies which scene owns a glass command."""

    HUD = "HUD"
    CLICKGUI = "ClickGUI"
    ECONOMY = "Economy"
    RADIAL = "Radial"
    HERO_DEPLOY = "HeroDeploy"


def compose_combined_backdrop_pixel(
    scene: tuple[float, float, float],
    control: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    """Combined Backdrop 的 CPU 参考合成，供像素级回归测试使用。"""
    return tuple(control[index] + scene[index] * (1.0 - control[3]) for index in range(3))


def compose_premultiplied_pixel(
    background: tuple[float, float, float],
    glass: tuple[float, float, float, float],
    ui: tuple[float, float, float, float],
    dynamic_ui: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
) -> tuple[float, float, float]:
    """Reference implementation of the final shader's premultiplied blend."""
    glass = normalize_premultiplied_pixel(glass)
    ui = normalize_premultiplied_pixel(ui)
    dynamic_ui = normalize_premultiplied_pixel(dynamic_ui)
    glass_scene = tuple(glass[index] + background[index] * (1.0 - glass[3]) for index in range(3))
    base_ui = tuple(ui[index] + glass_scene[index] * (1.0 - ui[3]) for index in range(3))
    return tuple(dynamic_ui[index] + base_ui[index] * (1.0 - dynamic_ui[3]) for index in range(3))


def normalize_premultiplied_pixel(
    pixel: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Clamp a sample to the strict premultiplied-alpha invariant.

    This function deliberately does not guess whether a sample used straight
    alpha.  Every producer in the compositor is required to premultiply once;
    the present pass only rejects illegal ``RGB > A`` residue.
    """
    alpha = max(0.0, min(1.0, float(pixel[3])))
    rgb = tuple(max(0.0, min(alpha, float(pixel[index]))) for index in range(3))
    return rgb[0], rgb[1], rgb[2], alpha


def sanitize_premultiplied_rgba_bytes(payload: bytes | bytearray) -> bytes:
    """Remove RGB residue from transparent premultiplied RGBA pixels.

    The TargetHUD region runs through this every rendered frame. The scalar
    loop cost ~5 ms per frame at 2K and was the single largest contributor to
    module-list fade stutter, so vectorise with numpy when present and keep
    the original loop only as a dependency-free fallback.
    """
    try:
        import numpy as np  # type: ignore
    except ImportError:
        np = None  # type: ignore[assignment]
    if np is not None:
        usable = (len(payload) // 4) * 4
        pixels = np.frombuffer(payload, dtype=np.uint8, count=usable).reshape(-1, 4).copy()
        if pixels.size:
            # min(rgb, alpha) also zeroes RGB wherever alpha == 0, which is
            # exactly the original two-branch behaviour.
            np.minimum(pixels[:, :3], pixels[:, 3:4], out=pixels[:, :3])
        return pixels.tobytes()
    cleaned = bytearray(payload)
    usable = len(cleaned) - (len(cleaned) % 4)
    for offset in range(0, usable, 4):
        alpha = cleaned[offset + 3]
        if alpha == 0:
            cleaned[offset] = 0
            cleaned[offset + 1] = 0
            cleaned[offset + 2] = 0
        else:
            cleaned[offset] = min(cleaned[offset], alpha)
            cleaned[offset + 1] = min(cleaned[offset + 1], alpha)
            cleaned[offset + 2] = min(cleaned[offset + 2], alpha)
    return bytes(cleaned)


@dataclass(frozen=True)
class GlassCommand:
    """一个逻辑像素坐标下的玻璃镜片。"""

    rect: QRectF
    radius: float
    strength: float = 1.0
    surface: str = "main"
    hover: float = 0.0
    opacity: float = 1.0
    material: LensMaterial = LensMaterial.REGULAR
    refraction_height_px: float = 10.0
    refraction_amount_px: float = -9.0
    chromatic_aberration: float = 0.28
    depth_effect: float = 0.22
    frost_amount: float = 0.0
    vibrancy_amount: float = 0.0
    clip_rect: QRectF | None = None
    owner: GlassOwner = GlassOwner.HUD


@dataclass(frozen=True)
class BackdropTreatment:
    """A source-aligned treatment for content beneath a transient workspace."""

    active: bool = False
    progress: float = 0.0
    blur_radius_px: float = 2.0
    dim_alpha: float = 0.0
    include_overlay: bool = False
    # Sigma 5.1.0 does not use the compositor's compact five-tap Gaussian.
    # Its generated ``jelloblur`` post chain runs Minecraft's 41-sample box
    # blur horizontally and vertically with an integer radius of 20.  Keep the
    # kernel explicit so other overlays and the liquid-glass renderer retain
    # their existing appearance.
    blur_kernel: str = "compact_gaussian"


@dataclass(frozen=True)
class LensRegressionCase:
    """液态镜片回归参数；数值直接对应 Kyant GlassSlider 教程。"""

    name: str
    source_size: tuple[int, int]
    preview_size: tuple[int, int]
    preview_scale: float
    refraction_height_px: float
    refraction_amount_px: float
    depth_effect: float
    chromatic_aberration: float
    minimum_outline_iou: float = 0.97


KYANT_GLASS_SLIDER_CASE = LensRegressionCase(
    name="KyantGlassSlider",
    source_size=(56, 32),
    preview_size=(420, 240),
    preview_scale=7.5,
    refraction_height_px=90.0,
    refraction_amount_px=-120.0,
    depth_effect=0.0,
    chromatic_aberration=1.0,
)


@dataclass(frozen=True)
class FailedFrameRecovery:
    """Keep the initiating defect distinct from independent cleanup defects."""

    render_fault: RenderFault
    cleanup_faults: tuple[RenderFault, ...]
    rejected_layers: tuple[str, ...] = ()


@dataclass(frozen=True)
class RejectedFrameRecovery:
    """Ordinary rejected work is not an invented renderer exception."""
    reason: str
    rejected_layers: tuple[str, ...]
    cleanup_faults: tuple[RenderFault, ...]


class _GlCleanupFailure(RuntimeError):
    """Carry one operation fault plus every independent unwind failure."""

    def __init__(self, failures: tuple[tuple[str, Exception], ...]) -> None:
        self.failures = failures
        super().__init__("; ".join(
            f"{name or 'draw'}: {type(error).__name__}: {error}" for name, error in failures
        ))


class _GlProgramBuildFailure(_GlCleanupFailure):
    """Transfer a failed factory candidate to the compositor's retirement slot.

    The native wrapper exists before compilation can complete. The exception
    carries it across the existing classmethod factory boundary without global
    state or mutating the original driver exception. Diagnostics unwrap the
    original failure through the same structured operation-error contract.
    """

    def __init__(self, program: QOpenGLShaderProgram, error: Exception) -> None:
        self.program = program
        super().__init__((("", error),))


def _attempt_gl_cleanup(
    actions: tuple[tuple[str, Callable[[], object]], ...],
) -> tuple[bool, tuple[tuple[str, Exception], ...]]:
    accepted = True
    failures = []
    for name, release in actions:
        try:
            if release() is False:
                accepted = False
        except Exception as exc:
            accepted = False
            failures.append((name, exc))
    return accepted, tuple(failures)


class LayerPaintSession:
    """Own one active painter and pass-local state until the layer is released.

    The caller may end raster painting before native commands. An exceptional
    draw leaves the painter registered for the pass's independent unwind.
    """

    def __init__(self, device) -> None:
        self.device = device
        self._painter: QPainter | None = None
        self.cleanup: list[tuple[str, Callable[[], object]]] = [
            ("painter", self._end_painter),
        ]

    def begin_painter(self) -> QPainter:
        if self._painter is not None:
            raise RuntimeError("layer already owns an active painter")
        self._painter = QPainter(self.device)
        return self._painter

    def _end_painter(self) -> bool:
        painter, self._painter = self._painter, None
        return painter is None or painter.end() is not False

    def end_painter(self) -> bool:
        try:
            return self._end_painter()
        except Exception as error:
            raise _GlCleanupFailure((("cleanup.painter", error),)) from error

    def defer_cleanup(self, name: str, release: Callable[[], object]) -> None:
        # End the painter first; restore nested recording/owner state next.
        self.cleanup.insert(1, (name, release))


class GpuFrameCompositor:
    """在私有 FBO 中顺序合成背景、共享模糊、玻璃和 UI。"""

    background_fbo = framebuffer_slot("background_fbo")
    control_fbo = framebuffer_slot("control_fbo")
    blur_temp_fbo = framebuffer_slot("blur_temp_fbo")
    blur_fbo = framebuffer_slot("blur_fbo")
    treated_blur_temp_fbo = framebuffer_slot("treated_blur_temp_fbo")
    treated_blur_fbo = framebuffer_slot("treated_blur_fbo")
    hud_glass_fbo = framebuffer_slot("hud_glass_fbo")
    glass_fbo = framebuffer_slot("glass_fbo")
    overlay_fbo = framebuffer_slot("overlay_fbo")
    vision_fbo = framebuffer_slot("vision_fbo")
    dynamic_fbo = framebuffer_slot("dynamic_fbo")
    target_fbo = framebuffer_slot("target_fbo")

    def __init__(self) -> None:
        self.available = False
        self._release_pending = False
        self.last_error = ""
        self.diagnostics = RenderFaultDiagnostics()
        self.last_failed_frame: FailedFrameRecovery | None = None
        self.last_rejected_frame: RejectedFrameRecovery | None = None
        self._frame_rejection = ""
        self._framebuffers = FramebufferStorage()
        self._size = QSize()
        self._device_pixel_ratio = 1.0
        # Scene metadata belongs to this storage and pixel ratio, not just
        # the widget's logical size. DPR can change without resizeEvent.
        self.fbo_generation = 0
        # 静态 HUD 与逐帧动画镜片必须分开。旧实现只在静态层失效时
        # 清空一个总列表，动态岛和地图镜片会逐帧累积并把画面洗白。
        self._glass_commands = GlassCommandBuffer(GlassOwner.HUD)
        self._blur_valid = False
        self._treated_blur_valid = False
        # The media texture stays immutable between decoded frames. Mouse
        # parallax is applied at sampling time so it never invalidates the
        # scene FBO or shared blur texture.
        self._scene_uv_offset = (0.0, 0.0)
        self._backdrop_treatment = BackdropTreatment()

        self.present_program: QOpenGLShaderProgram | None = None
        self.blur_program: QOpenGLShaderProgram | None = None
        self.jello_blur_program: QOpenGLShaderProgram | None = None
        self.glass_program: QOpenGLShaderProgram | None = None
        # uniformLocation is a per-name GL query (~40 us). The blur/glass/
        # present passes re-resolved ~47 names every frame; memoise them per
        # program identity and clear whenever programs are rebuilt.
        self._uniform_cache: dict[tuple[int, str], int] = {}
        self.vertex_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.vertex_array = QOpenGLVertexArrayObject()

        self.last_background_ms = 0.0
        self.last_blur_ms = 0.0
        self.last_glass_ms = 0.0
        self.last_overlay_ms = 0.0
        self.last_dynamic_ms = 0.0
        self.last_present_ms = 0.0

    def _uniform(self, program: QOpenGLShaderProgram, name: str) -> int:
        key = (id(program), name)
        location = self._uniform_cache.get(key)
        if location is None:
            location = int(program.uniformLocation(name))
            self._uniform_cache[key] = location
        return location

    def _record_operation_reject(self, phase: str, error: Exception) -> tuple[RenderFault, ...]:
        if isinstance(error, _GlCleanupFailure):
            return tuple(
                fault
                for name, nested in error.failures
                for fault in self._record_operation_reject(f"{phase}.{name}" if name else phase, nested)
            )
        return (self.diagnostics.record(phase, error, logger=_LOG, now=time.perf_counter()),)

    def _run_pass(
        self,
        phase: str,
        render: Callable[[], bool],
        *,
        cleanup: tuple[tuple[str, Callable[[], object]], ...] = (),
        on_reject: tuple[tuple[str, Callable[[], object]], ...] = (),
    ) -> bool:
        """Contain a GL pass fault while preserving root and cleanup evidence.

        A normal not-ready/bind rejection remains False without an invented
        exception. Every independent cleanup is attempted; a cleanup defect
        also rejects the pass rather than reporting a successful frame.
        """
        fault = None
        cleanup_faults = []
        try:
            accepted = bool(render())
        except Exception as exc:
            accepted = False
            fault, *unwind_faults = self._record_operation_reject(phase, exc)
            cleanup_faults.extend(unwind_faults)
        actions = (*on_reject, *cleanup) if not accepted else cleanup
        cleanup_accepted, errors = _attempt_gl_cleanup(actions)
        for name, error in errors:
            cleanup_faults.extend(self._record_operation_reject(f"{phase}.cleanup.{name}", error))
        if fault is not None or cleanup_faults:
            primary = fault if fault is not None else cleanup_faults[0]
            self.last_error = primary.summary
            self.last_failed_frame = FailedFrameRecovery(primary, tuple(cleanup_faults))
        return accepted and cleanup_accepted

    def initialize(self) -> bool:
        if self.available:
            return True
        # Unavailable does not mean unowned: resize failure and incomplete
        # retirement can leave programs or native buffers alive. Never replace
        # them until the previous lifetime has independently finished release.
        if self._release_pending or any(program is not None for program in (
            self.present_program, self.blur_program, self.jello_blur_program, self.glass_program,
        )):
            def retire() -> bool:
                self.release()
                return True
            if not self._run_pass("initialize.retire", retire):
                return False
        accepted = self._run_pass(
            "initialize", self._initialize_resources, on_reject=(("resources", self.release),),
        )
        if not accepted:
            self.available = False
        return accepted

    def _initialize_resources(self) -> bool:
        self._install_program("present_program", self._build_present_program)
        self._install_program("blur_program", self._build_blur_program)
        self._install_program("jello_blur_program", self._build_jello_blur_program)
        self._install_program("glass_program", self._build_lens_program)
        self._uniform_cache.clear()
        if not self.vertex_array.create():
            raise RuntimeError("GPU compositor VAO creation failed")
        if not self.vertex_buffer.create():
            raise RuntimeError("GPU compositor vertex buffer creation failed")
        if not self._initialize_quad_storage():
            return False
        self.available = True
        self.last_error = ""
        return True

    def _install_program(self, name: str, build: Callable[[], QOpenGLShaderProgram]) -> None:
        """Publish both successful and failed native candidates to one owner."""
        try:
            program = build()
        except _GlProgramBuildFailure as failure:
            setattr(self, name, failure.program)
            raise
        setattr(self, name, program)

    def _initialize_quad_storage(self) -> bool:
        """Unbind setup state before lifetime retirement, even on partial bind.

        Bindings belong to this pass; created native handles belong to release().
        Preserve the setup cause plus every independent unbind failure instead
        of allowing a finally-block exception to replace the initiating defect.
        """
        failure = None
        accepted = False
        try:
            self.vertex_array.bind()
            if self.vertex_buffer.bind() is not False:
                vertices = struct.pack("8f", -1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
                self.vertex_buffer.allocate(vertices, len(vertices))
                accepted = True
        except Exception as error:
            failure = error
        finally:
            cleanup_accepted, cleanup = _attempt_gl_cleanup((
                ("setup.cleanup.buffer", self.vertex_buffer.release),
                ("setup.cleanup.array", self.vertex_array.release),
            ))
        errors = (("", failure), *cleanup) if failure is not None else cleanup
        if errors:
            raise _GlCleanupFailure(errors) from (failure or errors[0][1])
        return accepted and cleanup_accepted

    def ensure_size(self, width: int, height: int, device_pixel_ratio: float) -> bool:
        if not self.available:
            return False
        accepted = self._run_pass(
            "resize", lambda: self._allocate_size(width, height, device_pixel_ratio),
            on_reject=(("framebuffers", self._destroy_fbos),),
        )
        if not accepted:
            self.available = False
        return accepted

    def _allocate_size(self, width: int, height: int, device_pixel_ratio: float) -> bool:
        if not self.available:
            return False
        ratio = max(1.0, float(device_pixel_ratio))
        size = QSize(max(1, round(width * ratio)), max(1, round(height * ratio)))
        if size == self._size and self._framebuffers.reusable():
            if ratio != self._device_pixel_ratio:
                # Equal physical sizes can still represent different logical
                # geometry. Retain storage, but redraw its content and blur.
                self.fbo_generation += 1
                self._blur_valid = False
                self._treated_blur_valid = False
            self._device_pixel_ratio = ratio
            return True
        self._destroy_fbos()
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Attachment.NoAttachment)
        fmt.setInternalTextureFormat(_GL_RGBA8)
        # QPainter's OpenGL engine uses stencil for path fills, strokes and
        # nonrectangular clips. A colour-only target turns rounded outlines
        # into bounding-box fragments on Windows and corrupts glyph masks.
        # Pure shader/blur/lens targets remain colour-only; their material
        # and sampling parameters are unchanged.
        painter_fmt = QOpenGLFramebufferObjectFormat()
        painter_fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        painter_fmt.setInternalTextureFormat(_GL_RGBA8)
        self._framebuffers.allocate(
            size, create=QOpenGLFramebufferObject,
            shader_format=fmt, painter_format=painter_fmt,
        )
        self._size = size
        self._device_pixel_ratio = ratio
        self._blur_valid = False
        self._treated_blur_valid = False
        self.fbo_generation += 1
        return True

    def paint_device(self, layer: str) -> QOpenGLPaintDevice | None:
        # Qt's OpenGL paint engine keeps a glyph-atlas binding cache.  The
        # compositor samples up to five textures and used to leave texture
        # unit 4 active, so the next QPainter bound its glyph atlas to the
        # wrong unit and text sampled fragments of the scene textures.
        context = QOpenGLContext.currentContext()
        if context is not None:
            functions = context.extraFunctions()
            self._restore_qt_texture_state(functions, clear_bindings=True)
            # Direct sub-image uploads use byte alignment.  Qt's glyph cache
            # expects the OpenGL default row alignment, so restore it before
            # every QOpenGLPaintDevice starts accepting text.
            functions.glPixelStorei(_GL_UNPACK_ALIGNMENT, 4)
            functions.glPixelStorei(_GL_PACK_ALIGNMENT, 4)
        if layer == "background":
            fbo = self.background_fbo
        elif layer == "control":
            fbo = self.control_fbo
        elif layer == "dynamic":
            fbo = self.dynamic_fbo
        elif layer == "target":
            fbo = self.target_fbo
        else:
            fbo = self.overlay_fbo
        if fbo is None or not fbo.bind():
            return None
        return QOpenGLPaintDevice(self._size)

    def paint_layer(
        self, functions, layer: str, ratio: float,
        render: Callable[[LayerPaintSession], bool], *, transparent: bool = True,
    ) -> bool:
        """Acquire, clear, draw and independently unwind a private layer.

        Only an accepted draw with successful painter/FBO cleanup can be
        committed to a window cache. Painter and layer release failures retain
        separate typed evidence and cannot replace an initiating draw fault.
        """
        session = None
        def draw() -> bool:
            nonlocal session
            device = self.paint_device(layer)
            if device is None:
                return False
            session = LayerPaintSession(device)
            device.setDevicePixelRatio(ratio)
            self.clear_current(functions, transparent=transparent)
            return render(session)

        def finish_session() -> bool:
            if session is None:
                return True
            accepted, failures = _attempt_gl_cleanup(tuple(session.cleanup))
            if failures:
                raise _GlCleanupFailure(failures)
            return accepted

        accepted = self._run_pass(
            f"layer.paint.{layer}", draw,
            cleanup=(("session", finish_session),
                     ("framebuffer", lambda: self.release_layer(layer))),
        )
        if not accepted:
            self.reject_frame(f"{layer} paint pass rejected")
        return accepted

    def paint_raster(self, device, draw: Callable[[QPainter], None], *, phase: str) -> bool:
        """Finish a CPU image/picture painter before its device can retire."""
        session = LayerPaintSession(device)
        def paint() -> bool:
            draw(session.begin_painter())
            return True
        accepted = self._run_pass(phase, paint, cleanup=(("painter", session._end_painter),))
        if not accepted:
            self.reject_frame(f"{phase} rejected")
        return accepted

    def render_vision_layer(self, functions, render: Callable[[], bool]) -> bool:
        """Keep vision ownership separate from accepted native draw/cleanup."""
        def draw() -> bool:
            if self.vision_fbo is None or not self.vision_fbo.bind():
                return False
            self.clear_current(functions, transparent=True)
            return render()
        accepted = self._run_pass(
            "layer.paint.vision", draw, cleanup=(("framebuffer", self.end_vision_layer),),
        )
        if not accepted:
            self.reject_frame("vision paint pass rejected")
        return accepted

    def clear_current(self, functions, *, transparent: bool) -> None:
        functions.glViewport(0, 0, self._size.width(), self._size.height())
        functions.glDisable(_GL_SCISSOR_TEST)
        functions.glDisable(_GL_DEPTH_TEST)
        functions.glDisable(_GL_FRAMEBUFFER_SRGB)
        functions.glClearColor(0.0, 0.0, 0.0, 0.0 if transparent else 1.0)
        functions.glClear(_GL_COLOR_BUFFER_BIT)

    @property
    def frame_rejected(self) -> bool:
        return bool(self._frame_rejection)

    def reject_frame(self, reason: str, *, detail: str = "") -> None:
        """Retain a child renderer's False result without inventing a fault.

        Child renderers keep their own typed exception/cleanup diagnostics.
        The frame owner only records why their incomplete output cannot be
        cached or presented; a later rejection must not erase that first cause.
        """
        if not self._frame_rejection:
            self._frame_rejection = f"{reason}: {detail}" if detail else reason

    def _release_frame_layers(self) -> tuple[tuple[RenderFault, ...], tuple[str, ...]]:
        faults, rejected = [], []
        original_error = self.last_error
        for layer in ("background", "control", "overlay", "vision", "dynamic", "target"):
            try:
                accepted = self.end_vision_layer() if layer == "vision" else self.release_layer(layer)
                if accepted is False:
                    rejected.append(layer)
            except Exception as error:
                faults.append(self.diagnostics.record(
                    f"frame.cleanup.{layer}", error, logger=_LOG, now=time.perf_counter()))
        self.last_error = original_error
        return tuple(faults), tuple(rejected)

    def recover_failed_frame(self, exc: Exception) -> FailedFrameRecovery:
        """Release every possible owner while preserving the initiating defect."""
        fault = self.diagnostics.record("frame.compose", exc, logger=_LOG, now=time.perf_counter())
        self.last_error = fault.summary
        cleanup_faults, rejected = self._release_frame_layers()
        recovery = FailedFrameRecovery(fault, cleanup_faults, rejected)
        self.last_failed_frame = recovery
        if rejected:
            self.last_rejected_frame = RejectedFrameRecovery("exception cleanup rejected", rejected, cleanup_faults)
        return recovery

    def recover_rejected_frame(self) -> RejectedFrameRecovery:
        """Unwind an ordinary False result without fabricating an exception."""
        reason = self._frame_rejection or "frame rejected"
        faults, rejected = self._release_frame_layers()
        recovery = RejectedFrameRecovery(reason, rejected, faults)
        self.last_rejected_frame = recovery
        return recovery

    def _release_framebuffer(self, fbo, layer: str) -> bool:
        accepted = fbo is None or fbo.release() is not False
        if not accepted:
            self.reject_frame(f"{layer} framebuffer release rejected")
        return accepted

    def release_layer(self, layer: str) -> bool:
        if layer == "background":
            fbo = self.background_fbo
        elif layer == "control":
            fbo = self.control_fbo
        elif layer == "dynamic":
            fbo = self.dynamic_fbo
        elif layer == "target":
            fbo = self.target_fbo
        else:
            fbo = self.overlay_fbo
        return self._release_framebuffer(fbo, layer)

    def upload_dynamic_image(
        self,
        functions,
        image: QImage,
        x: int,
        y: int,
    ) -> bool:
        """Replace a premultiplied RGBA region in the dynamic FBO.

        Some Windows OpenGL drivers make Qt's image paint path copy the
        destination into transparent source pixels.  A direct sub-image
        upload has exact replacement semantics, so zero-alpha pixels remain
        transparent instead of becoming a visible rectangular bounding box.
        """
        return self._upload_image_to_fbo(
            functions,
            self.dynamic_fbo,
            image,
            x,
            y,
        )

    def upload_target_region(
        self,
        functions,
        image: QImage,
        x: int,
        y: int,
    ) -> bool:
        """Replace a small premultiplied region of the isolated TargetHUD FBO.

        Same exact-replace semantics as ``upload_dynamic_image``: CPU-rasterized
        pixels (including true zero-alpha) overwrite driver residual from Qt's
        OpenGL paint engine.  Keep the region tight around the ring so this
        stays cheap every frame.
        """
        return self._upload_image_to_fbo(
            functions,
            self.target_fbo,
            image,
            x,
            y,
            sanitize_alpha=True,
        )

    def _clear_framebuffer(self, functions, fbo, layer: str) -> bool:
        if fbo is None:
            return False
        def clear() -> bool:
            if not fbo.bind():
                return False
            self.clear_current(functions, transparent=True)
            return True
        return self._run_pass(f"layer.clear.{layer}", clear,
            cleanup=(("framebuffer", lambda: self._release_framebuffer(fbo, layer)),))

    def clear_target_layer(self, functions) -> bool:
        """Clear the isolated TargetHUD layer and confirm its FBO release."""
        return self._clear_framebuffer(functions, self.target_fbo, "target")

    def clear_scene_layers(
        self, functions, layers: tuple[str, ...] = ("overlay", "dynamic", "target"),
    ) -> bool:
        """Clear modal storage; a bind or release rejection keeps restore pending."""
        mapping = {"overlay": self.overlay_fbo, "dynamic": self.dynamic_fbo, "target": self.target_fbo}
        self._blur_valid = False
        self._treated_blur_valid = False
        for name in layers:
            if not self._clear_framebuffer(functions, mapping.get(str(name)), str(name)):
                return False
        self._glass_commands.clear_frame()
        return True

    def begin_vision_layer(self, functions) -> bool:
        """Bind and clear the dedicated premultiplied vision wire layer."""
        def begin() -> bool:
            if self.vision_fbo is None or not self.vision_fbo.bind():
                self.reject_frame("vision framebuffer begin rejected")
                return False
            self.clear_current(functions, transparent=True)
            return True
        accepted = self._run_pass("layer.begin.vision", begin,
            on_reject=(("framebuffer", self.end_vision_layer),))
        if not accepted:
            self.reject_frame("vision framebuffer begin rejected")
        return accepted

    def end_vision_layer(self) -> bool:
        return self._release_framebuffer(self.vision_fbo, "vision")

    def clear_vision_layer(self, functions) -> bool:
        """Clear the vision FBO and reject any unsuccessful release."""
        return self._clear_framebuffer(functions, self.vision_fbo, "vision")

    def _upload_image_to_fbo(
        self,
        functions,
        fbo: QOpenGLFramebufferObject | None,
        image: QImage,
        x: int,
        y: int,
        *,
        sanitize_alpha: bool = False,
    ) -> bool:
        if fbo is None or image.isNull():
            return False
        upload = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
        width = upload.width()
        height = upload.height()
        if (
            width <= 0
            or height <= 0
            or x < 0
            or y < 0
            or x + width > self._size.width()
            or y + height > self._size.height()
        ):
            return False

        upload = upload.mirrored(False, True)
        texture_y = self._size.height() - y - height
        payload = bytes(upload.constBits())
        if sanitize_alpha:
            payload = sanitize_premultiplied_rgba_bytes(payload)
        functions.glActiveTexture(_GL_TEXTURE0)
        try:
            functions.glBindTexture(_GL_TEXTURE_2D, fbo.texture())
            functions.glPixelStorei(_GL_UNPACK_ALIGNMENT, 1)
            functions.glTexSubImage2D(
                _GL_TEXTURE_2D,
                0,
                x,
                texture_y,
                width,
                height,
                _GL_RGBA,
                _GL_UNSIGNED_BYTE,
                payload,
            )
        finally:
            functions.glBindTexture(_GL_TEXTURE_2D, 0)
            functions.glPixelStorei(_GL_UNPACK_ALIGNMENT, 4)
            functions.glPixelStorei(_GL_PACK_ALIGNMENT, 4)
            functions.glActiveTexture(_GL_TEXTURE0)
        return True

    @staticmethod
    def _restore_qt_texture_state(functions, *, clear_bindings: bool = False) -> None:
        """Return raw OpenGL texture state to the baseline QPainter expects."""
        if clear_bindings:
            for unit in range(8):
                functions.glActiveTexture(_GL_TEXTURE0 + unit)
                functions.glBindTexture(_GL_TEXTURE_2D, 0)
        functions.glActiveTexture(_GL_TEXTURE0)

    # Retain the established inspection/mutation aliases while the buffer owns
    # the lists and recording scope. No second copy of that state is maintained.
    @property
    def _static_glass_commands(self) -> list[GlassCommand]:
        return self._glass_commands.static

    @property
    def _frame_glass_commands(self) -> list[GlassCommand]:
        return self._glass_commands.frame

    @property
    def _glass_owner_stack(self) -> list[GlassOwner]:
        return self._glass_commands.owners

    @property
    def _recording_static_glass(self) -> bool:
        return self._glass_commands.recording_static

    @_recording_static_glass.setter
    def _recording_static_glass(self, value: bool) -> None:
        self._glass_commands.recording_static = value

    def begin_frame(self, *, rebuild_static_ui: bool = True) -> None:
        self._frame_rejection = ""
        self._glass_commands.begin_frame(rebuild_static_ui=rebuild_static_ui)

    def clear_static_glass(self) -> None:
        """页面切换为全动态场景时，丢弃上一页缓存的玻璃命令。"""
        self._glass_commands.clear_static()

    def clear_frame_glass(self) -> None:
        """立即丢弃本帧临时镜片，防止页面切换后旧命令再参与一次合成。"""
        self._glass_commands.clear_frame()

    def invalidate_transient_layers(self) -> None:
        """Forget every cache whose source changes across a modal scene.

        This method intentionally performs no OpenGL calls, so overlay close
        handlers can use it outside ``paintGL``.  The next frame repaints the
        static/dynamic FBOs and rebuilds both blur chains from the live HUD.
        """
        self._glass_commands.invalidate()
        self._blur_valid = False
        self._treated_blur_valid = False
        self._backdrop_treatment = BackdropTreatment()

    def begin_static_glass(self) -> None:
        """将后续镜片记录到可缓存的静态 UI 层。"""
        self._glass_commands.begin_static()

    def end_static_glass(self) -> None:
        self._glass_commands.end_static()

    @property
    def glass_owner(self) -> GlassOwner:
        return self._glass_commands.owner

    def push_glass_owner(self, owner: GlassOwner | str) -> None:
        if not isinstance(owner, GlassOwner):
            owner = GlassOwner(str(owner))
        self._glass_commands.push_owner(owner)

    def pop_glass_owner(self) -> None:
        self._glass_commands.pop_owner()

    def add_glass(
        self,
        rect: QRectF,
        radius: float,
        *,
        strength: float = 1.0,
        surface: str = "main",
        hover: float = 0.0,
        opacity: float = 1.0,
        material: LensMaterial | str | None = None,
        refraction_height_px: float | None = None,
        refraction_amount_px: float | None = None,
        chromatic_aberration: float | None = None,
        depth_effect: float | None = None,
        frost_amount: float = 0.0,
        vibrancy_amount: float = 0.0,
        clip_rect: QRectF | None = None,
        owner: GlassOwner | str | None = None,
    ) -> None:
        if rect.isEmpty():
            return
        if material is None:
            material = LensMaterial.STRONG if surface == "clear" else LensMaterial.REGULAR
        elif not isinstance(material, LensMaterial):
            try:
                material = LensMaterial(str(material))
            except ValueError:
                material = LensMaterial.REGULAR
        # Kyant GlassSlider 的基准是逻辑 12px / 16px。参数不能再按
        # 控件短边成倍放大，否则大卡片会变成失真的放大镜。
        defaults = {
            LensMaterial.STRONG: (12.0, -16.0, 1.0, 0.0),
            LensMaterial.REGULAR: (10.0, -13.0, 0.34, 0.18),
            LensMaterial.ISLAND: (12.0, -16.0, 0.72, 0.12),
        }
        default_height, default_amount, default_chromatic, default_depth = defaults[material]
        if owner is None:
            owner = self.glass_owner
        elif not isinstance(owner, GlassOwner):
            owner = GlassOwner(str(owner))
        command = GlassCommand(
            QRectF(rect),
            max(0.0, float(radius)),
            max(0.25, min(1.5, float(strength))),
            surface if surface in {"main", "sub", "clear"} else "main",
            max(0.0, min(1.0, float(hover))),
            max(0.0, min(1.0, float(opacity))),
            material,
            max(1.0, float(refraction_height_px if refraction_height_px is not None else default_height)),
            -abs(float(refraction_amount_px if refraction_amount_px is not None else default_amount)),
            max(0.0, min(2.0, float(chromatic_aberration if chromatic_aberration is not None else default_chromatic))),
            max(0.0, min(1.0, float(depth_effect if depth_effect is not None else default_depth))),
            max(0.0, min(1.0, float(frost_amount))),
            max(0.0, min(1.0, float(vibrancy_amount))),
            QRectF(clip_rect) if clip_rect is not None else None,
            owner,
        )
        self._glass_commands.append(command)

    def set_scene_uv_offset(self, x: float, y: float) -> None:
        self._scene_uv_offset = (float(x), float(y))

    def set_backdrop_treatment(
        self,
        *,
        active: bool,
        progress: float = 0.0,
        blur_radius_px: float = 4.0,
        dim_alpha: float = 0.4,
        include_overlay: bool = True,
        blur_kernel: str = "compact_gaussian",
    ) -> bool:
        """Publish one complete backdrop treatment at a frame boundary."""
        reveal = max(0.0, min(1.0, float(progress))) if active else 0.0
        treatment = BackdropTreatment(
            active=bool(active and reveal > 0.0001),
            progress=round(reveal, 5),
            blur_radius_px=round(max(0.0, float(blur_radius_px)), 4),
            dim_alpha=round(max(0.0, min(1.0, float(dim_alpha))), 5),
            include_overlay=bool(include_overlay),
            blur_kernel=(
                "jello_box20"
                if str(blur_kernel) == "jello_box20"
                else "compact_gaussian"
            ),
        )
        if treatment == self._backdrop_treatment:
            return False
        previous = self._backdrop_treatment
        self._backdrop_treatment = treatment
        blur_changed = (
            previous.active != treatment.active
            or previous.blur_radius_px != treatment.blur_radius_px
            or previous.include_overlay != treatment.include_overlay
            or previous.blur_kernel != treatment.blur_kernel
        )
        if blur_changed:
            self._treated_blur_valid = False
        return blur_changed

    def blur_needs_refresh(self, *, background_changed: bool) -> bool:
        return bool(background_changed or not self._blur_valid)

    def _render_blur_pair(
        self,
        functions,
        *,
        temp_fbo: QOpenGLFramebufferObject,
        output_fbo: QOpenGLFramebufferObject,
        radius_px: float,
        include_overlay: bool,
    ) -> bool:
        assert self.background_fbo is not None
        assert self.control_fbo is not None
        assert self.overlay_fbo is not None
        assert self.hud_glass_fbo is not None
        assert self.blur_program is not None

        if not temp_fbo.bind():
            return False
        functions.glViewport(0, 0, self._size.width(), self._size.height())
        functions.glDisable(_GL_BLEND)
        functions.glDisable(_GL_DEPTH_TEST)
        functions.glDisable(_GL_SCISSOR_TEST)
        functions.glDisable(_GL_FRAMEBUFFER_SRGB)
        if self.blur_program.bind() is False:
            return False
        functions.glActiveTexture(_GL_TEXTURE0)
        functions.glBindTexture(_GL_TEXTURE_2D, self.background_fbo.texture())
        functions.glUniform1i(self._uniform(self.blur_program, "sourceTex"), 0)
        functions.glActiveTexture(_GL_TEXTURE0 + 1)
        functions.glBindTexture(_GL_TEXTURE_2D, self.control_fbo.texture())
        functions.glUniform1i(self._uniform(self.blur_program, "controlTex"), 1)
        functions.glActiveTexture(_GL_TEXTURE0 + 2)
        functions.glBindTexture(_GL_TEXTURE_2D, self.overlay_fbo.texture())
        functions.glUniform1i(self._uniform(self.blur_program, "overlayTex"), 2)
        functions.glActiveTexture(_GL_TEXTURE0 + 3)
        functions.glBindTexture(_GL_TEXTURE_2D, self.hud_glass_fbo.texture())
        functions.glUniform1i(self._uniform(self.blur_program, "hudGlassTex"), 3)
        functions.glUniform1i(self._uniform(self.blur_program, "sourceMode"), 0)
        functions.glUniform2f(self._uniform(self.blur_program, "direction"), 1.0, 0.0)
        functions.glUniform2f(
            self._uniform(self.blur_program, "texel"),
            1.0 / max(1.0, float(self._size.width())),
            1.0 / max(1.0, float(self._size.height())),
        )
        functions.glUniform1f(self._uniform(self.blur_program, "blurRadiusPx"), float(radius_px))
        functions.glUniform1i(
            self._uniform(self.blur_program, "includeOverlay"),
            1 if include_overlay else 0,
        )
        if not self._draw_quad(functions, self.blur_program):
            return False
        if temp_fbo.release() is False:
            return False

        if not output_fbo.bind():
            self.blur_program.release()
            return False
        functions.glActiveTexture(_GL_TEXTURE0)
        functions.glBindTexture(_GL_TEXTURE_2D, temp_fbo.texture())
        functions.glUniform1i(self._uniform(self.blur_program, "sourceTex"), 0)
        functions.glUniform1i(self._uniform(self.blur_program, "sourceMode"), 1)
        functions.glUniform2f(self._uniform(self.blur_program, "direction"), 0.0, 1.0)
        if not self._draw_quad(functions, self.blur_program):
            return False
        self.blur_program.release()
        if output_fbo.release() is False:
            return False
        return True

    def _render_jello_blur_pair(
        self,
        functions,
        *,
        temp_fbo: QOpenGLFramebufferObject,
        output_fbo: QOpenGLFramebufferObject,
        radius_px: int,
        include_overlay: bool,
    ) -> bool:
        """Run Sigma 5.1.0's literal two-pass Minecraft box blur.

        ``JelloBlurJSON`` installs two ``blur`` passes with ``Radius=20``.
        Minecraft's bundled fragment shader samples every integer offset from
        ``-Radius`` through ``+Radius`` and divides by ``2*Radius+1``.  A dense
        kernel is required here: stretching the normal five-tap glass kernel
        produces visible copies of HUD text instead of the reference blur.
        """

        assert self.background_fbo is not None
        assert self.control_fbo is not None
        assert self.overlay_fbo is not None
        assert self.hud_glass_fbo is not None
        assert self.jello_blur_program is not None
        program = self.jello_blur_program
        radius = max(0, min(20, int(radius_px)))

        if not temp_fbo.bind():
            return False
        functions.glViewport(0, 0, self._size.width(), self._size.height())
        functions.glDisable(_GL_BLEND)
        functions.glDisable(_GL_DEPTH_TEST)
        functions.glDisable(_GL_SCISSOR_TEST)
        functions.glDisable(_GL_FRAMEBUFFER_SRGB)
        if program.bind() is False:
            return False
        functions.glActiveTexture(_GL_TEXTURE0)
        functions.glBindTexture(_GL_TEXTURE_2D, self.background_fbo.texture())
        functions.glUniform1i(self._uniform(program, "sourceTex"), 0)
        functions.glActiveTexture(_GL_TEXTURE0 + 1)
        functions.glBindTexture(_GL_TEXTURE_2D, self.control_fbo.texture())
        functions.glUniform1i(self._uniform(program, "controlTex"), 1)
        functions.glActiveTexture(_GL_TEXTURE0 + 2)
        functions.glBindTexture(_GL_TEXTURE_2D, self.overlay_fbo.texture())
        functions.glUniform1i(self._uniform(program, "overlayTex"), 2)
        functions.glActiveTexture(_GL_TEXTURE0 + 3)
        functions.glBindTexture(_GL_TEXTURE_2D, self.hud_glass_fbo.texture())
        functions.glUniform1i(self._uniform(program, "hudGlassTex"), 3)
        functions.glUniform1i(self._uniform(program, "sourceMode"), 0)
        functions.glUniform1i(self._uniform(program, "radiusPx"), radius)
        functions.glUniform1i(
            self._uniform(program, "includeOverlay"),
            1 if include_overlay else 0,
        )
        functions.glUniform2f(self._uniform(program, "direction"), 1.0, 0.0)
        functions.glUniform2f(
            self._uniform(program, "texel"),
            1.0 / max(1.0, float(self._size.width())),
            1.0 / max(1.0, float(self._size.height())),
        )
        if not self._draw_quad(functions, program):
            return False
        if temp_fbo.release() is False:
            return False

        if not output_fbo.bind():
            program.release()
            return False
        functions.glActiveTexture(_GL_TEXTURE0)
        functions.glBindTexture(_GL_TEXTURE_2D, temp_fbo.texture())
        functions.glUniform1i(self._uniform(program, "sourceTex"), 0)
        functions.glUniform1i(self._uniform(program, "sourceMode"), 1)
        functions.glUniform2f(self._uniform(program, "direction"), 0.0, 1.0)
        if not self._draw_quad(functions, program):
            return False
        program.release()
        if output_fbo.release() is False:
            return False
        return True

    def prepare_blur(self, functions, *, background_changed: bool) -> bool:
        """Build the scene/control blur sampled by native HUD lenses."""
        if not background_changed and self._blur_valid:
            return True
        if (
            self.background_fbo is None
            or self.control_fbo is None
            or self.overlay_fbo is None
            or self.hud_glass_fbo is None
            or self.blur_temp_fbo is None
            or self.blur_fbo is None
            or self.blur_program is None
        ):
            return False
        started = time.perf_counter()

        def render() -> bool:
            self._blur_valid = self._render_blur_pair(
                functions,
                temp_fbo=self.blur_temp_fbo,
                output_fbo=self.blur_fbo,
                radius_px=2.0 * self._device_pixel_ratio,
                include_overlay=False,
            )
            self.last_blur_ms = (time.perf_counter() - started) * 1000.0
            return self._blur_valid

        accepted = self._run_pass(
            "blur", render,
            on_reject=(
                ("program", self.blur_program.release),
                ("temporary", self.blur_temp_fbo.release),
                ("destination", self.blur_fbo.release),
            ),
            cleanup=(("qt-texture", lambda: self._restore_qt_texture_state(functions)),),
        )
        if not accepted:
            self._blur_valid = False
        return accepted

    def prepare_treated_backdrop(self, functions, *, background_changed: bool) -> bool:
        """Blur the already-composited native HUD for modal overlay backdrops."""
        treatment = self._backdrop_treatment
        if not treatment.active:
            self._treated_blur_valid = False
            return True
        if not background_changed and self._treated_blur_valid:
            return True
        if (
            self.treated_blur_temp_fbo is None
            or self.treated_blur_fbo is None
            or self.background_fbo is None
            or self.control_fbo is None
            or self.overlay_fbo is None
            or self.hud_glass_fbo is None
            or self.blur_program is None
        ):
            return False
        started = time.perf_counter()

        def render() -> bool:
            if treatment.blur_kernel == "jello_box20":
                # RenderUtil2.setShaderParamsRounded() feeds an integer radius
                # directly to Minecraft's full-resolution post shader.  It is
                # intentionally not multiplied by Qt's DPR: the reference
                # radius is measured in framebuffer pixels.
                self._treated_blur_valid = self._render_jello_blur_pair(
                    functions,
                    temp_fbo=self.treated_blur_temp_fbo,
                    output_fbo=self.treated_blur_fbo,
                    radius_px=int(max(0, min(20, round(treatment.blur_radius_px)))),
                    include_overlay=treatment.include_overlay,
                )
            else:
                self._treated_blur_valid = self._render_blur_pair(
                    functions,
                    temp_fbo=self.treated_blur_temp_fbo,
                    output_fbo=self.treated_blur_fbo,
                    radius_px=max(0.0, treatment.blur_radius_px * self._device_pixel_ratio),
                    include_overlay=treatment.include_overlay,
                )
            self.last_blur_ms += (time.perf_counter() - started) * 1000.0
            return self._treated_blur_valid

        accepted = self._run_pass(
            "backdrop.blur", render,
            on_reject=(
                ("program", self.blur_program.release),
                ("jello-program", lambda: self.jello_blur_program.release()
                 if self.jello_blur_program is not None else None),
                ("temporary", self.treated_blur_temp_fbo.release),
                ("destination", self.treated_blur_fbo.release),
            ),
            cleanup=(("qt-texture", lambda: self._restore_qt_texture_state(functions)),),
        )
        if not accepted:
            self._treated_blur_valid = False
        return accepted

    def render_glass(
        self,
        functions,
        *,
        include_static: bool = True,
        owners: set[GlassOwner] | frozenset[GlassOwner] | None = None,
        destination: str = "overlay",
        apply_backdrop_treatment: bool = True,
    ) -> bool:
        destination_fbo = self.hud_glass_fbo if destination == "hud" else self.glass_fbo
        if (
            destination_fbo is None
            or self.background_fbo is None
            or self.control_fbo is None
            or self.blur_fbo is None
            or self.treated_blur_fbo is None
            or self.hud_glass_fbo is None
            or self.overlay_fbo is None
            or self.glass_program is None
        ):
            return False
        started = time.perf_counter()

        def render() -> bool:
            if not destination_fbo.bind():
                return False
            self.clear_current(functions, transparent=True)
            glass_commands = self._glass_commands.select(include_static=include_static, owners=owners)
            if not glass_commands:
                if destination_fbo.release() is False:
                    return False
                self.last_glass_ms = (time.perf_counter() - started) * 1000.0
                return True

            functions.glEnable(_GL_BLEND)
            functions.glBlendEquation(_GL_FUNC_ADD)
            functions.glBlendFunc(_GL_ONE, _GL_ONE_MINUS_SRC_ALPHA)
            functions.glEnable(_GL_SCISSOR_TEST)
            if self.glass_program.bind() is False:
                return False
            functions.glActiveTexture(_GL_TEXTURE0)
            functions.glBindTexture(_GL_TEXTURE_2D, self.background_fbo.texture())
            functions.glUniform1i(self._uniform(self.glass_program, "backgroundTex"), 0)
            functions.glActiveTexture(_GL_TEXTURE0 + 1)
            functions.glBindTexture(_GL_TEXTURE_2D, self.control_fbo.texture())
            functions.glUniform1i(self._uniform(self.glass_program, "controlTex"), 1)
            functions.glActiveTexture(_GL_TEXTURE0 + 2)
            blur_texture = (
                self.treated_blur_fbo.texture()
                if apply_backdrop_treatment
                and self._backdrop_treatment.active
                and self._treated_blur_valid
                else self.blur_fbo.texture()
            )
            functions.glBindTexture(_GL_TEXTURE_2D, blur_texture)
            functions.glUniform1i(self._uniform(self.glass_program, "blurTex"), 2)
            functions.glActiveTexture(_GL_TEXTURE0 + 3)
            functions.glBindTexture(_GL_TEXTURE_2D, self.overlay_fbo.texture())
            functions.glUniform1i(self._uniform(self.glass_program, "overlayTex"), 3)
            functions.glActiveTexture(_GL_TEXTURE0 + 4)
            functions.glBindTexture(_GL_TEXTURE_2D, self.hud_glass_fbo.texture())
            functions.glUniform1i(self._uniform(self.glass_program, "hudGlassTex"), 4)
            functions.glUniform2f(
                self._uniform(self.glass_program, "viewport"),
                float(self._size.width()),
                float(self._size.height()),
            )
            functions.glUniform2f(
                self._uniform(self.glass_program, "sceneUvOffset"),
                *self._scene_uv_offset,
            )
            functions.glUniform1f(
                self._uniform(self.glass_program, "backdropTreatment"),
                float(
                    self._backdrop_treatment.progress
                    if apply_backdrop_treatment and self._backdrop_treatment.active
                    else 0.0
                ),
            )
            functions.glUniform1f(
                self._uniform(self.glass_program, "backdropDimAlpha"),
                float(
                    self._backdrop_treatment.dim_alpha
                    if apply_backdrop_treatment and self._backdrop_treatment.active
                    else 0.0
                ),
            )

            ratio = self._device_pixel_ratio
            for command in glass_commands:
                geometry = glass_draw_geometry(command, self._size, ratio)
                if geometry is None:
                    continue
                functions.glScissor(*geometry.scissor)
                functions.glUniform4f(
                    self._uniform(self.glass_program, "glassRect"),
                    *(float(value) for value in geometry.rect),
                )
                functions.glUniform1f(self._uniform(self.glass_program, "radius"), float(command.radius * ratio))
                functions.glUniform1f(self._uniform(self.glass_program, "strength"), float(command.strength))
                functions.glUniform1i(
                    self._uniform(self.glass_program, "surfaceKind"),
                    1 if command.surface == "sub" else 2 if command.surface == "clear" else 0,
                )
                functions.glUniform1f(self._uniform(self.glass_program, "hoverAmount"), float(command.hover))
                functions.glUniform1f(self._uniform(self.glass_program, "opacity"), float(command.opacity))
                functions.glUniform1i(
                    self._uniform(self.glass_program, "materialKind"),
                    0 if command.material == LensMaterial.REGULAR else 1 if command.material == LensMaterial.STRONG else 2,
                )
                functions.glUniform1f(
                    self._uniform(self.glass_program, "refractionHeightPx"),
                    float(command.refraction_height_px * ratio),
                )
                functions.glUniform1f(
                    self._uniform(self.glass_program, "refractionAmountPx"),
                    float(command.refraction_amount_px * ratio),
                )
                functions.glUniform1f(
                    self._uniform(self.glass_program, "chromaticAberration"),
                    float(command.chromatic_aberration),
                )
                functions.glUniform1f(self._uniform(self.glass_program, "depthEffect"), float(command.depth_effect))
                functions.glUniform1f(self._uniform(self.glass_program, "frostAmount"), float(command.frost_amount))
                functions.glUniform1f(self._uniform(self.glass_program, "vibrancyAmount"), float(command.vibrancy_amount))
                if not self._draw_quad(functions, self.glass_program):
                    return False

            self.glass_program.release()
            functions.glDisable(_GL_SCISSOR_TEST)
            functions.glDisable(_GL_BLEND)
            if destination_fbo.release() is False:
                return False
            self.last_glass_ms = (time.perf_counter() - started) * 1000.0
            return True

        return self._run_pass(
            "glass", render,
            on_reject=(
                ("program", self.glass_program.release),
                ("scissor", lambda: functions.glDisable(_GL_SCISSOR_TEST)),
                ("blend", lambda: functions.glDisable(_GL_BLEND)),
                ("destination", destination_fbo.release),
            ),
            cleanup=(("qt-texture", lambda: self._restore_qt_texture_state(functions)),),
        )

    def present(self, functions, default_fbo: int) -> bool:
        if self.frame_rejected:
            return False
        if not self.available or self.present_program is None or any(
            fbo is None
            for fbo in (
                self.background_fbo,
                self.control_fbo,
                self.blur_temp_fbo,
                self.blur_fbo,
                self.treated_blur_temp_fbo,
                self.treated_blur_fbo,
                self.hud_glass_fbo,
                self.glass_fbo,
                self.overlay_fbo,
                self.vision_fbo,
                self.dynamic_fbo,
                self.target_fbo,
            )
        ):
            return False
        assert self.background_fbo is not None
        assert self.control_fbo is not None
        assert self.blur_fbo is not None
        assert self.treated_blur_fbo is not None
        assert self.hud_glass_fbo is not None
        assert self.glass_fbo is not None
        assert self.overlay_fbo is not None
        assert self.vision_fbo is not None
        assert self.dynamic_fbo is not None
        assert self.target_fbo is not None
        def render() -> bool:
            started = time.perf_counter()
            functions.glBindFramebuffer(_GL_FRAMEBUFFER, default_fbo)
            functions.glViewport(0, 0, self._size.width(), self._size.height())
            functions.glDisable(_GL_DEPTH_TEST)
            functions.glDisable(_GL_SCISSOR_TEST)
            functions.glDisable(_GL_BLEND)
            functions.glDisable(_GL_FRAMEBUFFER_SRGB)
            functions.glClearColor(0.0, 0.0, 0.0, 1.0)
            functions.glClear(_GL_COLOR_BUFFER_BIT)
            if self.present_program.bind() is False:
                return False
            for unit, name, texture_id in (
                (0, "backgroundTex", self.background_fbo.texture()),
                (1, "controlTex", self.control_fbo.texture()),
                (2, "hudGlassTex", self.hud_glass_fbo.texture()),
                (3, "glassTex", self.glass_fbo.texture()),
                (4, "uiTex", self.overlay_fbo.texture()),
                (5, "visionTex", self.vision_fbo.texture()),
                (6, "dynamicTex", self.dynamic_fbo.texture()),
                (7, "targetTex", self.target_fbo.texture()),
                (
                    8,
                    "treatedBackdropTex",
                    self.treated_blur_fbo.texture()
                    if self._treated_blur_valid
                    else self.blur_fbo.texture(),
                ),
            ):
                functions.glActiveTexture(_GL_TEXTURE0 + unit)
                functions.glBindTexture(_GL_TEXTURE_2D, texture_id)
                functions.glUniform1i(self._uniform(self.present_program, name), unit)
            functions.glUniform2f(
                self._uniform(self.present_program, "sceneUvOffset"),
                *self._scene_uv_offset,
            )
            functions.glUniform1f(
                self._uniform(self.present_program, "backdropTreatment"),
                float(self._backdrop_treatment.progress if self._backdrop_treatment.active else 0.0),
            )
            functions.glUniform1f(
                self._uniform(self.present_program, "backdropDimAlpha"),
                float(self._backdrop_treatment.dim_alpha if self._backdrop_treatment.active else 0.0),
            )
            if not self._draw_quad(functions, self.present_program):
                return False
            self.present_program.release()
            self.last_present_ms = (time.perf_counter() - started) * 1000.0
            return True

        return self._run_pass(
            "present", render,
            on_reject=(("program", self.present_program.release),),
            # Present binds multiple texture units. Leave unit zero active so
            # Qt's next glyph-atlas upload cannot target a compositor unit.
            cleanup=(("qt-texture", lambda: self._restore_qt_texture_state(functions)),),
        )

    def _draw_quad(self, functions, program: QOpenGLShaderProgram) -> bool:
        failure = None
        accepted = False
        try:
            self.vertex_array.bind()  # Qt VAO bind is void; VBO bind is bool.
            if self.vertex_buffer.bind() is not False:
                program.enableAttributeArray(0)
                program.setAttributeBuffer(0, _GL_FLOAT, 0, 2, 2 * 4)
                functions.glDrawArrays(_GL_TRIANGLE_STRIP, 0, 4)
                accepted = True
        except Exception as exc:
            failure = exc
        finally:
            # A partial bind or one failed release cannot strand the other
            # GL state owners before QPainter's compatibility fallback.
            cleanup_accepted, cleanup = _attempt_gl_cleanup((
                ("quad.cleanup.attribute", lambda: program.disableAttributeArray(0)),
                ("quad.cleanup.buffer", self.vertex_buffer.release),
                ("quad.cleanup.array", self.vertex_array.release),
            ))
        errors = (("", failure), *cleanup) if failure is not None else cleanup
        if errors:
            raise _GlCleanupFailure(errors) from (failure or errors[0][1])
        return accepted and cleanup_accepted

    @staticmethod
    def _compile(vertex: str, fragment: str) -> QOpenGLShaderProgram:
        program = QOpenGLShaderProgram()
        try:
            if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex):
                raise RuntimeError(program.log())
            if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment):
                raise RuntimeError(program.log())
            if not program.link():
                raise RuntimeError(program.log())
        except Exception as error:
            # The factory cannot return this candidate, but the initializer
            # must retain it before independently retiring every program.
            raise _GlProgramBuildFailure(program, error) from error
        return program

    @staticmethod
    def _vertex_shader() -> str:
        return """
            #version 330
            layout(location = 0) in vec2 position;
            out vec2 uv;
            void main() {
                uv = position * 0.5 + vec2(0.5);
                gl_Position = vec4(position, 0.0, 1.0);
            }
        """

    @classmethod
    def _build_present_program(cls) -> QOpenGLShaderProgram:
        return cls._compile(cls._vertex_shader(), """
            #version 330
            in vec2 uv;
            uniform sampler2D backgroundTex;
            uniform sampler2D controlTex;
            uniform sampler2D hudGlassTex;
            uniform sampler2D glassTex;
            uniform sampler2D uiTex;
            uniform sampler2D visionTex;
            uniform sampler2D dynamicTex;
            uniform sampler2D targetTex;
            uniform sampler2D treatedBackdropTex;
            uniform vec2 sceneUvOffset;
            uniform float backdropTreatment;
            uniform float backdropDimAlpha;
            out vec4 fragColor;
            vec4 validPremultiplied(vec4 sampleColor) {
                sampleColor.a = clamp(sampleColor.a, 0.0, 1.0);
                sampleColor.rgb = clamp(sampleColor.rgb, vec3(0.0), vec3(sampleColor.a));
                return sampleColor;
            }
            void main() {
                vec3 background = texture(
                    backgroundTex,
                    clamp(uv + sceneUvOffset, vec2(0.001), vec2(0.999))
                ).rgb;
                vec4 control = validPremultiplied(texture(controlTex, uv));
                background = control.rgb + background * (1.0 - control.a);
                vec4 hudGlass = validPremultiplied(texture(hudGlassTex, uv));
                vec3 hudScene = hudGlass.rgb + background * (1.0 - hudGlass.a);
                vec4 glass = validPremultiplied(texture(glassTex, uv));
                vec4 ui = validPremultiplied(texture(uiTex, uv));
                if (backdropTreatment > 0.0001) {
                    vec3 rawBackdrop = ui.rgb + hudScene * (1.0 - ui.a);
                    vec3 treatedBackdrop = texture(treatedBackdropTex, uv).rgb
                        * (1.0 - clamp(backdropDimAlpha, 0.0, 1.0));
                    background = mix(
                        rawBackdrop,
                        treatedBackdrop,
                        clamp(backdropTreatment, 0.0, 1.0)
                    );
                    ui = vec4(0.0);
                    hudScene = background;
                }
                vec4 visionUi = validPremultiplied(texture(visionTex, uv));
                vec4 dynamicUi = validPremultiplied(texture(dynamicTex, uv));
                vec4 targetUi = validPremultiplied(texture(targetTex, uv));
                vec3 baseHud = ui.rgb + hudScene * (1.0 - ui.a);
                vec3 baseUi = glass.rgb + baseHud * (1.0 - glass.a);
                vec3 visionScene = visionUi.rgb + baseUi * (1.0 - visionUi.a);
                vec3 motionUi = dynamicUi.rgb + visionScene * (1.0 - dynamicUi.a);
                vec3 color = targetUi.rgb + motionUi * (1.0 - targetUi.a);
                fragColor = vec4(color, 1.0);
            }
        """)

    @classmethod
    def _build_blur_program(cls) -> QOpenGLShaderProgram:
        return cls._compile(cls._vertex_shader(), """
            #version 330
            in vec2 uv;
            uniform sampler2D sourceTex;
            uniform sampler2D controlTex;
            uniform sampler2D overlayTex;
            uniform sampler2D hudGlassTex;
            uniform vec2 texel;
            uniform vec2 direction;
            uniform float blurRadiusPx;
            uniform int includeOverlay;
            uniform int sourceMode;
            out vec4 fragColor;
            vec3 sourceAt(vec2 p) {
                vec2 sampleUv = clamp(p, vec2(0.001), vec2(0.999));
                if (sourceMode != 0) {
                    return texture(sourceTex, sampleUv).rgb;
                }
                vec3 scene = texture(sourceTex, sampleUv).rgb;
                vec4 control = texture(controlTex, sampleUv);
                vec3 combined = control.rgb + scene * (1.0 - control.a);
                if (includeOverlay != 0) {
                    vec4 hudGlass = texture(hudGlassTex, sampleUv);
                    vec4 overlay = texture(overlayTex, sampleUv);
                    combined = hudGlass.rgb + combined * (1.0 - hudGlass.a);
                    combined = overlay.rgb + combined * (1.0 - overlay.a);
                }
                return combined;
            }
            void main() {
                float sampleScale = max(0.0, blurRadiusPx) / 3.23076923;
                vec2 stepUv = texel * direction * sampleScale;
                vec3 color = sourceAt(uv) * 0.22702703;
                color += sourceAt(uv + stepUv * 1.38461538) * 0.31621622;
                color += sourceAt(uv - stepUv * 1.38461538) * 0.31621622;
                color += sourceAt(uv + stepUv * 3.23076923) * 0.07027027;
                color += sourceAt(uv - stepUv * 3.23076923) * 0.07027027;
                fragColor = vec4(color, 1.0);
            }
        """)

    @classmethod
    def _build_jello_blur_program(cls) -> QOpenGLShaderProgram:
        # Port of the ``assets/minecraft/shaders/program/blur.fsh`` embedded in
        # sigma-jello-5.1.0.jar.  The source composition is the host equivalent
        # of Minecraft's main framebuffer before that post chain is applied.
        return cls._compile(cls._vertex_shader(), """
            #version 330
            in vec2 uv;
            uniform sampler2D sourceTex;
            uniform sampler2D controlTex;
            uniform sampler2D overlayTex;
            uniform sampler2D hudGlassTex;
            uniform vec2 texel;
            uniform vec2 direction;
            uniform int radiusPx;
            uniform int includeOverlay;
            uniform int sourceMode;
            out vec4 fragColor;
            vec3 sourceAt(vec2 p) {
                vec2 sampleUv = clamp(p, vec2(0.001), vec2(0.999));
                if (sourceMode != 0) {
                    return texture(sourceTex, sampleUv).rgb;
                }
                vec3 scene = texture(sourceTex, sampleUv).rgb;
                vec4 control = texture(controlTex, sampleUv);
                vec3 combined = control.rgb + scene * (1.0 - control.a);
                if (includeOverlay != 0) {
                    vec4 hudGlass = texture(hudGlassTex, sampleUv);
                    vec4 overlay = texture(overlayTex, sampleUv);
                    combined = hudGlass.rgb + combined * (1.0 - hudGlass.a);
                    combined = overlay.rgb + combined * (1.0 - overlay.a);
                }
                return combined;
            }
            void main() {
                int radius = clamp(radiusPx, 0, 20);
                vec3 blurred = vec3(0.0);
                for (int offset = -20; offset <= 20; ++offset) {
                    if (abs(offset) <= radius) {
                        blurred += sourceAt(
                            uv + texel * direction * float(offset)
                        );
                    }
                }
                fragColor = vec4(
                    blurred / float(radius * 2 + 1),
                    1.0
                );
            }
        """)

    @classmethod
    def _build_lens_program(cls) -> QOpenGLShaderProgram:
        """构建唯一的 Combined Backdrop 液态镜片着色器。"""
        return cls._compile(cls._vertex_shader(), """
            #version 330
            in vec2 uv;
            uniform sampler2D backgroundTex;
            uniform sampler2D controlTex;
            uniform sampler2D blurTex;
            uniform sampler2D overlayTex;
            uniform sampler2D hudGlassTex;
            uniform vec2 viewport;
            uniform vec2 sceneUvOffset;
            uniform vec4 glassRect;
            uniform float radius;
            uniform float strength;
            uniform int surfaceKind;
            uniform int materialKind;
            uniform float hoverAmount;
            uniform float opacity;
            uniform float refractionHeightPx;
            uniform float refractionAmountPx;
            uniform float chromaticAberration;
            uniform float depthEffect;
            uniform float frostAmount;
            uniform float vibrancyAmount;
            uniform float backdropTreatment;
            uniform float backdropDimAlpha;
            out vec4 fragColor;

            vec3 applyVibrancy(vec3 color) {
                // AndroidLiquidGlass-kmp's vibrancy() is a saturation=1.5
                // color-control matrix. The amount lets a sheet fade the
                // same material in with its pull-down progress.
                float saturation = 1.0 + 0.5 * clamp(vibrancyAmount, 0.0, 1.0);
                float invSaturation = 1.0 - saturation;
                float r = 0.213 * invSaturation;
                float g = 0.715 * invSaturation;
                float b = 0.072 * invSaturation;
                return vec3(
                    dot(color, vec3(r + saturation, g, b)),
                    dot(color, vec3(r, g + saturation, b)),
                    dot(color, vec3(r, g, b + saturation))
                );
            }

            vec4 combinedAt(vec2 position) {
                vec2 sampleUv = clamp(position, vec2(0.001), vec2(0.999));
                vec4 scene = texture(
                    backgroundTex,
                    clamp(sampleUv + sceneUvOffset, vec2(0.001), vec2(0.999))
                );
                vec4 control = texture(controlTex, sampleUv);
                vec4 combined = control + scene * (1.0 - control.a);
                if (backdropTreatment > 0.0001) {
                    vec4 hudGlass = texture(hudGlassTex, sampleUv);
                    vec4 overlay = texture(overlayTex, sampleUv);
                    vec4 rawBackdrop = hudGlass + combined * (1.0 - hudGlass.a);
                    rawBackdrop = overlay + rawBackdrop * (1.0 - overlay.a);
                    vec4 treated = texture(blurTex, sampleUv);
                    treated.rgb *= 1.0 - clamp(backdropDimAlpha, 0.0, 1.0);
                    combined = mix(
                        rawBackdrop,
                        treated,
                        clamp(backdropTreatment, 0.0, 1.0)
                    );
                }
                combined.rgb = applyVibrancy(combined.rgb);
                return combined;
            }

            float sdRoundedRect(vec2 position, vec2 halfSize, float cornerRadius) {
                vec2 q = abs(position) - (halfSize - vec2(cornerRadius));
                return length(max(q, vec2(0.0))) - cornerRadius
                    + min(max(q.x, q.y), 0.0);
            }

            vec2 gradSdRoundedRect(vec2 position, vec2 halfSize, float cornerRadius) {
                vec2 corner = abs(position) - (halfSize - vec2(cornerRadius));
                if (corner.x >= 0.0 || corner.y >= 0.0) {
                    return sign(position) * normalize(max(corner, vec2(0.00001)));
                }
                float horizontal = step(corner.y, corner.x);
                return sign(position) * vec2(horizontal, 1.0 - horizontal);
            }

            float circleMap(float value) {
                float x = clamp(value, 0.0, 1.0);
                return 1.0 - sqrt(max(0.0, 1.0 - x * x));
            }

            vec4 sevenBandSample(vec2 refractedUv, vec2 dispersedUv) {
                vec4 red = combinedAt(refractedUv + dispersedUv);
                vec4 orange = combinedAt(refractedUv + dispersedUv * (2.0 / 3.0));
                vec4 yellow = combinedAt(refractedUv + dispersedUv * (1.0 / 3.0));
                vec4 green = combinedAt(refractedUv);
                vec4 cyan = combinedAt(refractedUv - dispersedUv * (1.0 / 3.0));
                vec4 blue = combinedAt(refractedUv - dispersedUv * (2.0 / 3.0));
                vec4 purple = combinedAt(refractedUv - dispersedUv);

                vec4 color = vec4(0.0);
                color.r += red.r / 3.5;
                color.a += red.a / 7.0;
                color.r += orange.r / 3.5;
                color.g += orange.g / 7.0;
                color.a += orange.a / 7.0;
                color.r += yellow.r / 3.5;
                color.g += yellow.g / 3.5;
                color.a += yellow.a / 7.0;
                color.g += green.g / 3.5;
                color.a += green.a / 7.0;
                color.g += cyan.g / 3.5;
                color.b += cyan.b / 3.0;
                color.a += cyan.a / 7.0;
                color.b += blue.b / 3.0;
                color.a += blue.a / 7.0;
                color.r += purple.r / 7.0;
                color.b += purple.b / 3.0;
                color.a += purple.a / 7.0;
                return color;
            }

            void main() {
                // 先把 OpenGL 左下原点转换为源码使用的左上局部坐标。
                vec2 localGl = gl_FragCoord.xy - glassRect.xy;
                vec2 localTopLeft = vec2(localGl.x, glassRect.w - localGl.y);
                vec2 halfSize = glassRect.zw * 0.5;
                vec2 centered = localTopLeft - halfSize;
                float safeRadius = min(radius, min(halfSize.x, halfSize.y));
                float sd = sdRoundedRect(centered, halfSize, safeRadius);
                float coverage = 1.0 - smoothstep(-0.55, 0.85, sd);
                if (coverage <= 0.001) {
                    discard;
                }

                vec2 sourceUv = gl_FragCoord.xy / viewport;
                float inside = max(-sd, 0.0);
                float lensHeight = min(
                    max(1.0, refractionHeightPx),
                    max(1.0, min(halfSize.x, halfSize.y))
                );

                // The source control-center uses a shallow 4dp blur inside
                // the otherwise clear center of its pull-down surface.
                if (inside >= lensHeight) {
                    vec4 centerColor = combinedAt(sourceUv);
                    if (frostAmount > 0.001) {
                        // Keep the source blur shallow. This is not a modal
                        // frosted-card treatment; it only protects dense copy
                        // while the live scene remains legible through glass.
                        vec2 texel = 1.0 / viewport;
                        float frostRadius = frostAmount * 10.0;
                        vec2 uvBase = clamp(sourceUv + sceneUvOffset, vec2(0.001), vec2(0.999));
                        vec3 blurred = texture(blurTex, uvBase).rgb * 0.16;
                        // Ring 1 (radius = frostRadius)
                        blurred += texture(blurTex, clamp(uvBase + vec2(texel.x, 0.0) * frostRadius, vec2(0.001), vec2(0.999))).rgb * 0.08;
                        blurred += texture(blurTex, clamp(uvBase - vec2(texel.x, 0.0) * frostRadius, vec2(0.001), vec2(0.999))).rgb * 0.08;
                        blurred += texture(blurTex, clamp(uvBase + vec2(0.0, texel.y) * frostRadius, vec2(0.001), vec2(0.999))).rgb * 0.08;
                        blurred += texture(blurTex, clamp(uvBase - vec2(0.0, texel.y) * frostRadius, vec2(0.001), vec2(0.999))).rgb * 0.08;
                        blurred += texture(blurTex, clamp(uvBase + texel * frostRadius * 0.707, vec2(0.001), vec2(0.999))).rgb * 0.06;
                        blurred += texture(blurTex, clamp(uvBase - texel * frostRadius * 0.707, vec2(0.001), vec2(0.999))).rgb * 0.06;
                        blurred += texture(blurTex, clamp(uvBase + vec2(texel.x, -texel.y) * frostRadius * 0.707, vec2(0.001), vec2(0.999))).rgb * 0.06;
                        blurred += texture(blurTex, clamp(uvBase + vec2(-texel.x, texel.y) * frostRadius * 0.707, vec2(0.001), vec2(0.999))).rgb * 0.06;
                        // Ring 2 (radius = frostRadius * 2)
                        blurred += texture(blurTex, clamp(uvBase + vec2(texel.x, 0.0) * frostRadius * 2.0, vec2(0.001), vec2(0.999))).rgb * 0.04;
                        blurred += texture(blurTex, clamp(uvBase - vec2(texel.x, 0.0) * frostRadius * 2.0, vec2(0.001), vec2(0.999))).rgb * 0.04;
                        blurred += texture(blurTex, clamp(uvBase + vec2(0.0, texel.y) * frostRadius * 2.0, vec2(0.001), vec2(0.999))).rgb * 0.04;
                        blurred += texture(blurTex, clamp(uvBase - vec2(0.0, texel.y) * frostRadius * 2.0, vec2(0.001), vec2(0.999))).rgb * 0.04;
                        blurred += texture(blurTex, clamp(uvBase + texel * frostRadius * 1.414, vec2(0.001), vec2(0.999))).rgb * 0.03;
                        blurred += texture(blurTex, clamp(uvBase - texel * frostRadius * 1.414, vec2(0.001), vec2(0.999))).rgb * 0.03;
                        blurred += texture(blurTex, clamp(uvBase + vec2(texel.x, -texel.y) * frostRadius * 1.414, vec2(0.001), vec2(0.999))).rgb * 0.03;
                        blurred += texture(blurTex, clamp(uvBase + vec2(-texel.x, texel.y) * frostRadius * 1.414, vec2(0.001), vec2(0.999))).rgb * 0.03;
                        // Vibrancy: boost saturation to 1.5x (AndroidLiquidGlass-kmp vibrancy()).
                        float lum = dot(blurred, vec3(0.2126, 0.7152, 0.0722));
                        blurred = mix(vec3(lum), blurred, 1.5);
                        // The control-center surface carries only a five
                        // percent container tint. Keep the blurred source
                        // bright enough for copy to remain legible instead of
                        // turning a rich camera feed into a dark modal.
                        blurred *= 0.96;
                        centerColor.rgb = mix(centerColor.rgb, blurred, frostAmount);
                    }
                    float alpha = centerColor.a * coverage * opacity;
                    fragColor = vec4(centerColor.rgb * alpha, alpha);
                    return;
                }

                float signedDistance = min(sd, 0.0);
                float edgeDistance = circleMap(1.0 - (-signedDistance / lensHeight));
                float gradientRadius = min(
                    safeRadius * 1.5,
                    min(halfSize.x, halfSize.y)
                );
                vec2 gradientTopLeft = gradSdRoundedRect(
                    centered,
                    halfSize,
                    gradientRadius
                );
                vec2 radialTopLeft = normalize(centered + vec2(0.00001));
                vec2 directionTopLeft = normalize(
                    gradientTopLeft + depthEffect * radialTopLeft
                );
                // 方向向量转换回 OpenGL 纹理坐标，避免靠象限符号猜方向。
                vec2 directionUv = vec2(directionTopLeft.x, -directionTopLeft.y);
                float distancePx = edgeDistance * refractionAmountPx * strength;
                vec2 refractedUv = sourceUv + directionUv * distancePx / viewport;

                float dispersionIntensity = chromaticAberration
                    * ((centered.x * centered.y)
                       / max(1.0, halfSize.x * halfSize.y));
                vec2 dispersedUv = directionUv * distancePx
                    * dispersionIntensity / viewport;
                vec4 lens = chromaticAberration > 0.001
                    ? sevenBandSample(refractedUv, dispersedUv)
                    : combinedAt(refractedUv);

                // ControlCenterContent uses HighlightStyle.Default with a
                // 0.5dp stroke, 0.25dp blur and falloff=2. Keep that narrow,
                // directional highlight on every material, including the
                // strong tiles. It is a border response, never a sweep.
                vec2 highlightNormal = normalize(vec2(0.70710678, 0.70710678));
                float highlightDirection = pow(
                    abs(dot(gradientTopLeft, highlightNormal)),
                    2.0
                );
                float highlightStroke = 1.0 - smoothstep(0.25, 0.95, abs(sd));
                lens.rgb += vec3(1.0) * highlightDirection * highlightStroke
                    * (0.50 + hoverAmount * 0.08);

                // Strong material matches the source Control Center: the
                // combined backdrop, vibrancy, refraction and highlight only.
                if (materialKind == 1) {
                    float alpha = lens.a * coverage * opacity;
                    fragColor = vec4(lens.rgb * alpha, alpha);
                    return;
                }

                // 文字密集材质仍使用同一折射模型，只加入极轻的局部柔化。
                float edge = clamp(edgeDistance, 0.0, 1.0);
                float blurMix = materialKind == 2 ? 0.055 : 0.035;
                vec3 softened = texture(
                    blurTex,
                    clamp(refractedUv + sceneUvOffset, vec2(0.001), vec2(0.999))
                ).rgb;
                lens.rgb = mix(lens.rgb, softened, blurMix * edge);

                float alpha = lens.a * coverage * opacity;
                fragColor = vec4(lens.rgb * alpha, alpha);
            }
        """)

    def _destroy_fbos(self) -> None:
        self._framebuffers.clear()
        self._size = QSize()
        self._blur_valid = False
        self._treated_blur_valid = False

    def release(self) -> None:
        """Retire every independent GL owner, retaining any failed program."""
        self.available = False

        def destroy_buffer() -> None:
            if self.vertex_buffer.isCreated():
                self.vertex_buffer.destroy()

        def destroy_array() -> None:
            if self.vertex_array.isCreated():
                self.vertex_array.destroy()

        def release_program(name: str) -> None:
            program = getattr(self, name)
            if program is not None:
                program.removeAllShaders()
                setattr(self, name, None)

        actions = (
            ("release.framebuffers", self._destroy_fbos),
            ("release.vertex-buffer", destroy_buffer),
            ("release.vertex-array", destroy_array),
            *((f"release.{name}", lambda name=name: release_program(name)) for name in (
                "present_program", "blur_program", "jello_blur_program", "glass_program",
            )),
        )
        _, failures = _attempt_gl_cleanup(actions)
        self._release_pending = bool(failures)
        self._uniform_cache.clear()
        if failures:
            raise _GlCleanupFailure(failures) from failures[0][1]
