"""Characterize framebuffer ownership without creating a native GL context."""
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSize
from rm_client.ui import gpu_frame_compositor as gpu


NAMES = (
    "background_fbo", "control_fbo", "blur_temp_fbo", "blur_fbo",
    "treated_blur_temp_fbo", "treated_blur_fbo", "hud_glass_fbo", "glass_fbo",
    "overlay_fbo", "vision_fbo", "dynamic_fbo", "target_fbo",
)
PAINTER_NAMES = {"background_fbo", "control_fbo", "overlay_fbo", "dynamic_fbo", "target_fbo"}


class FramebufferStorageTests(unittest.TestCase):
    def setUp(self):
        self.c = gpu.GpuFrameCompositor()
        self.c.available = True
        self.created = []
        self.fail_at = None
        self.invalid_at = None
        self.validation_error_at = None
        original = gpu.QOpenGLFramebufferObject
        def create(size, fmt):
            index = len(self.created)
            if index == self.fail_at:
                raise ValueError("allocation failed")
            handle = Mock()
            handle.isValid.return_value = index != self.invalid_at
            if index == self.validation_error_at:
                handle.isValid.side_effect = TypeError("validation failed")
            self.created.append((handle, QSize(size), fmt.attachment(), fmt.internalTextureFormat()))
            return handle
        factory = Mock(side_effect=create)
        factory.Attachment = original.Attachment
        self.patches = [patch.object(gpu, "QOpenGLFramebufferObject", factory), patch.object(gpu, "_LOG")]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def allocate(self, width=100, height=50, ratio=2.):
        return self.c.ensure_size(width, height, ratio)

    def test_order_formats_and_pixel_size_are_preserved(self):
        self.assertTrue(self.allocate())
        self.assertEqual(len(self.created), 12)
        for name, (handle, size, attachment, texture_format) in zip(NAMES, self.created):
            self.assertIs(getattr(self.c, name), handle)
            self.assertEqual(size, QSize(200, 100))
            expected = gpu.QOpenGLFramebufferObject.Attachment.CombinedDepthStencil if name in PAINTER_NAMES else gpu.QOpenGLFramebufferObject.Attachment.NoAttachment
            self.assertEqual(attachment, expected)
            self.assertEqual(texture_format, 0x8058)
            handle.isValid.assert_called_once()
        self.assertEqual(self.c.fbo_generation, 1)

    def test_equal_storage_is_inert_but_dpr_change_invalidates_content(self):
        self.assertTrue(self.allocate())
        self.c._blur_valid = self.c._treated_blur_valid = True
        self.assertTrue(self.allocate())
        self.assertEqual(len(self.created), 12)
        self.assertEqual(self.c.fbo_generation, 1)
        self.assertTrue(self.c._blur_valid)
        self.assertTrue(self.allocate(200, 100, 1.))
        self.assertEqual(len(self.created), 12)
        self.assertEqual(self.c.fbo_generation, 2)
        self.assertFalse(self.c._blur_valid)
        self.assertFalse(self.c._treated_blur_valid)
        self.assertEqual(self.c._device_pixel_ratio, 1.)

    def test_minimum_dimensions_and_ratio_are_preserved(self):
        self.assertTrue(self.allocate(-5, 0, .5))
        self.assertEqual(self.c._size, QSize(1, 1))
        self.assertEqual(self.c._device_pixel_ratio, 1.)

    def test_every_partial_allocation_failure_retires_all_owned_handles(self):
        for index in range(12):
            with self.subTest(index=index):
                self.created.clear()
                self.c.available = True
                self.fail_at = index
                self.assertFalse(self.allocate())
                self.assertEqual(len(self.created), index)
                self.assertTrue(all(getattr(self.c, name) is None for name in NAMES))
                self.assertEqual(self.c._size, QSize())
                self.assertEqual(self.c.fbo_generation, 0)
                self.assertFalse(self.c.available)
                self.assertEqual(self.c.last_failed_frame.render_fault.error_type, "ValueError")

    def test_invalid_or_raising_validation_retires_the_complete_set(self):
        for kind in ("invalid_at", "validation_error_at"):
            for index in range(12):
                with self.subTest(kind=kind, index=index):
                    self.created.clear()
                    self.c.available = True
                    setattr(self, kind, index)
                    self.assertFalse(self.allocate())
                    self.assertEqual(len(self.created), 12)
                    self.assertTrue(all(getattr(self.c, name) is None for name in NAMES))
                    for record in self.created[:index + 1]:
                        record[0].isValid.assert_called_once()
                    for record in self.created[index + 1:]:
                        record[0].isValid.assert_not_called()
                    setattr(self, kind, None)

    def test_replacement_failure_never_reuses_old_size_or_blur(self):
        self.assertTrue(self.allocate())
        self.c._blur_valid = self.c._treated_blur_valid = True
        self.fail_at = len(self.created) + 3
        self.assertFalse(self.allocate(300, 200))
        self.assertEqual(self.c.fbo_generation, 1)
        self.assertEqual(self.c._size, QSize())
        self.assertFalse(self.c._blur_valid)
        self.assertFalse(self.c._treated_blur_valid)
        self.assertTrue(all(getattr(self.c, name) is None for name in NAMES))

    def test_release_keeps_public_slots_assignable_and_clears_them(self):
        handles = [object() for name in NAMES]
        for name, handle in zip(NAMES, handles):
            setattr(self.c, name, handle)
            self.assertIs(getattr(self.c, name), handle)
        self.c._destroy_fbos()
        self.c._destroy_fbos()
        self.assertTrue(all(getattr(self.c, name) is None for name in NAMES))

    def test_retirement_order_matches_native_construction_order(self):
        retired = []
        class Handle:
            def __init__(self, name):
                self.name = name
            def __del__(self):
                retired.append(self.name)
        for name in NAMES:
            setattr(self.c, name, Handle(name))
        self.c._destroy_fbos()
        self.assertEqual(retired, list(NAMES))

    def test_resize_retires_old_set_before_constructing_first_replacement(self):
        retired = []
        class Handle:
            def __init__(self, name):
                self.name = name
            def __del__(self):
                retired.append(self.name)
        for name in NAMES:
            setattr(self.c, name, Handle(name))
        self.c._size = QSize(1, 1)
        def reject(size, fmt):
            self.assertEqual(retired, list(NAMES))
            self.assertTrue(all(getattr(self.c, name) is None for name in NAMES))
            raise ValueError("replacement failed")
        gpu.QOpenGLFramebufferObject.side_effect = reject
        self.assertFalse(self.allocate())
        self.assertEqual(retired, list(NAMES))

    def test_reuse_admission_preserves_existing_scratch_slot_policy(self):
        self.assertTrue(self.allocate())
        self.c.blur_temp_fbo = self.c.treated_blur_temp_fbo = None
        self.assertTrue(self.allocate())
        self.assertEqual(len(self.created), 12)
        self.c.target_fbo = None
        self.assertTrue(self.allocate())
        self.assertEqual(len(self.created), 24)


if __name__ == "__main__":
    unittest.main()
