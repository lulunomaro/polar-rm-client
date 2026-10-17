"""Glass command lifetimes and live read order without a native GL context."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QRectF, QSize
from rm_client.ui import gpu_frame_compositor as gpu


class GlassCommandLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.c = gpu.GpuFrameCompositor()
        self.c.available = True
        self.c._size = QSize(800, 600)
        self.c.glass_program = Mock(uniformLocation=lambda name: name)
        self.c._uniform = lambda program, name: name
        self.c.glass_program.release.return_value = None
        for name in ('background', 'control', 'blur', 'treated_blur', 'hud_glass', 'glass',
                     'overlay', 'dynamic', 'target'):
            setattr(self.c, name + '_fbo', Mock(bind=Mock(return_value=True),
                    release=Mock(return_value=True), texture=Mock(return_value=1)))
        self.c._draw_quad = Mock(return_value=True)
        self.gl = Mock()
        self.logging = patch.object(gpu, '_LOG')
        self.logging.start()
        self.addCleanup(self.logging.stop)

    def add(self, radius, **kwargs):
        self.c.add_glass(QRectF(10, 20, 30, 40), radius, **kwargs)
        commands = self.c._static_glass_commands if self.c._recording_static_glass else self.c._frame_glass_commands
        return commands[-1]

    def radii(self):
        return [call.args[1] for call in self.gl.glUniform1f.call_args_list if call.args[0] == 'radius']

    def test_frame_reset_keeps_static_identity_and_resets_owner_recording(self):
        static, frame, owners = self.c._static_glass_commands, self.c._frame_glass_commands, self.c._glass_owner_stack
        self.c.begin_static_glass()
        first = self.add(1)
        self.c.end_static_glass()
        self.add(2)
        self.c.push_glass_owner('ClickGUI')
        self.c.begin_static_glass()
        self.c.begin_frame(rebuild_static_ui=False)
        self.assertIs(self.c._static_glass_commands, static)
        self.assertIs(self.c._frame_glass_commands, frame)
        self.assertIs(self.c._glass_owner_stack, owners)
        self.assertEqual(static, [first])
        self.assertEqual(frame, [])
        self.assertEqual(owners, [gpu.GlassOwner.HUD])
        self.assertFalse(self.c._recording_static_glass)
        self.c.begin_frame()
        self.assertEqual(static, [])

    def test_nested_owner_conversion_and_underflow_preserve_recorded_identity(self):
        self.c.push_glass_owner('ClickGUI')
        self.c.push_glass_owner(gpu.GlassOwner.ECONOMY)
        command = self.add(1)
        self.assertEqual(command.owner, gpu.GlassOwner.ECONOMY)
        with self.assertRaises(ValueError):
            self.c.push_glass_owner('missing')
        self.assertEqual(self.c.glass_owner, gpu.GlassOwner.ECONOMY)
        self.c.pop_glass_owner()
        self.assertEqual(self.c.glass_owner, gpu.GlassOwner.CLICKGUI)
        self.c.pop_glass_owner()
        self.c.pop_glass_owner()
        self.assertEqual(self.c.glass_owner, gpu.GlassOwner.HUD)
        self.assertEqual(command.owner, gpu.GlassOwner.ECONOMY)

    def test_static_then_frame_selection_preserves_order_and_owner_filter(self):
        self.c.begin_static_glass()
        self.add(1)
        self.add(2, owner=gpu.GlassOwner.ECONOMY)
        self.c.end_static_glass()
        self.add(3, owner=gpu.GlassOwner.ECONOMY)
        self.add(4)
        self.assertTrue(self.c.render_glass(self.gl, owners={gpu.GlassOwner.ECONOMY}))
        self.assertEqual(self.radii(), [2., 3.])
        self.gl.reset_mock()
        self.assertTrue(self.c.render_glass(self.gl, include_static=False))
        self.assertEqual(self.radii(), [3., 4.])
        self.gl.reset_mock()
        self.assertTrue(self.c.render_glass(self.gl, owners=set()))
        self.assertEqual(self.radii(), [])

    def test_destination_clear_precedes_command_selection(self):
        self.add(1)
        self.gl.glClear.side_effect = lambda _: self.add(2)
        self.assertTrue(self.c.render_glass(self.gl))
        self.assertEqual(self.radii(), [1., 2.])

    def test_selection_freezes_membership_but_keeps_next_command_geometry_live(self):
        first, second = self.add(1), self.add(2)
        def change_after_first_scissor(*args):
            if self.gl.glScissor.call_count == 1:
                first.rect.setWidth(99)
                second.rect.translate(7, 9)
                self.add(3)
                self.c._device_pixel_ratio = 2.
        self.gl.glScissor.side_effect = change_after_first_scissor
        self.assertTrue(self.c.render_glass(self.gl))
        self.assertEqual(self.radii(), [1., 2.])
        rectangles = [call.args[1:] for call in self.gl.glUniform4f.call_args_list if call.args[0] == 'glassRect']
        self.assertEqual(rectangles, [(10., 540., 30., 40.), (17., 531., 30., 40.)])
        self.assertEqual(len(self.c._frame_glass_commands), 3)
        self.assertEqual(first.rect.width(), 99.)

    def test_fractional_dpr_clipping_and_tiny_command_skips_match_existing_math(self):
        self.c._device_pixel_ratio = 1.5
        self.c.add_glass(QRectF(-2.2, 3.4, 10.1, 20.2), 2, clip_rect=QRectF(-1, 5, 7, 9))
        self.c.add_glass(QRectF(1, 1, .2, 9), 3)
        self.c.add_glass(QRectF(1, 1, 9, 9), 4, clip_rect=QRectF(99, 99, 2, 2))
        self.assertTrue(self.c.render_glass(self.gl))
        self.assertEqual(self.radii(), [3.])
        self.gl.glScissor.assert_called_once_with(0, 579, 11, 14)
        rectangles = [call.args[1:] for call in self.gl.glUniform4f.call_args_list if call.args[0] == 'glassRect']
        self.assertEqual(rectangles, [(-3.3000000000000003, 564.6, 15.149999999999999, 30.299999999999997)])

    def test_geometry_thresholds_and_offscreen_scissors_remain_unchanged(self):
        cases = (
            (QRectF(10, 20, .5, 5), 1., None),
            (QRectF(10, 20, .5001, 5), 1., (10, 575, 1, 5)),
            (QRectF(810, 20, 10, 5), 1., (810, 575, -10, 5)),
            (QRectF(10, -20, 10, 5), 1., (10, 615, 10, -15)),
            (QRectF(-20, 20, 10, 5), 1., (0, 575, 10, 5)),
            (QRectF(10, 610, 10, 5), 1., (10, 0, 10, 5)),
            (QRectF(10.2, 20.3, 10.4, 5.5), 2., (20, 548, 21, 11)),
        )
        for rect, ratio, expected in cases:
            with self.subTest(rect=rect, ratio=ratio):
                self.c.begin_frame()
                self.gl.reset_mock()
                self.c._draw_quad.reset_mock()
                self.c._device_pixel_ratio = ratio
                self.c.add_glass(rect, 1)
                self.assertTrue(self.c.render_glass(self.gl))
                if expected is None:
                    self.gl.glScissor.assert_not_called()
                    self.c._draw_quad.assert_not_called()
                else:
                    self.gl.glScissor.assert_called_once_with(*expected)
                    self.c._draw_quad.assert_called_once()

    def test_scene_clear_discards_frame_commands_only_after_every_layer_accepts(self):
        self.c.begin_static_glass()
        static = self.add(1)
        self.c.end_static_glass()
        frame = self.add(2)
        self.c.dynamic_fbo.release.return_value = False
        self.assertFalse(self.c.clear_scene_layers(self.gl))
        self.assertEqual(self.c._frame_glass_commands, [frame])
        self.c.target_fbo.bind.assert_not_called()
        self.c.dynamic_fbo.release.return_value = True
        self.assertTrue(self.c.clear_scene_layers(self.gl))
        self.assertEqual(self.c._frame_glass_commands, [])
        self.assertEqual(self.c._static_glass_commands, [static])

    def test_transient_reset_clears_all_command_state_without_native_io(self):
        self.c.begin_static_glass()
        self.add(1)
        self.c.push_glass_owner(gpu.GlassOwner.ECONOMY)
        self.c._blur_valid = self.c._treated_blur_valid = True
        self.c.invalidate_transient_layers()
        self.assertEqual(self.c._static_glass_commands, [])
        self.assertEqual(self.c._frame_glass_commands, [])
        self.assertEqual(self.c._glass_owner_stack, [gpu.GlassOwner.HUD])
        self.assertFalse(self.c._recording_static_glass)
        self.assertFalse(self.c._blur_valid)
        self.assertFalse(self.c._treated_blur_valid)
        self.assertEqual(self.gl.mock_calls, [])

    def test_uniform_fault_retains_commands_and_attempts_all_cleanup(self):
        self.c.begin_static_glass()
        static = self.add(1)
        self.c.end_static_glass()
        frame = self.add(2)
        self.gl.glUniform4f.side_effect = TypeError('bad glass rectangle')
        self.c.glass_program.release.side_effect = OSError('program release')
        self.c.glass_fbo.release.side_effect = ValueError('destination release')
        self.assertFalse(self.c.render_glass(self.gl))
        failure = self.c.last_failed_frame
        self.assertEqual(failure.render_fault.error_type, 'TypeError')
        self.assertEqual([fault.error_type for fault in failure.cleanup_faults], ['OSError', 'ValueError'])
        self.assertEqual(self.c._static_glass_commands, [static])
        self.assertEqual(self.c._frame_glass_commands, [frame])
        self.c.glass_fbo.release.assert_called_once()
        self.gl.glActiveTexture.assert_called_with(gpu._GL_TEXTURE0)
        disabled = [call.args[0] for call in self.gl.glDisable.call_args_list]
        self.assertIn(gpu._GL_SCISSOR_TEST, disabled)
        self.assertIn(gpu._GL_BLEND, disabled)
        self.c.begin_frame(rebuild_static_ui=False)
        self.assertEqual(self.c._static_glass_commands, [static])
        self.assertEqual(self.c._frame_glass_commands, [])

    def test_failed_draw_or_release_does_not_consume_commands(self):
        command = self.add(1)
        for phase in ('draw', 'release'):
            with self.subTest(phase=phase):
                self.c._draw_quad.return_value = phase != 'draw'
                self.c.glass_fbo.release.return_value = phase != 'release'
                self.assertFalse(self.c.render_glass(self.gl))
                self.assertEqual(self.c._frame_glass_commands, [command])
                self.assertEqual(self.c.diagnostics.faults, 0)


class GlassPainterCacheTests(unittest.TestCase):
    def test_partial_static_commands_never_commit_cache_after_draw_or_cleanup_failure(self):
        from dataclasses import replace
        import test_overlay_layer_boundary as overlay_tests
        for defect in ('draw', 'release'):
            with self.subTest(defect=defect):
                fixture = overlay_tests.OverlayLayerOwnershipTests()
                fixture.setUp()
                try:
                    c = fixture.c
                    painters = []
                    def draw(painter):
                        painters.append(painter)
                        c.add_glass(QRectF(1, 2, 3, 4), 5)
                        if defect == 'draw':
                            raise TypeError('glass-producing painter failed')
                    fixture.draws = replace(fixture.draws, static_hud=draw)
                    original_release = c.release_layer
                    c.release_layer = Mock(side_effect=OSError('layer release failed'))
                    self.assertIsNone(fixture.render())
                    self.assertFalse(painters[0].isActive())
                    self.assertEqual(c._glass_owner_stack, [gpu.GlassOwner.HUD])
                    self.assertFalse(c._recording_static_glass)
                    self.assertIsNone(fixture.cache.static_key)
                    partial = c._static_glass_commands[0]
                    self.assertEqual(partial.radius, 5.)
                    self.assertTrue(c.frame_rejected)
                    if defect == 'draw':
                        self.assertEqual(c.last_failed_frame.render_fault.error_type, 'TypeError')
                        self.assertEqual([fault.error_type for fault in c.last_failed_frame.cleanup_faults], ['OSError'])
                    c.release_layer = original_release
                    fixture.draws = replace(fixture.draws, static_hud=lambda painter: c.add_glass(QRectF(1, 2, 3, 4), 6))
                    self.assertIsNotNone(fixture.render())
                    self.assertEqual([command.radius for command in c._static_glass_commands], [6.])
                    self.assertTrue(all(command is not partial for command in c._static_glass_commands))
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
