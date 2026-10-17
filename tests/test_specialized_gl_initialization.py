"""Fault injection at real specialized initialization and retry boundaries."""
from pathlib import Path
import os
import hashlib
import json
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_specialized_gl_owners import RENDERERS, StartupGpuCompositor, make_renderer


def make_uninitialized(cls):
    renderer, functions, buffer, render, trace = make_renderer(cls)
    renderer.available = False
    renderer.program = None
    if cls is StartupGpuCompositor:
        renderer.current = None
        renderer.previous = None
    program = Mock()
    program.addShaderFromSourceCode.return_value = True
    program.link.return_value = True
    program.uniformLocation.return_value = 0
    program.attributeLocation.return_value = 0
    program.log.return_value = "shader rejected"
    program.bind.return_value = True
    for handle in (buffer, renderer.vertex_array):
        handle.isCreated.return_value = False
        def create(handle=handle):
            handle.isCreated.return_value = True
            return True
        def destroy(handle=handle):
            handle.isCreated.return_value = False
        handle.create.side_effect = create
        handle.destroy.side_effect = destroy
    return renderer, functions, buffer, render, program


class SpecializedGlInitializationTests(unittest.TestCase):
    def test_successful_initialization_compiles_exact_baseline_shader_sources(self):
        baseline = json.loads((ROOT / "tests/fixtures/specialized_gl_shader_sources.json").read_text())
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, program = make_uninitialized(cls)
                with patch(cls.__module__ + ".QOpenGLShaderProgram", return_value=program) as factory:
                    self.assertTrue(renderer.initialize())
                    self.assertTrue(renderer.initialize())
                factory.assert_called_once()
                self.assertEqual([hashlib.sha256(call.args[1].encode()).hexdigest() for call in program.addShaderFromSourceCode.call_args_list], baseline[cls.__name__])
                program.link.assert_called_once()
                renderer.vertex_array.create.assert_called_once()
                buffer.create.assert_called_once()
                program.removeAllShaders.assert_not_called()
                self.assertTrue(renderer.available)
                self.assertEqual(renderer.last_error, "")

    def test_native_setup_primary_survives_multiple_cleanup_defects(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, program = make_uninitialized(cls)
                buffer.create.side_effect = TypeError("native buffer setup defect")
                renderer.vertex_array.destroy.side_effect = OSError("array destroy defect")
                program.removeAllShaders.side_effect = ValueError("shader cleanup defect")
                with patch(cls.__module__ + ".QOpenGLShaderProgram", return_value=program):
                    self.assertFalse(renderer.initialize())
                result = renderer.resources.last_result
                self.assertEqual(result.primary_fault.error_type, "TypeError")
                self.assertEqual([fault.error_type for fault in result.cleanup_faults], ["OSError", "ValueError"])
                self.assertIn("native buffer setup defect", renderer.last_error)
                self.assertIs(renderer.program, program)
                self.assertFalse(renderer.available)
                self.assertTrue(renderer.resources.release_pending)

    def test_shader_compile_exception_does_not_orphan_candidate_program(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, program = make_uninitialized(cls)
                program.addShaderFromSourceCode.side_effect = TypeError("shader source defect")
                program.removeAllShaders.side_effect = OSError("shader cleanup defect")
                with patch(cls.__module__ + ".QOpenGLShaderProgram", return_value=program):
                    self.assertFalse(renderer.initialize())
                self.assertIs(renderer.program, program)
                self.assertEqual(renderer.resources.last_result.primary_fault.error_type, "TypeError")
                self.assertEqual(renderer.resources.last_result.cleanup_faults[0].error_type, "OSError")
                renderer.vertex_array.create.assert_not_called()

    def test_shader_create_bool_rejections_do_not_invent_exceptions(self):
        for cls in RENDERERS:
            for boundary in ("shader", "link", "array", "buffer"):
                with self.subTest(renderer=cls.__name__, boundary=boundary):
                    renderer, functions, buffer, render, program = make_uninitialized(cls)
                    method = {"shader": program.addShaderFromSourceCode, "link": program.link,
                              "array": renderer.vertex_array.create, "buffer": buffer.create}[boundary]
                    method.side_effect = None
                    method.return_value = False
                    with patch(cls.__module__ + ".QOpenGLShaderProgram", return_value=program), patch("rm_client.ui.gl_resources._LOG") as logger:
                        self.assertFalse(renderer.initialize())
                    self.assertFalse(renderer.available)
                    self.assertIsNone(renderer.program)
                    self.assertTrue(renderer.resources.last_result.rejection)
                    self.assertIsNone(renderer.resources.last_result.primary_fault)
                    self.assertFalse(renderer.resources.last_result.cleanup_faults)
                    program.removeAllShaders.assert_called_once()
                    logger.error.assert_not_called()

    def test_startup_rejected_buffer_bind_cannot_allocate(self):
        renderer, functions, buffer, render, program = make_uninitialized(StartupGpuCompositor)
        buffer.bind.return_value = False
        with patch(StartupGpuCompositor.__module__ + ".QOpenGLShaderProgram", return_value=program):
            self.assertFalse(renderer.initialize())
        buffer.allocate.assert_not_called()
        buffer.release.assert_not_called()
        renderer.vertex_array.release.assert_called_once()
        program.removeAllShaders.assert_called_once()
        self.assertIn("buffer.bind", renderer.last_error)

    def test_startup_allocate_fault_unwinds_before_destroy_and_preserves_primary(self):
        renderer, functions, buffer, render, program = make_uninitialized(StartupGpuCompositor)
        buffer.allocate.side_effect = TypeError("vertex payload defect")
        buffer.release.side_effect = OSError("buffer unbind defect")
        with patch(StartupGpuCompositor.__module__ + ".QOpenGLShaderProgram", return_value=program):
            self.assertFalse(renderer.initialize())
        buffer.release.assert_called_once()
        renderer.vertex_array.release.assert_called_once()
        buffer.destroy.assert_called_once()
        renderer.vertex_array.destroy.assert_called_once()
        program.removeAllShaders.assert_called_once()
        self.assertEqual(renderer.resources.last_result.primary_fault.error_type, "TypeError")
        self.assertEqual(renderer.resources.last_result.cleanup_faults[0].error_type, "OSError")

    def test_failed_release_is_retried_before_program_replacement(self):
        for cls in RENDERERS:
            with self.subTest(renderer=cls.__name__):
                renderer, functions, buffer, render, program = make_uninitialized(cls)
                with patch(cls.__module__ + ".QOpenGLShaderProgram", return_value=program) as factory:
                    self.assertTrue(renderer.initialize())
                    program.removeAllShaders.side_effect = OSError("retained program")
                    with self.assertRaises(OSError):
                        renderer.release()
                    self.assertFalse(renderer.initialize())
                    self.assertEqual(factory.call_count, 1)
                    self.assertIs(renderer.program, program)
                    program.removeAllShaders.side_effect = None
                    replacement = Mock(wraps=None)
                    replacement.addShaderFromSourceCode.return_value = True
                    replacement.link.return_value = True
                    replacement.uniformLocation.return_value = 0
                    replacement.attributeLocation.return_value = 0
                    factory.return_value = replacement
                    self.assertTrue(renderer.initialize())
                    self.assertEqual(factory.call_count, 2)
                    self.assertIs(renderer.program, replacement)
                    self.assertFalse(renderer.resources.release_pending)
                    if cls is StartupGpuCompositor:
                        renderer.current = Mock()
                    self.assertTrue(render())
                    self.assertTrue(renderer.available)
                    self.assertEqual(renderer.last_error, "")

    def test_startup_draw_cleanup_fault_cannot_silently_replace_old_program(self):
        renderer, functions, buffer, render, program = make_uninitialized(StartupGpuCompositor)
        with patch(StartupGpuCompositor.__module__ + ".QOpenGLShaderProgram", return_value=program) as factory:
            self.assertTrue(renderer.initialize())
            renderer.current = Mock()
            functions.glDrawArrays.side_effect = TypeError("draw defect")
            buffer.release.side_effect = OSError("buffer unbind defect")
            self.assertFalse(render())
            self.assertFalse(renderer.available)
            program.removeAllShaders.side_effect = ValueError("old shader removal defect")
            self.assertFalse(renderer.initialize())
            self.assertEqual(factory.call_count, 1)
            self.assertIs(renderer.program, program)
            self.assertIn("old shader removal defect", renderer.last_error)


if __name__ == "__main__":
    unittest.main()
