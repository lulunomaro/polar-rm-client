"""Actual specialized render/release methods with inert native handles.

The success trace pins uniforms, geometry payloads, draw order and cleanup;
it is not a native-driver pixel qualification.
"""
from pathlib import Path
import hashlib
import json
import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from rm_client.ui.startup_gl import StartupGpuCompositor, TextureOrigin, normalize_image_for_texture
from rm_client.ui.quick_action_wheel_gpu import QuickActionWheelGpuRenderer, QuickActionWheelCommand
from rm_client.ui.island_icon_gpu import IslandIconGpuRenderer, IslandIconCommand
from rm_client.ui.vision_wire_gpu import VisionWireGpuRenderer, VisionWireBatch

APP = QApplication.instance() or QApplication([])
RENDERERS = (StartupGpuCompositor, QuickActionWheelGpuRenderer, IslandIconGpuRenderer, VisionWireGpuRenderer)


def make_renderer(cls, *, previous=True):
    renderer = cls()
    renderer.available = True
    trace = Mock()
    renderer.program = Mock(bind=Mock(return_value=True), uniformLocation=Mock(return_value=3))
    renderer.vertex_array = Mock(bind=Mock(return_value=None), release=Mock(return_value=None))
    buffer = Mock(bind=Mock(return_value=True), release=Mock(return_value=None))
    renderer.program.release.return_value = None
    functions = Mock()
    for name, value in (("program", renderer.program), ("vao", renderer.vertex_array), ("buffer", buffer), ("gl", functions)):
        trace.attach_mock(value, name)
    if cls is StartupGpuCompositor:
        renderer.vertex_buffer = buffer
        renderer.current = Mock(bind=Mock(return_value=None), release=Mock(return_value=None))
        renderer.current_rect = QRectF(-10, -5, 120, 90)
        renderer.previous = Mock(bind=Mock(return_value=None), release=Mock(return_value=None)) if previous else None
        renderer.previous_rect = QRectF(-8, -4, 116, 88) if previous else QRectF()
        renderer._position_attribute = 0
        renderer.frame_started = 99.875
        renderer.frame_interval = .25
        renderer.dim = .3
        renderer._uniforms = {name: index for index, name in enumerate(("currentTex", "previousTex", "currentUv", "previousUv", "blend", "dim", "hasPrevious", "flipY"))}
        trace.attach_mock(renderer.current, "current")
        if previous:
            trace.attach_mock(renderer.previous, "previous")
        render = lambda: renderer.render(functions, QRectF(0, 0, 100, 80))
    else:
        renderer.buffer = buffer
        if cls is QuickActionWheelGpuRenderer:
            render = lambda: renderer.render(functions, QuickActionWheelCommand(50, 40, 20, 10, 6, 1, .8), 100, 80, 1.5, background_texture=11, blur_texture=12)
        elif cls is IslandIconGpuRenderer:
            commands = [IslandIconCommand(QRectF(2, 3, 18, 16), "info", QColor(128, 160, 220, 180))]
            render = lambda: renderer.render(functions, commands, 100, 80, 1.5)
        else:
            batch = VisionWireBatch()
            batch.line(QPointF(1, 2), QPointF(10, 12), QColor(128, 160, 220, 180), 1.5)
            render = lambda: renderer.render(functions, batch, 100, 80, 1.5)
    return renderer, functions, buffer, render, trace


def normalized_trace(trace):
    def normalize(value):
        if isinstance(value, bytes):
            return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        return value
    return [{"call": name, "args": [normalize(arg) for arg in args], "kwargs": kwargs}
            for name, args, kwargs in trace.mock_calls]


