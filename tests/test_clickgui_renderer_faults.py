"""Exercise caught faults through the production renderer, with inert GL calls."""
from pathlib import Path
import os
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui import clickgui_gpu as gpu
from rm_client.ui.polar.clickgui_presenter import ClickGuiGpuPresenter, ClickGuiViewport


class ActualRendererFaultTests(unittest.TestCase):
    def setUp(self):
        self.renderer = gpu.ClickGuiGpuRenderer()
        self.renderer.available = True
        self.renderer.rect_program = self.renderer.text_program = self.renderer.image_program = object()
        self.renderer.vertex_array = Mock()
        self.renderer._resources_ready = Mock(return_value=True)
        self.renderer._draw_ordered_commands = Mock()
        self.functions = Mock()
        self.commands = gpu.ClickGuiDrawList()
        self.logger = Mock()
        self.log_patch = patch.object(gpu, "_LOG", self.logger)
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    def render(self):
        return self.renderer.render(self.functions, self.commands, 800, 600, 1., atlas_prepared=True)

    def test_internal_type_error_is_typed_logged_and_counted_before_false_return(self):
        self.renderer._draw_ordered_commands.side_effect = TypeError("bad command")
        self.assertFalse(self.render())
        self.assertFalse(self.renderer.available)
        self.assertFalse(self.renderer._atlas_locked)
        self.assertEqual(self.renderer.diagnostics.faults, 1)
        self.assertEqual(self.renderer.diagnostics.last_fault.phase, "commands.render")
        self.assertEqual(self.renderer.diagnostics.last_fault.error_type, "TypeError")
        self.assertEqual(self.renderer.last_error, "commands.render: TypeError: bad command")
        self.logger.error.assert_called_once()
        self.assertIs(self.logger.error.call_args.kwargs["exc_info"][0], TypeError)
        self.assertIsNotNone(self.logger.error.call_args.kwargs["exc_info"][2])

    def test_actual_renderer_fault_remains_observable_through_presenter_fallback(self):
        self.renderer._draw_ordered_commands.side_effect = TypeError("bad command")
        presenter = ClickGuiGpuPresenter(self.renderer, Mock())
        painter = NS(beginNativePainting=Mock(), endNativePainting=Mock())
        self.assertFalse(presenter.present(painter, self.functions, self.commands, ClickGuiViewport(800, 600, 1.), atlas_prepared=True))
        self.assertEqual(presenter.diagnostics.rejected_frames, 1)
        self.assertEqual(self.renderer.diagnostics.faults, 1)
        self.logger.error.assert_called_once()
        self.assertEqual(painter.beginNativePainting.call_count, painter.endNativePainting.call_count)

    def test_resource_defects_are_distinct_and_do_not_disable_rendering(self):
        self.renderer._resources_ready.side_effect = AttributeError("missing atlas")
        self.assertFalse(self.render())
        self.assertTrue(self.renderer.available)
        self.assertEqual(self.renderer.diagnostics.last_fault.phase, "resources.prepare")
        self.assertEqual(self.renderer.diagnostics.last_fault.error_type, "AttributeError")
        self.renderer._draw_ordered_commands.assert_not_called()

    def test_incomplete_snapshot_is_a_rejection_without_invented_exception(self):
        self.renderer._resources_ready.return_value = False
        self.assertFalse(self.render())
        self.assertIn("incomplete", self.renderer.last_error)
        self.assertEqual(self.renderer.diagnostics.faults, 0)
        self.logger.error.assert_not_called()

    def test_repeated_failure_is_throttled_and_success_keeps_failure_history(self):
        self.renderer._resources_ready.side_effect = TypeError("resources")
        with patch.object(gpu.time, "perf_counter", return_value=10.):
            for _ in range(3):
                self.assertFalse(self.render())
        self.assertEqual(self.renderer.diagnostics.faults, 3)
        self.logger.error.assert_called_once()
        self.renderer._resources_ready.side_effect = None
        self.renderer._resources_ready.return_value = True
        self.assertTrue(self.render())
        self.assertTrue(self.renderer.available)
        self.assertEqual(self.renderer.diagnostics.faults, 3)

    def test_initialization_programmer_defect_is_observable(self):
        self.renderer.available = False
        self.renderer._build_rect_program = Mock(side_effect=TypeError("shader layout"))
        self.assertFalse(self.renderer.initialize())
        self.assertEqual(self.renderer.diagnostics.last_fault.phase, "initialize")
        self.assertEqual(self.renderer.diagnostics.last_fault.error_type, "TypeError")
        self.logger.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
