"""Exercise compositor owner boundaries, with inert GL handles, not a GPU."""
from pathlib import Path
import os
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSize
from rm_client.ui import gpu_frame_compositor as gpu
from rm_client.ui.polar.compositor_plan import FinalFrameCompositor, FrameCompositionPlan


class CompositorFaultTests(unittest.TestCase):
    def setUp(self):
        self.c = gpu.GpuFrameCompositor()
        self.c.available = True
        self.c._size = QSize(800, 600)
        self.c.vertex_array = Mock()
        self.c.vertex_buffer = Mock()
        for name in ("present_program", "blur_program", "jello_blur_program", "glass_program"):
            setattr(self.c, name, Mock(uniformLocation=Mock(return_value=1)))
        for name in ("background", "control", "blur_temp", "blur", "treated_blur_temp", "treated_blur",
                     "hud_glass", "glass", "overlay", "vision", "dynamic", "target"):
            setattr(self.c, name + "_fbo", Mock(bind=Mock(return_value=True), texture=Mock(return_value=1)))
        self.gl = Mock()
        self.logger = Mock()
        self.patch = patch.object(gpu, "_LOG", self.logger)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_layer_release_false_rejects_final_present_until_a_new_frame(self):
        self.c.dynamic_fbo.release.return_value = False
        self.assertFalse(self.c.release_layer("dynamic"))
        self.assertTrue(self.c.frame_rejected)
        self.assertFalse(self.c.present(self.gl, 1))
        self.gl.glDrawArrays.assert_not_called()
        self.assertEqual(self.c.diagnostics.faults, 0)
        self.logger.error.assert_not_called()
        self.c.dynamic_fbo.release.return_value = True
        self.c.begin_frame()
        self.assertFalse(self.c.frame_rejected)
        self.assertTrue(self.c.present(self.gl, 1))

    def test_false_releases_are_reported_separately_without_overwriting_the_root_exception(self):
        layers = ("background", "control", "overlay", "vision", "dynamic", "target")
        for name in layers:
            getattr(self.c, name + "_fbo").release.return_value = False
        recovery = self.c.recover_failed_frame(TypeError("root image defect"))
        self.assertEqual(recovery.render_fault.error_type, "TypeError")
        self.assertEqual(recovery.rejected_layers, layers)
        self.assertEqual(recovery.cleanup_faults, ())
        self.assertIn("TypeError: root image defect", self.c.last_error)
        self.assertEqual(self.c.diagnostics.faults, 1)
        for name in layers:
            getattr(self.c, name + "_fbo").release.assert_called_once()

    def test_ordinary_rejection_unwinds_every_layer_without_inventing_faults(self):
        self.c.background_fbo.release.return_value = False
        recovery = self.c.recover_rejected_frame()
        self.assertEqual(recovery.rejected_layers, ("background",))
        self.assertEqual(recovery.cleanup_faults, ())
        self.assertEqual(self.c.diagnostics.faults, 0)
        self.assertIsNone(self.c.last_failed_frame)
        for name in ("background", "control", "overlay", "vision", "dynamic", "target"):
            getattr(self.c, name + "_fbo").release.assert_called_once()
        self.logger.error.assert_not_called()

    def test_target_vision_and_restore_clear_reject_false_release(self):
        for layer, clear in (("target", self.c.clear_target_layer), ("vision", self.c.clear_vision_layer)):
            with self.subTest(layer=layer):
                getattr(self.c, layer + "_fbo").release.return_value = False
                self.assertFalse(clear(self.gl))
                self.assertTrue(self.c.frame_rejected)
                self.c.begin_frame()
        self.c.overlay_fbo.release.return_value = False
        self.c._blur_valid = self.c._treated_blur_valid = True
        self.assertFalse(self.c.clear_scene_layers(self.gl))
        self.c.dynamic_fbo.bind.assert_not_called()
        self.assertFalse(self.c._blur_valid)
        self.assertFalse(self.c._treated_blur_valid)
        self.assertEqual(self.c.diagnostics.faults, 0)

    def test_layer_clear_cleanup_fault_retains_the_original_gl_defect(self):
        self.gl.glClear.side_effect = TypeError("clear defect")
        self.c.target_fbo.release.side_effect = OSError("release defect")
        self.assertFalse(self.c.clear_target_layer(self.gl))
        recovery = self.c.last_failed_frame
        self.assertEqual(recovery.render_fault.phase, "layer.clear.target")
        self.assertEqual(recovery.render_fault.error_type, "TypeError")
        self.assertEqual(recovery.cleanup_faults[0].error_type, "OSError")
        self.assertIn("TypeError: clear defect", self.c.last_error)
        self.c.target_fbo.release.assert_called_once()

    def test_vision_bind_rejection_cannot_be_mistaken_for_a_successful_cpu_fallback(self):
        self.c.vision_fbo.bind.return_value = False
        self.assertFalse(self.c.begin_vision_layer(self.gl))
        self.assertTrue(self.c.frame_rejected)
        self.c.vision_fbo.release.assert_called_once()
        self.assertFalse(self.c.present(self.gl, 1))
        self.gl.glDrawArrays.assert_not_called()
        self.assertEqual(self.c.diagnostics.faults, 0)

    def test_present_type_error_is_typed_logged_and_counted_before_false(self):
        self.gl.glBindFramebuffer.side_effect = TypeError("wrong framebuffer value")
        self.assertFalse(self.c.present(self.gl, 7))
        fault = self.c.last_failed_frame.render_fault
        self.assertEqual((fault.phase, fault.error_type), ("present", "TypeError"))
        self.assertEqual(self.c.last_error, "present: TypeError: wrong framebuffer value")
        self.assertEqual(self.c.diagnostics.faults, 1)
        info = self.logger.error.call_args.kwargs["exc_info"]
        self.assertIs(info[0], TypeError)
        self.assertIsNotNone(info[2])
        self.c.present_program.release.assert_called_once()
        self.gl.glActiveTexture.assert_called_with(gpu._GL_TEXTURE0)

    def test_actual_final_composition_preserves_internal_present_fault(self):
        self.gl.glBindFramebuffer.side_effect = TypeError("final target")
        plan = FrameCompositionPlan(True, True, True, True, frozenset(), 9)
        self.assertFalse(FinalFrameCompositor(self.c).present(self.gl, plan))
        self.assertEqual(self.c.diagnostics.last_fault.phase, "present")
        self.assertEqual(self.c.diagnostics.faults, 1)
        self.logger.error.assert_called_once()

    def test_repeated_faults_are_throttled_and_recovery_keeps_history(self):
        self.gl.glBindFramebuffer.side_effect = TypeError("target")
        with patch.object(gpu.time, "perf_counter", return_value=10.):
            for _ in range(3):
                self.assertFalse(self.c.present(self.gl, 1))
        self.assertEqual(self.c.diagnostics.faults, 3)
        self.logger.error.assert_called_once()
        self.gl.glBindFramebuffer.side_effect = None
        self.assertTrue(self.c.present(self.gl, 1))
        self.assertEqual(self.c.diagnostics.faults, 3)
        self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "TypeError")

    def test_present_cleanup_fault_never_overwrites_root_or_skips_texture_reset(self):
        self.gl.glBindFramebuffer.side_effect = TypeError("root target")
        self.c.present_program.release.side_effect = OSError("program release")
        self.assertFalse(self.c.present(self.gl, 1))
        recovery = self.c.last_failed_frame
        self.assertEqual(recovery.render_fault.error_type, "TypeError")
        self.assertEqual(recovery.cleanup_faults[0].phase, "present.cleanup.program")
        self.assertIn("root target", self.c.last_error)
        self.gl.glActiveTexture.assert_called_with(gpu._GL_TEXTURE0)

    def test_texture_reset_defect_rejects_an_otherwise_successful_pass(self):
        def active(unit):
            # Actual present binds unit zero first and restores it after all
            # nine texture inputs. Fail only that final restoration call.
            if self.gl.glActiveTexture.call_count == 10:
                raise AttributeError("texture reset")
        self.gl.glActiveTexture.side_effect = active
        self.assertFalse(self.c.present(self.gl, 1))
        fault = self.c.last_failed_frame.render_fault
        self.assertEqual((fault.phase, fault.error_type), ("present.cleanup.qt-texture", "AttributeError"))

    def test_ordinary_not_ready_and_bind_rejection_do_not_invent_faults(self):
        self.c.available = False
        self.assertFalse(self.c.present(self.gl, 1))
        self.c.blur_temp_fbo.bind.return_value = False
        self.assertFalse(self.c.prepare_blur(self.gl, background_changed=True))
        self.assertEqual(self.c.diagnostics.faults, 0)
        self.logger.error.assert_not_called()

    def test_blur_fault_releases_each_owner_and_invalidates_cached_blur(self):
        self.gl.glViewport.side_effect = TypeError("blur viewport")
        self.c.blur_temp_fbo.release.side_effect = OSError("temporary release")
        self.assertFalse(self.c.prepare_blur(self.gl, background_changed=True))
        self.assertFalse(self.c._blur_valid)
        self.assertEqual(self.c.last_failed_frame.render_fault.phase, "blur")
        self.c.blur_program.release.assert_called_once()
        self.c.blur_fbo.release.assert_called_once()
        self.gl.glActiveTexture.assert_called_with(gpu._GL_TEXTURE0)

    def test_treated_backdrop_fault_is_distinct_for_both_blur_kernels(self):
        for kernel in ("compact_gaussian", "jello_box20"):
            with self.subTest(kernel=kernel):
                self.c.set_backdrop_treatment(active=True, progress=1, blur_radius_px=4,
                    dim_alpha=.2, include_overlay=True, blur_kernel=kernel)
                self.gl.glViewport.side_effect = TypeError("treated viewport")
                self.assertFalse(self.c.prepare_treated_backdrop(self.gl, background_changed=True))
                self.assertEqual(self.c.last_failed_frame.render_fault.phase, "backdrop.blur")
                self.assertFalse(self.c._treated_blur_valid)
                self.assertTrue(self.c.jello_blur_program.release.called)

    def test_glass_cleanup_attempts_remaining_owners_after_first_failure(self):
        self.gl.glViewport.side_effect = TypeError("glass viewport")
        self.c.glass_program.release.side_effect = OSError("program release")
        self.assertFalse(self.c.render_glass(self.gl))
        self.assertEqual(self.c.last_failed_frame.render_fault.phase, "glass")
        self.c.glass_fbo.release.assert_called_once()
        self.assertIn(unittest.mock.call(gpu._GL_SCISSOR_TEST), self.gl.glDisable.call_args_list)
        self.assertIn(unittest.mock.call(gpu._GL_BLEND), self.gl.glDisable.call_args_list)
        self.gl.glActiveTexture.assert_called_with(gpu._GL_TEXTURE0)

    def test_initialize_shader_defect_retains_cause_and_cleanup_fault(self):
        def fail():
            try:
                raise ValueError("layout data")
            except ValueError as cause:
                raise TypeError("shader layout") from cause
        self.c.available = False
        # This case begins a new shader lifetime. Retained programs now
        # correctly take the retirement admission path before any builder.
        for name in ("present_program", "blur_program", "jello_blur_program", "glass_program"):
            setattr(self.c, name, None)
        self.c._build_present_program = fail
        self.c.release = Mock(side_effect=OSError("resource cleanup"))
        self.assertFalse(self.c.initialize())
        recovery = self.c.last_failed_frame
        self.assertEqual((recovery.render_fault.phase, recovery.render_fault.cause_type), ("initialize", "ValueError"))
        self.assertEqual(recovery.cleanup_faults[0].phase, "initialize.cleanup.resources")
        self.assertFalse(self.c.available)

    def test_each_quad_bind_and_draw_failure_releases_all_three_state_owners(self):
        operations = (
            self.c.vertex_array.bind, self.c.vertex_buffer.bind,
            self.c.present_program.enableAttributeArray,
            self.c.present_program.setAttributeBuffer, self.gl.glDrawArrays,
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self.c.vertex_array.reset_mock()
                self.c.vertex_buffer.reset_mock()
                self.c.present_program.reset_mock()
                self.gl.reset_mock()
                operation.side_effect = TypeError("quad operation")
                try:
                    self.assertFalse(self.c.present(self.gl, 1))
                finally:
                    operation.side_effect = None
                self.c.present_program.disableAttributeArray.assert_called_once_with(0)
                self.c.vertex_buffer.release.assert_called_once()
                self.c.vertex_array.release.assert_called_once()
                self.assertEqual(self.c.last_failed_frame.render_fault.phase, "present")
                self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "TypeError")

    def test_quad_cleanup_failures_cannot_hide_draw_error_or_skip_remaining_releases(self):
        self.gl.glDrawArrays.side_effect = TypeError("draw root")
        self.c.present_program.disableAttributeArray.side_effect = ValueError("attribute cleanup")
        self.c.vertex_buffer.release.side_effect = OSError("buffer cleanup")
        self.assertFalse(self.c.present(self.gl, 1))
        result = self.c.last_failed_frame
        self.assertEqual(result.render_fault.message, "draw root")
        self.assertEqual([fault.phase for fault in result.cleanup_faults], [
            "present.quad.cleanup.attribute", "present.quad.cleanup.buffer",
        ])
        self.assertEqual([fault.error_type for fault in result.cleanup_faults], ["ValueError", "OSError"])
        self.c.vertex_array.release.assert_called_once()
        self.c.present_program.release.assert_called_once()
        self.gl.glActiveTexture.assert_called_with(gpu._GL_TEXTURE0)
        self.assertEqual(self.c.diagnostics.faults, 3)
        self.assertIn("draw root", self.c.last_error)

    def test_quad_cleanup_only_failure_rejects_successful_draw(self):
        self.c.present_program.disableAttributeArray.side_effect = ValueError("attribute cleanup")
        self.assertFalse(self.c.present(self.gl, 1))
        fault = self.c.last_failed_frame.render_fault
        self.assertEqual((fault.phase, fault.error_type), ("present.quad.cleanup.attribute", "ValueError"))
        self.c.vertex_buffer.release.assert_called_once()
        self.c.vertex_array.release.assert_called_once()

    def test_resource_release_continues_after_destroy_error_and_retains_failed_program(self):
        programs = [self.c.present_program, self.c.blur_program, self.c.jello_blur_program, self.c.glass_program]
        self.c.vertex_buffer.destroy.side_effect = OSError("buffer destroy")
        programs[0].removeAllShaders.side_effect = TypeError("program destroy")
        with self.assertRaises(RuntimeError) as raised:
            self.c.release()
        self.assertIn("OSError: buffer destroy", str(raised.exception))
        self.assertIn("TypeError: program destroy", str(raised.exception))
        self.c.vertex_array.destroy.assert_called_once()
        for program in programs:
            program.removeAllShaders.assert_called_once()
        self.assertIs(self.c.present_program, programs[0])
        self.assertIsNone(self.c.blur_program)
        self.assertIsNone(self.c.background_fbo)
        self.assertFalse(self.c.available)
        # The retained object remains reachable for an explicit later release.
        self.c.vertex_buffer.destroy.side_effect = None
        programs[0].removeAllShaders.side_effect = None
        self.c.release()
        self.assertIsNone(self.c.present_program)

    def test_initialize_retains_shader_root_and_independent_release_errors(self):
        self.c.available = False
        # This case begins a new shader lifetime. Retained programs now
        # correctly take the retirement admission path before any builder.
        for name in ("present_program", "blur_program", "jello_blur_program", "glass_program"):
            setattr(self.c, name, None)
        self.c._build_present_program = Mock(side_effect=TypeError("shader root"))
        self.c.vertex_buffer.destroy.side_effect = OSError("buffer destroy")
        self.assertFalse(self.c.initialize())
        failure = self.c.last_failed_frame
        self.assertEqual(failure.render_fault.message, "shader root")
        self.assertEqual(failure.cleanup_faults[0].phase, "initialize.cleanup.resources.release.vertex-buffer")
        self.c.vertex_array.destroy.assert_called_once()

    def test_shader_bind_false_rejects_without_draw_or_invented_exception(self):
        self.c.present_program.bind.return_value = False
        self.assertFalse(self.c.present(self.gl, 1))
        self.gl.glDrawArrays.assert_not_called()
        self.c.present_program.release.assert_called_once()
        self.assertEqual(self.c.diagnostics.faults, 0)
        self.logger.error.assert_not_called()

    def test_vertex_buffer_bind_false_still_unwinds_quad_but_never_draws(self):
        self.c.vertex_buffer.bind.return_value = False
        self.assertFalse(self.c.present(self.gl, 1))
        self.gl.glDrawArrays.assert_not_called()
        self.c.vertex_buffer.release.assert_called_once()
        self.c.vertex_array.release.assert_called_once()
        self.c.present_program.disableAttributeArray.assert_called_once_with(0)
        self.assertEqual(self.c.diagnostics.faults, 0)

    def test_void_qt_operations_returning_none_are_not_rejections(self):
        self.c.vertex_array.bind.return_value = None
        self.c.vertex_array.release.return_value = None
        self.c.vertex_buffer.bind.return_value = True
        self.c.vertex_buffer.release.return_value = None
        self.c.present_program.bind.return_value = True
        self.c.present_program.release.return_value = None
        self.c.present_program.disableAttributeArray.return_value = None
        self.assertTrue(self.c.present(self.gl, 1))
        self.gl.glDrawArrays.assert_called_once()
        self.assertEqual(self.c.diagnostics.faults, 0)

    def test_framebuffer_release_false_rejects_blur_and_attempts_other_cleanup(self):
        self.c.blur_temp_fbo.release.return_value = False
        self.assertFalse(self.c.prepare_blur(self.gl, background_changed=True))
        self.assertFalse(self.c._blur_valid)
        self.gl.glDrawArrays.assert_called_once()  # No second blur pass.
        self.c.blur_fbo.release.assert_called_once()
        self.c.blur_program.release.assert_called_once()
        self.assertEqual(self.c.diagnostics.faults, 0)

    def test_glass_destination_release_false_is_not_a_successful_frame(self):
        self.c.glass_fbo.release.return_value = False
        self.assertFalse(self.c.render_glass(self.gl))
        self.assertEqual(self.c.diagnostics.faults, 0)
        self.c.glass_program.release.assert_called_once()
        self.gl.glActiveTexture.assert_called_with(gpu._GL_TEXTURE0)

    def test_blur_shader_bind_false_rejects_without_sampling_or_fault(self):
        self.c.blur_program.bind.return_value = False
        self.assertFalse(self.c.prepare_blur(self.gl, background_changed=True))
        self.gl.glDrawArrays.assert_not_called()
        self.c.blur_temp_fbo.release.assert_called_once()
        self.c.blur_fbo.release.assert_called_once()
        self.assertEqual(self.c.diagnostics.faults, 0)

    def test_resize_allocation_defect_disables_invalid_storage_and_is_diagnostic(self):
        with patch.object(gpu, "QOpenGLFramebufferObject", side_effect=TypeError("attachment layout")):
            self.assertFalse(self.c.ensure_size(900, 700, 1.))
        self.assertFalse(self.c.available)
        self.assertIsNone(self.c.background_fbo)
        self.assertEqual(self.c.diagnostics.last_fault.phase, "resize")
        self.assertEqual(self.c.diagnostics.last_fault.error_type, "TypeError")


if __name__ == "__main__": unittest.main()
