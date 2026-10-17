"""Inert native-handle evidence for compositor setup and retry ownership.

No real context or desktop GPU output is qualified by these tests.
"""
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui import gpu_frame_compositor as gpu


class CompositorInitializationOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.make_compositor()
        logging = patch.object(gpu, "_LOG")
        logging.start()
        self.addCleanup(logging.stop)

    def make_compositor(self):
        self.c = gpu.GpuFrameCompositor()
        self.c.vertex_array = Mock()
        self.c.vertex_buffer = Mock()
        for handle in (self.c.vertex_array, self.c.vertex_buffer):
            handle.isCreated.return_value = False
            def create(handle=handle):
                handle.isCreated.return_value = True
                return True
            def destroy(handle=handle):
                handle.isCreated.return_value = False
            handle.create.side_effect = create
            handle.destroy.side_effect = destroy
        self.builders = []
        for name in ("present", "blur", "jello_blur", "lens"):
            builder = Mock(return_value=Mock())
            setattr(self.c, "_build_" + name + "_program", builder)
            self.builders.append(builder)

    def test_success_keeps_geometry_and_repeated_initialize_is_inert(self):
        self.assertTrue(self.c.initialize())
        self.assertTrue(self.c.initialize())
        for builder in self.builders:
            builder.assert_called_once()
        self.c.vertex_buffer.allocate.assert_called_once_with(
            gpu.struct.pack("8f", -1., -1., 1., -1., -1., 1., 1., 1.), 32)
        self.c.vertex_buffer.release.assert_called_once()
        self.c.vertex_array.release.assert_called_once()

    def test_failed_retirement_blocks_new_programs_until_original_owner_releases(self):
        self.assertTrue(self.c.initialize())
        original = self.c.present_program
        original.removeAllShaders.side_effect = OSError("program still owned")
        with self.assertRaises(RuntimeError):
            self.c.release()
        self.assertFalse(self.c.initialize())
        self.assertIs(self.c.present_program, original)
        self.assertFalse(self.c.available)
        for builder in self.builders:
            builder.assert_called_once()
        original.removeAllShaders.side_effect = None
        replacement = Mock()
        self.builders[0].return_value = replacement
        self.assertTrue(self.c.initialize())
        self.assertIs(self.c.present_program, replacement)
        self.assertEqual(original.removeAllShaders.call_count, 3)

    def test_failed_buffer_retirement_blocks_recreation_even_without_programs(self):
        self.assertTrue(self.c.initialize())
        self.c.vertex_buffer.destroy.side_effect = OSError("native buffer still owned")
        with self.assertRaises(RuntimeError):
            self.c.release()
        self.assertIsNone(self.c.present_program)
        self.assertFalse(self.c.initialize())
        self.c.vertex_buffer.create.assert_called_once()
        for builder in self.builders:
            builder.assert_called_once()
        self.c.vertex_buffer.destroy.side_effect = lambda: setattr(self.c.vertex_buffer.isCreated, "return_value", False)
        self.assertTrue(self.c.initialize())
        self.assertEqual(self.c.vertex_buffer.create.call_count, 2)

    def test_failed_resize_retires_existing_programs_before_reinitialization(self):
        self.assertTrue(self.c.initialize())
        original = self.c.present_program
        with patch.object(gpu, "QOpenGLFramebufferObject", side_effect=ValueError("allocation")):
            self.assertFalse(self.c.ensure_size(100, 100, 1.))
        self.assertTrue(self.c.initialize())
        original.removeAllShaders.assert_called_once()

    def test_setup_fault_unbinds_both_handles_before_destroy_and_preserves_all_causes(self):
        order = []
        def fail(name, error=None):
            def operation(*args):
                order.append(name)
                if error:
                    raise error
            return operation
        self.c.vertex_buffer.allocate.side_effect = fail("allocate", TypeError("vertex payload"))
        self.c.vertex_buffer.release.side_effect = fail("buffer.release", OSError("buffer unbind"))
        self.c.vertex_array.release.side_effect = fail("array.release", ValueError("array unbind"))
        self.c.vertex_buffer.destroy.side_effect = fail("buffer.destroy")
        self.c.vertex_array.destroy.side_effect = fail("array.destroy")
        self.assertFalse(self.c.initialize())
        self.assertEqual(order, ["allocate", "buffer.release", "array.release", "buffer.destroy", "array.destroy"])
        failure = self.c.last_failed_frame
        self.assertEqual(failure.render_fault.error_type, "TypeError")
        self.assertEqual([fault.error_type for fault in failure.cleanup_faults], ["OSError", "ValueError"])
        self.assertIn("vertex payload", self.c.last_error)

    def test_bind_false_does_not_allocate_and_unbinds_before_retirement(self):
        self.c.vertex_buffer.bind.return_value = False
        self.assertFalse(self.c.initialize())
        self.c.vertex_buffer.allocate.assert_not_called()
        self.c.vertex_buffer.release.assert_called_once()
        self.c.vertex_array.release.assert_called_once()
        self.assertEqual(self.c.diagnostics.faults, 0)

    def test_each_partial_bind_fault_attempts_both_unbinds(self):
        for boundary in ("array", "buffer"):
            with self.subTest(boundary=boundary):
                self.make_compositor()
                handle = self.c.vertex_array if boundary == "array" else self.c.vertex_buffer
                handle.bind.side_effect = TypeError("partial native bind")
                self.assertFalse(self.c.initialize())
                self.c.vertex_buffer.release.assert_called_once()
                self.c.vertex_array.release.assert_called_once()
                self.c.vertex_buffer.allocate.assert_not_called()
                self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "TypeError")

    def test_failed_initialization_cleanup_must_retire_before_retry(self):
        original = self.builders[0].return_value
        self.c.vertex_buffer.allocate.side_effect = TypeError("setup failure")
        original.removeAllShaders.side_effect = OSError("retained shader")
        self.assertFalse(self.c.initialize())
        self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "TypeError")
        self.assertIs(self.c.present_program, original)
        self.c.vertex_buffer.allocate.side_effect = None
        self.assertFalse(self.c.initialize())
        for builder in self.builders:
            builder.assert_called_once()
        original.removeAllShaders.side_effect = None
        self.assertTrue(self.c.initialize())
        self.assertEqual(self.builders[0].call_count, 2)

    def test_unbind_false_rejects_setup_without_inventing_exception(self):
        self.c.vertex_buffer.release.return_value = False
        self.assertFalse(self.c.initialize())
        self.c.vertex_array.release.assert_called_once()
        self.assertFalse(self.c.available)
        self.assertIsNone(self.c.present_program)
        self.assertEqual(self.c.diagnostics.faults, 0)


if __name__ == "__main__":
    unittest.main()