class SpecializedGlRenderTests(unittest.TestCase):
    def test_successful_render_preserves_commands_except_corrected_texture_unit_release(self):
        baseline = json.loads((ROOT / "tests/fixtures/specialized_gl_trace.json").read_text())
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__), patch("rm_client.ui.startup_gl.time.perf_counter", return_value=100.):
                renderer, functions, buffer, render, trace = make_renderer(cls)
                self.assertTrue(render())
                expected = baseline[cls.__name__]
                if cls is StartupGpuCompositor:
                    # Keep the pre-refactor fixture frozen. The reviewed bugfix
                    # changes only release parameters: Qt no-arg release targets
                    # the active unit, so the old pair cleared unit1 twice.
                    releases = {"previous.release": [1], "current.release": [0]}
                    self.assertEqual([entry["call"] for entry in expected if entry["call"] in releases],
                                     ["previous.release", "current.release"])
                    for entry in expected:
                        if entry["call"] in releases:
                            self.assertEqual(entry["args"], [])
                            entry["args"] = releases[entry["call"]]
                self.assertEqual(normalized_trace(trace), expected)

    def test_false_program_or_buffer_bind_rejects_without_drawing(self):
        for cls in RENDERERS:
            for name in ("program", "buffer"):
                with self.subTest(renderer=cls.__name__, boundary=name):
                    renderer, functions, buffer, render, trace = make_renderer(cls)
                    handle = renderer.program if name == "program" else buffer
                    handle.bind.return_value = False
                    self.assertFalse(render())
                    functions.glDrawArrays.assert_not_called()
                    handle.release.assert_not_called()

    def test_draw_fault_unwinds_each_admitted_owner_independently(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, trace = make_renderer(cls)
                functions.glDrawArrays.side_effect = TypeError("original draw defect")
                buffer.release.side_effect = OSError("buffer cleanup defect")
                self.assertFalse(render())
                buffer.release.assert_called_once()
                renderer.vertex_array.release.assert_called_once()
                renderer.program.release.assert_called_once()
                renderer.program.disableAttributeArray.assert_called()
                self.assertIn("original draw defect", renderer.last_error)
                if cls is StartupGpuCompositor:
                    renderer.current.release.assert_called_once()
                    renderer.previous.release.assert_called_once()

    def test_one_attribute_cleanup_fault_does_not_skip_other_cleanup(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, trace = make_renderer(cls)
                renderer.program.disableAttributeArray.side_effect = OSError("attribute cleanup defect")
                self.assertFalse(render())
                buffer.release.assert_called_once()
                renderer.vertex_array.release.assert_called_once()
                renderer.program.release.assert_called_once()
                self.assertEqual(renderer.program.disableAttributeArray.call_count, 1 if cls is StartupGpuCompositor else 2 if cls is QuickActionWheelGpuRenderer else 4)

    def test_void_none_bind_release_contracts_are_accepted(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, trace = make_renderer(cls)
                self.assertTrue(render())
                self.assertEqual(functions.glDrawArrays.call_count, 1)

    def test_rejections_have_no_exception_trace_and_only_release_admitted_handles(self):
        for cls in RENDERERS:
            for name in ("program", "buffer"):
                with self.subTest(renderer=cls.__name__, boundary=name):
                    renderer, functions, buffer, render, trace = make_renderer(cls)
                    (renderer.program if name == "program" else buffer).bind.return_value = False
                    with patch("rm_client.ui.gl_resources._LOG") as logger:
                        self.assertFalse(render())
                    result = renderer.resources.last_result
                    self.assertIn(name + ".bind", result.rejection)
                    self.assertIsNone(result.primary_fault)
                    self.assertFalse(result.cleanup_faults)
                    self.assertEqual(renderer.resources.diagnostics.faults, 0)
                    logger.error.assert_not_called()
                    self.assertEqual(renderer.vertex_array.release.call_count, int(cls is StartupGpuCompositor or name == "buffer"))
                    self.assertEqual(renderer.program.release.call_count, int(name == "buffer"))
                    buffer.release.assert_not_called()

    def test_typed_primary_cause_and_each_cleanup_failure_are_separate(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, trace = make_renderer(cls)
                def fail_draw(*args):
                    try:
                        raise ValueError("invalid caller geometry")
                    except ValueError as cause:
                        raise TypeError("original draw defect") from cause
                functions.glDrawArrays.side_effect = fail_draw
                buffer.release.side_effect = OSError("buffer release defect")
                renderer.vertex_array.release.side_effect = RuntimeError("array release defect")
                with patch("rm_client.ui.gl_resources._LOG") as logger:
                    self.assertFalse(render())
                result = renderer.resources.last_result
                self.assertEqual((result.primary_fault.error_type, result.primary_fault.cause_type), ("TypeError", "ValueError"))
                self.assertEqual([fault.error_type for fault in result.cleanup_faults], ["OSError", "RuntimeError"])
                self.assertEqual(logger.error.call_count, 3)
                primary_trace = logger.error.call_args_list[0].kwargs["exc_info"]
                self.assertIs(primary_trace[0], TypeError)
                self.assertIsNotNone(primary_trace[2])
                self.assertIn("original draw defect", renderer.last_error)
                self.assertNotIn("buffer release defect", renderer.last_error)

    def test_partial_attribute_setup_releases_only_enabled_attributes(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, trace = make_renderer(cls)
                renderer.program.setAttributeBuffer.side_effect = TypeError("attribute format defect")
                self.assertFalse(render())
                renderer.program.disableAttributeArray.assert_called_once_with(0)
                functions.glDrawArrays.assert_not_called()
                buffer.release.assert_called_once()
                renderer.vertex_array.release.assert_called_once()
                renderer.program.release.assert_called_once()

    def test_state_cleanup_failure_cannot_escape_or_skip_later_resets(self):
        for cls in RENDERERS[1:]:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, trace = make_renderer(cls)
                functions.glDrawArrays.side_effect = TypeError("original draw defect")
                def disable(capability):
                    if capability == 0x0BE2:
                        raise OSError("blend cleanup defect")
                functions.glDisable.side_effect = disable
                self.assertFalse(render())
                self.assertEqual(functions.glDisable.call_args_list[-1].args, (0x0B44,))
                self.assertIn("original draw defect", renderer.last_error)
                self.assertEqual(renderer.resources.last_result.cleanup_faults[0].error_type, "OSError")
                self.assertTrue(renderer.resources.last_result.cleanup_faults[0].phase.endswith("cleanup.disable_blend"))

    def test_repeated_faults_keep_latest_evidence_and_throttle_trace(self):
        renderer, functions, buffer, render, trace = make_renderer(VisionWireGpuRenderer)
        functions.glDrawArrays.side_effect = TypeError("same frame defect")
        with patch("rm_client.ui.gl_resources._LOG") as logger, patch("rm_client.ui.gl_resources.time.perf_counter", return_value=100.):
            for _ in range(25):
                self.assertFalse(render())
        self.assertEqual(renderer.resources.diagnostics.faults, 25)
        self.assertEqual(logger.error.call_count, 1)
        self.assertEqual(len(renderer.resources.diagnostics.last_reported_at), 1)
        self.assertFalse(renderer.resources.last_result.cleanup_faults)
        functions.glDrawArrays.side_effect = None
        self.assertTrue(render())
        self.assertEqual(renderer.last_error, "")
        self.assertIsNone(renderer.resources.last_result.primary_fault)

    def test_partial_native_admission_before_exception_is_also_unwound(self):
        for cls in RENDERERS:
            boundaries = ["program", "buffer", "vertex_array", "attribute"]
            if cls is StartupGpuCompositor:
                boundaries.extend(("current", "previous"))
            for name in boundaries:
                with self.subTest(renderer=cls.__name__, boundary=name):
                    renderer, functions, buffer, render, trace = make_renderer(cls)
                    bound = [False]
                    def partial_bind(*args):
                        bound[0] = True
                        raise TypeError("failure after native admission")
                    def release(*args):
                        bound[0] = False
                    if name == "attribute":
                        bind, cleanup = renderer.program.enableAttributeArray, renderer.program.disableAttributeArray
                    else:
                        handle = buffer if name == "buffer" else getattr(renderer, name)
                        bind, cleanup = handle.bind, handle.release
                    bind.side_effect = partial_bind
                    cleanup.side_effect = release
                    self.assertFalse(render())
                    self.assertFalse(bound[0], "native admission must not survive a raised wrapper call")
                    cleanup.assert_called_once()
                    functions.glDrawArrays.assert_not_called()
                    self.assertEqual(renderer.resources.last_result.primary_fault.error_type, "TypeError")

    def test_real_qimage_normalization_preserves_rgba_and_one_origin_flip(self):
        source = QImage(2, 2, QImage.Format.Format_RGBA8888)
        colors = (QColor(10, 20, 30, 255), QColor(40, 50, 60, 128), QColor(70, 80, 90, 64), QColor(100, 110, 120, 0))
        for index, color in enumerate(colors):
            source.setPixelColor(index % 2, index // 2, color)
        top = normalize_image_for_texture(source, TextureOrigin.TOP_LEFT_IMAGE)
        bottom = normalize_image_for_texture(source, TextureOrigin.BOTTOM_LEFT_FBO)
        for index, color in enumerate(colors):
            self.assertEqual(top.pixelColor(index % 2, 1 - index // 2), color)
            self.assertEqual(bottom.pixelColor(index % 2, index // 2), color)


class SpecializedGlReleaseTests(unittest.TestCase):
    def test_first_destroy_failure_does_not_skip_later_owners(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, trace = make_renderer(cls)
                program, vao = renderer.program, renderer.vertex_array
                if cls is StartupGpuCompositor:
                    first, later = renderer.current, renderer.previous
                    renderer.spare = Mock()
                else:
                    first, later = buffer, vao
                original = OSError("first owner destroy defect")
                first.destroy.side_effect = original
                with self.assertRaises(OSError) as caught:
                    renderer.release()
                self.assertIs(caught.exception, original)
                self.assertFalse(renderer.available)
                later.destroy.assert_called_once()
                program.removeAllShaders.assert_called_once()
                self.assertIsNone(renderer.program)
                if cls is StartupGpuCompositor:
                    self.assertIs(renderer.current, first)
                    self.assertIsNone(renderer.previous)
                    self.assertIsNone(renderer.spare)
                first.destroy.side_effect = None
                renderer.release()
                self.assertEqual(first.destroy.call_count, 2)
                self.assertEqual(program.removeAllShaders.call_count, 1)

    def test_failed_shader_reference_survives_until_explicit_release_retry(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, trace = make_renderer(cls)
                program = renderer.program
                program.removeAllShaders.side_effect = RuntimeError("shader removal defect")
                with self.assertRaisesRegex(RuntimeError, "shader removal defect"):
                    renderer.release()
                self.assertIs(renderer.program, program)
                self.assertFalse(renderer.available)
                self.assertTrue(renderer.resources.release_pending)
                self.assertIn("shader removal defect", renderer.last_error)
                program.removeAllShaders.side_effect = None
                renderer.release()
                self.assertIsNone(renderer.program)
                self.assertFalse(renderer.resources.release_pending)

    def test_multiple_release_faults_preserve_first_exception_and_record_all(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, trace = make_renderer(cls)
                first = OSError("buffer destroy defect")
                buffer.destroy.side_effect = first
                renderer.vertex_array.destroy.side_effect = ValueError("array destroy defect")
                renderer.program.removeAllShaders.side_effect = TypeError("shader removal defect")
                with self.assertRaises(OSError) as caught:
                    renderer.release()
                self.assertIs(caught.exception, first)
                self.assertEqual([fault.error_type for fault in renderer.resources.last_result.cleanup_faults], ["OSError", "ValueError", "TypeError"])
                self.assertFalse(renderer.available)


if __name__ == "__main__":
    unittest.main()
