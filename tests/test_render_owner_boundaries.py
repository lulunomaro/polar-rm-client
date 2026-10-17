"""Production vision/TargetHUD resource unwinds with inert GL and Qt raster.

No driver pixels or native device qualification is inferred from these tests.
"""
from pathlib import Path
import os
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QSize
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication
from rm_client.model import build_default_registry
from rm_client.ui import gpu_frame_compositor as gpu
from rm_client.ui.polar.renderers.esp_wireframe import EspWireframeMixin
from test_cs_crosshair_target_bounds import TargetHost

APP = QApplication.instance() or QApplication([])


class RenderOwnerBoundaryTests(unittest.TestCase):
    def vision_host(self):
        compositor = gpu.GpuFrameCompositor()
        compositor._size = QSize(800, 600)
        compositor.vision_fbo = Mock(bind=Mock(return_value=True))
        registry = build_default_registry()
        registry.get("Armor3DESP").enabled = True
        registry.get("ArmorAutoAim").enabled = True
        host = NS(
            gpu_compositor=compositor, registry=registry, home_visible=False,
            setting_mode=lambda module, name, default: default,
            _vision_source="Local", vision_gpu_renderer_allows=lambda: True,
            vision_wire_gpu=NS(available=True, render=Mock(return_value=True)),
            clickgui_gpu=NS(available=True, prepare_frame=Mock(), render=Mock(return_value=True)),
            interpolated_vision_result=lambda now: object(),
            build_vision_wire_batch=lambda *args: NS(labels=NS(commands=[])),
        )
        return host, compositor

    def test_vision_draw_fault_remains_primary_when_real_layer_release_also_fails(self):
        host, compositor = self.vision_host()
        host.vision_wire_gpu.render.side_effect = TypeError("invalid vision batch")
        compositor.vision_fbo.release.side_effect = OSError("vision release failed")
        with patch.object(gpu, "_LOG"):
            try:
                accepted = EspWireframeMixin.paint_gpu_vision_layer(host, Mock(), 800, 600, 1.)
            except Exception as error:
                compositor.recover_failed_frame(error)
                accepted = False
        self.assertFalse(accepted)
        failure = compositor.last_failed_frame
        self.assertEqual(failure.render_fault.error_type, "TypeError")
        self.assertIn("invalid vision batch", compositor.last_error)
        self.assertIn("OSError", [fault.error_type for fault in failure.cleanup_faults])
        self.assertTrue(compositor.vision_fbo.release.called)

    def test_vision_cpu_owner_is_distinct_from_a_rejected_native_pass(self):
        host, compositor = self.vision_host()
        host.registry.get("Armor3DESP").enabled = False
        self.assertFalse(EspWireframeMixin.paint_gpu_vision_layer(host, Mock(), 800, 600, 1.))
        self.assertFalse(compositor.frame_rejected)
        host.vision_wire_gpu.render.assert_not_called()
        compositor.vision_fbo.release.assert_called_once()
        self.assertEqual(compositor.diagnostics.faults, 0)

    def test_native_vision_owner_can_submit_an_empty_cleared_layer(self):
        host, compositor = self.vision_host()
        host.interpolated_vision_result = lambda now: None
        self.assertTrue(EspWireframeMixin.paint_gpu_vision_layer(host, Mock(), 800, 600, 1.))
        self.assertFalse(compositor.frame_rejected)
        host.vision_wire_gpu.render.assert_not_called()
        compositor.vision_fbo.release.assert_called_once()

    def test_vision_false_draw_bind_or_release_rejects_without_inventing_exceptions(self):
        for boundary in ("draw", "bind", "release", "labels"):
            with self.subTest(boundary=boundary):
                host, compositor = self.vision_host()
                if boundary == "draw":
                    host.vision_wire_gpu.render.return_value = False
                elif boundary == "labels":
                    host.build_vision_wire_batch = lambda *args: NS(labels=NS(commands=[object()]))
                    host.clickgui_gpu.render.return_value = False
                else:
                    getattr(compositor.vision_fbo, boundary).return_value = False
                with patch.object(gpu, "_LOG") as logger:
                    self.assertFalse(EspWireframeMixin.paint_gpu_vision_layer(host, Mock(), 800, 600, 1.))
                self.assertTrue(compositor.frame_rejected)
                compositor.vision_fbo.release.assert_called_once()
                self.assertEqual(compositor.diagnostics.faults, 0)
                logger.error.assert_not_called()
                if boundary == "bind":
                    host.vision_wire_gpu.render.assert_not_called()

    def test_target_tile_painter_ends_before_a_failed_picture_replay_can_upload(self):
        host = TargetHost()
        painters = []
        class BadReplayPainter(QPainter):
            def __init__(self, device):
                super().__init__(device)
                painters.append(self)
            def drawPicture(self, *args):
                raise TypeError("bad picture replay")
        with patch.object(gpu, "QPainter", BadReplayPainter), patch.object(gpu, "_LOG"):
            self.assertFalse(host.run(720))
        self.assertEqual(len(painters), 2)
        self.assertTrue(all(not painter.isActive() for painter in painters))
        self.assertFalse(host.uploads)
        fault = host.gpu_compositor.last_failed_frame.render_fault
        self.assertEqual((fault.phase, fault.error_type), ("target.tile", "TypeError"))

    def test_target_end_rejection_prevents_upload_and_remains_an_ordinary_rejection(self):
        host = TargetHost()
        class RejectingPainter(QPainter):
            def end(self):
                super().end()
                return False
        with patch.object(gpu, "QPainter", RejectingPainter), patch.object(gpu, "_LOG") as logger:
            self.assertFalse(host.run(720))
        self.assertFalse(host.uploads)
        self.assertTrue(host.gpu_compositor.frame_rejected)
        self.assertEqual(host.gpu_compositor.diagnostics.faults, 0)
        logger.error.assert_not_called()

    def test_target_picture_painter_ends_when_actual_crosshair_draw_raises(self):
        host = TargetHost()
        painters, devices = [], []
        def fail(painter, *args):
            painters.append(painter)
            devices.append(painter.device())
            raise TypeError("bad crosshair command")
        host.draw_crosshair = fail
        try:
            with patch.object(gpu, "_LOG"):
                self.assertFalse(host.run(720))
            self.assertEqual(host.gpu_compositor.last_failed_frame.render_fault.error_type, "TypeError")
            self.assertEqual(len(painters), 1)
            self.assertFalse(painters[0].isActive())
            self.assertFalse(host.uploads)
        finally:
            for painter in painters:
                if painter.isActive():
                    painter.end()

    def test_target_direct_raster_painter_ends_when_hud_draw_raises(self):
        host = TargetHost()
        host.registry.get("CombatTargetHUD").enabled = True
        host.update_presence = lambda *args: NS(visible=True)
        host.target_hud_presence.update = host.update_presence
        painters, devices = [], []
        def fail(painter, *args, **kwargs):
            painters.append(painter)
            devices.append(painter.device())
            raise TypeError("bad target HUD command")
        host.draw_combat_target_hud_layer = fail
        try:
            with patch.object(gpu, "_LOG"):
                self.assertFalse(host.run(720))
            self.assertEqual(host.gpu_compositor.last_failed_frame.render_fault.error_type, "TypeError")
            self.assertEqual(len(painters), 1)
            self.assertFalse(painters[0].isActive())
            self.assertFalse(host.uploads)
        finally:
            for painter in painters:
                if painter.isActive():
                    painter.end()


if __name__ == "__main__":
    unittest.main()
