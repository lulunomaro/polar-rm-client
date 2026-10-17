"""GPU retry/Qt-painter boundaries, without a Qt or OpenGL dependency."""
from pathlib import Path
import sys
import logging
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.polar.clickgui_presenter import ClickGuiGpuPresenter, ClickGuiViewport


class ClickGuiPresenterTests(unittest.TestCase):
    def setUp(self):
        self.renderer = NS(prepare_frame=Mock(), render=Mock(return_value=True), last_error="")
        self.painter = NS(beginNativePainting=Mock(), endNativePainting=Mock())
        self.restore = Mock()
        self.logger = Mock(spec=logging.Logger)
        self.now = 10.0
        self.presenter = ClickGuiGpuPresenter(self.renderer, self.restore, logger=self.logger, clock=lambda: self.now)
        self.functions = object()
        self.commands = object()
        self.viewport = ClickGuiViewport(1280, 720, 1.5)

    def present(self, **kwargs):
        return self.presenter.present(self.painter, self.functions, self.commands, self.viewport, **kwargs)

    def test_prepared_success_uses_one_balanced_paint_and_no_prepare(self):
        self.assertTrue(self.present(atlas_prepared=True))
        self.renderer.prepare_frame.assert_not_called()
        self.renderer.render.assert_called_once_with(self.functions, self.commands, 1280, 720, 1.5, atlas_prepared=True)
        self.painter.beginNativePainting.assert_called_once()
        self.painter.endNativePainting.assert_called_once()
        self.restore.assert_called_once_with(self.functions, clear_bindings=True)

    def test_retry_reuses_commands_and_rebuilds_atlas_once(self):
        self.renderer.render.side_effect = [False, True]
        self.assertTrue(self.present(atlas_prepared=True))
        self.renderer.prepare_frame.assert_called_once_with(self.commands, 1.5)
        self.assertEqual(self.renderer.render.call_count, 2)
        self.assertEqual(self.painter.endNativePainting.call_count, 2)

    def test_rejected_frames_are_bounded_and_do_not_leave_native_paint_active(self):
        self.renderer.render.return_value = False
        self.assertFalse(self.present())
        self.assertEqual(self.renderer.prepare_frame.call_count, 2)
        self.assertEqual(self.renderer.render.call_count, 2)
        self.assertEqual(self.painter.endNativePainting.call_count, 2)

    def test_render_exception_releases_native_paint_before_retry(self):
        calls = []
        self.painter.beginNativePainting.side_effect = lambda: calls.append("begin")
        self.painter.endNativePainting.side_effect = lambda: calls.append("end")
        self.renderer.render.side_effect = [RuntimeError("GPU busy"), True]
        self.assertTrue(self.present())
        self.assertEqual(calls, ["begin", "end", "begin", "end"])
        self.assertEqual(self.renderer.last_error, "commands.render: RuntimeError: GPU busy")

    def test_texture_restore_exception_also_releases_native_paint(self):
        self.restore.side_effect = RuntimeError("texture state")
        self.assertFalse(self.present())
        self.assertEqual(self.painter.endNativePainting.call_count, 2)
        self.renderer.render.assert_not_called()

    def test_prepare_exception_never_enters_native_paint(self):
        self.renderer.prepare_frame.side_effect = RuntimeError("atlas")
        self.assertFalse(self.present())
        self.assertEqual(self.renderer.prepare_frame.call_count, 2)
        self.painter.beginNativePainting.assert_not_called()
        self.painter.endNativePainting.assert_not_called()

    def test_programming_faults_keep_type_phase_counts_and_throttled_trace(self):
        self.renderer.render.side_effect = TypeError("invalid command")
        self.assertFalse(self.present())
        self.now += .1
        self.assertFalse(self.present())
        diagnostics = self.presenter.diagnostics
        self.assertEqual(diagnostics.faults, 4)
        self.assertEqual(diagnostics.rejected_frames, 2)
        self.assertEqual(diagnostics.last_fault.phase, "commands.render")
        self.assertEqual(diagnostics.last_fault.error_type, "TypeError")
        self.logger.error.assert_called_once()
        error_info = self.logger.error.call_args.kwargs["exc_info"]
        self.assertIs(error_info[0], TypeError)
        self.assertIsNotNone(error_info[2])
        self.now += 1.0
        self.assertFalse(self.present())
        self.assertEqual(self.logger.error.call_count, 2)
        self.renderer.render.side_effect = None
        self.assertTrue(self.present())
        self.assertEqual(diagnostics.successful_frames, 1)
        self.assertEqual(diagnostics.consecutive_failed_frames, 0)
        self.assertEqual(diagnostics.faults, 6)

    def test_explicit_exception_cause_is_preserved(self):
        def fail(*args):
            try:
                raise KeyError("font")
            except KeyError as cause:
                raise TypeError("atlas page") from cause
        self.renderer.prepare_frame.side_effect = fail
        self.assertFalse(self.present())
        fault = self.presenter.diagnostics.last_fault
        self.assertEqual(fault.phase, "atlas.prepare")
        self.assertEqual(fault.error_type, "TypeError")
        self.assertEqual(fault.cause_type, "KeyError")
        self.assertEqual(fault.cause_message, "'font'")

    def test_native_painter_end_failure_rejects_otherwise_successful_frame(self):
        self.painter.endNativePainting.side_effect = RuntimeError("painter")
        self.assertFalse(self.present())
        self.assertEqual(self.presenter.diagnostics.last_fault.phase, "painter.end")
        self.assertEqual(self.painter.endNativePainting.call_count, 2)

    def test_missing_context_does_not_upload_or_paint(self):
        self.functions = None
        self.assertFalse(self.present())
        self.renderer.prepare_frame.assert_not_called()
        self.painter.beginNativePainting.assert_not_called()


if __name__ == "__main__":
    unittest.main()
