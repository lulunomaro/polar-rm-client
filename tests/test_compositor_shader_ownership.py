"""Actual shader factories, inert native handles: failed candidates stay owned."""
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui import gpu_frame_compositor as gpu


SLOTS = ("present_program", "blur_program", "jello_blur_program", "glass_program")


class CompositorShaderOwnershipTests(unittest.TestCase):
    def compositor(self):
        compositor = gpu.GpuFrameCompositor()
        compositor.vertex_array = Mock()
        compositor.vertex_buffer = Mock()
        return compositor

    def program(self):
        program = Mock()
        program.addShaderFromSourceCode.return_value = True
        program.link.return_value = True
        program.log.return_value = "shader rejected"
        return program

    def test_each_compile_and_link_failure_releases_candidate_and_earlier_programs(self):
        for slot in range(4):
            for stage in ("vertex", "fragment", "link"):
                for raises in (True, False):
                    with self.subTest(slot=SLOTS[slot], stage=stage, raises=raises):
                        compositor = self.compositor()
                        programs = [self.program() for _ in range(slot + 1)]
                        candidate = programs[-1]
                        failure = TypeError("shader operation defect") if raises else False
                        if stage == "link":
                            if raises:
                                candidate.link.side_effect = failure
                            else:
                                candidate.link.return_value = False
                        else:
                            candidate.addShaderFromSourceCode.side_effect = [failure] if stage == "vertex" else [True, failure]
                        with patch.object(gpu, "QOpenGLShaderProgram", side_effect=programs) as factory, patch.object(gpu, "_LOG"):
                            self.assertFalse(compositor.initialize())
                        self.assertEqual(factory.call_count, slot + 1)
                        for program in programs:
                            program.removeAllShaders.assert_called_once()
                        self.assertTrue(all(getattr(compositor, name) is None for name in SLOTS))
                        self.assertFalse(compositor.available)
                        compositor.vertex_array.create.assert_not_called()
                        fault = compositor.last_failed_frame.render_fault
                        self.assertEqual(fault.error_type, "TypeError" if raises else "RuntimeError")
                        self.assertEqual(fault.message, "shader operation defect" if raises else "shader rejected")

    def test_failed_candidate_cleanup_retains_identity_blocks_replacement_and_retries(self):
        for slot in range(4):
            with self.subTest(slot=SLOTS[slot]):
                compositor = self.compositor()
                programs = [self.program() for _ in range(slot + 1)]
                candidate = programs[-1]
                candidate.link.side_effect = TypeError("link root")
                candidate.removeAllShaders.side_effect = OSError("candidate still owned")
                with patch.object(gpu, "QOpenGLShaderProgram", side_effect=programs) as factory, patch.object(gpu, "_LOG"):
                    self.assertFalse(compositor.initialize())
                    self.assertIs(getattr(compositor, SLOTS[slot]), candidate)
                    recovery = compositor.last_failed_frame
                    self.assertEqual(recovery.render_fault.message, "link root")
                    self.assertEqual([f.error_type for f in recovery.cleanup_faults], ["OSError"])
                    self.assertFalse(compositor.initialize())
                    self.assertEqual(factory.call_count, slot + 1)
                candidate.removeAllShaders.side_effect = None
                replacements = [self.program() for _ in range(4)]
                with patch.object(gpu, "QOpenGLShaderProgram", side_effect=replacements):
                    self.assertTrue(compositor.initialize())
                self.assertEqual(candidate.removeAllShaders.call_count, 3)
                self.assertTrue(all(getattr(compositor, name) is replacement for name, replacement in zip(SLOTS, replacements)))

    def test_original_unannotatable_error_identity_and_cause_reach_diagnostics(self):
        class UnannotatableError(TypeError):
            def __setattr__(self, name, value):
                raise AttributeError("error attributes are sealed")
        compositor = self.compositor()
        candidate = self.program()
        original = UnannotatableError("original driver error")
        cause = ValueError("original driver cause")
        def fail(*args):
            raise original from cause
        candidate.addShaderFromSourceCode.side_effect = fail
        with patch.object(gpu, "QOpenGLShaderProgram", return_value=candidate), patch.object(gpu, "_LOG") as logger:
            self.assertFalse(compositor.initialize())
        candidate.removeAllShaders.assert_called_once()
        logger.error.assert_called_once()
        self.assertIs(logger.error.call_args.kwargs["exc_info"][1], original)
        fault = compositor.last_failed_frame.render_fault
        self.assertEqual((fault.error_type, fault.cause_type, fault.cause_message),
                         ("UnannotatableError", "ValueError", "original driver cause"))

    def test_constructor_failure_releases_prior_programs_without_fabricating_candidate(self):
        for slot in range(4):
            with self.subTest(slot=SLOTS[slot]):
                compositor = self.compositor()
                programs = [self.program() for _ in range(slot)]
                with patch.object(gpu, "QOpenGLShaderProgram", side_effect=[*programs, MemoryError("allocation failed")]), patch.object(gpu, "_LOG"):
                    self.assertFalse(compositor.initialize())
                for program in programs:
                    program.removeAllShaders.assert_called_once()
                self.assertTrue(all(getattr(compositor, name) is None for name in SLOTS))
                self.assertEqual(compositor.last_failed_frame.render_fault.error_type, "MemoryError")

    def test_failed_shader_log_preserves_original_error_and_candidate_owner(self):
        compositor = self.compositor()
        candidate = self.program()
        candidate.addShaderFromSourceCode.return_value = False
        candidate.log.side_effect = ValueError("driver log failed")
        with patch.object(gpu, "QOpenGLShaderProgram", return_value=candidate), patch.object(gpu, "_LOG"):
            self.assertFalse(compositor.initialize())
        candidate.removeAllShaders.assert_called_once()
        self.assertEqual(compositor.last_failed_frame.render_fault.error_type, "ValueError")


if __name__ == "__main__":
    unittest.main()
