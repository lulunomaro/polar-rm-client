"""Real startup allocation/upload/pool methods with inert native textures."""
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage
from PySide6.QtOpenGL import QOpenGLTexture
from test_specialized_gl_owners import StartupGpuCompositor, make_renderer


def image(width=2, height=2):
    result = QImage(width, height, QImage.Format.Format_RGBA8888)
    result.fill(QColor(20, 40, 80, 160))
    result.setPixelColor(0, 0, QColor(100, 120, 140, 255))
    return result


def texture(width=2, height=2):
    result = Mock()
    result.width.return_value = width
    result.height.return_value = height
    result.create.return_value = True
    result.uploaded_payloads = []
    # Native setData consumes the pointer synchronously. Copy while QImage is
    # alive; a Mock retaining constBits beyond the call would read freed memory.
    result.setData.side_effect = lambda *args: result.uploaded_payloads.append(bytes(args[2]))
    return result


def texture_factory(candidate):
    factory = Mock(return_value=candidate)
    for name in ("Target", "TextureFormat", "PixelFormat", "PixelType", "Filter", "WrapMode"):
        setattr(factory, name, getattr(QOpenGLTexture, name))
    return factory


class StartupTextureOwnershipTests(unittest.TestCase):
    def prepared(self):
        renderer, gl, buffer, render, trace = make_renderer(StartupGpuCompositor)
        renderer.current_key = 7
        renderer.previous_key = 6
        renderer.texture_size = (2, 2)
        return renderer

    def test_failed_spare_resize_destroy_keeps_owner_and_old_frame_unchanged(self):
        renderer = self.prepared()
        renderer.spare = candidate = texture(1, 2)
        current, previous, rect = renderer.current, renderer.previous, QRectF(renderer.current_rect)
        failure = OSError("old-size texture destroy defect")
        candidate.destroy.side_effect = failure
        with patch.object(renderer, "_allocate_texture") as factory:
            with self.assertRaises(OSError) as caught:
                renderer.update_frame(image(), QRectF(0, 0, 2, 2), 8, .1, .5)
        self.assertIs(caught.exception, failure)
        self.assertIs(renderer.spare, candidate)
        self.assertIs(renderer.current, current)
        self.assertIs(renderer.previous, previous)
        self.assertEqual((renderer.current_key, renderer.current_rect, renderer.texture_size), (7, rect, (2, 2)))
        factory.assert_not_called()
        candidate.destroy.assert_called_once()

    def test_spare_and_new_upload_failure_destroy_candidate_without_losing_old_frame(self):
        for reuse in (True, False):
            with self.subTest(reuse=reuse):
                renderer = self.prepared()
                candidate = texture()
                renderer.spare = candidate if reuse else None
                current, previous = renderer.current, renderer.previous
                failure = TypeError("upload payload defect")
                candidate.setData.side_effect = failure
                with patch("rm_client.ui.startup_gl.QOpenGLTexture", texture_factory(candidate)):
                    with self.assertRaises(TypeError) as caught:
                        renderer.update_frame(image(), QRectF(0, 0, 2, 2), 8, .1, .5)
                self.assertIs(caught.exception, failure)
                candidate.destroy.assert_called_once()
                self.assertIsNone(renderer._pending_texture)
                self.assertIs(renderer.current, current)
                self.assertIs(renderer.previous, previous)
                self.assertEqual(renderer.current_key, 7)
                self.assertEqual(renderer.resources.last_result.primary_fault.error_type, "TypeError")

    def test_create_and_upload_partial_candidates_survive_failed_destroy_for_retry(self):
        for phase in ("create", "allocateStorage", "setMinificationFilter", "setData"):
            with self.subTest(phase=phase):
                renderer = self.prepared()
                candidate = texture()
                original = TypeError("candidate " + phase + " defect")
                getattr(candidate, phase).side_effect = original
                candidate.destroy.side_effect = OSError("candidate cleanup defect")
                with patch("rm_client.ui.startup_gl.QOpenGLTexture", texture_factory(candidate)) as factory:
                    with self.assertRaises(TypeError) as caught:
                        renderer.update_frame(image(), QRectF(0, 0, 2, 2), 8, .1, .5)
                    self.assertIs(caught.exception, original)
                    self.assertIs(renderer._pending_texture, candidate)
                    self.assertEqual(renderer.resources.last_result.primary_fault.error_type, "TypeError")
                    self.assertEqual(renderer.resources.last_result.cleanup_faults[0].error_type, "OSError")
                    self.assertEqual(renderer.current_key, 7)
                    candidate.destroy.assert_called_once()
                    # The next request cannot overwrite the retained candidate.
                    with self.assertRaises(OSError):
                        renderer.update_frame(image(), QRectF(0, 0, 2, 2), 9, .1, .5)
                    self.assertEqual(factory.call_count, 1)
                    self.assertEqual(candidate.destroy.call_count, 2)
                    self.assertIs(renderer._pending_texture, candidate)
                candidate.destroy.side_effect = None
                renderer.release()
                self.assertIsNone(renderer._pending_texture)
                self.assertIsNone(renderer.current)
                self.assertIsNone(renderer.previous)

    def test_successful_pool_rotation_and_uploaded_rgba_bytes_are_unchanged(self):
        renderer = StartupGpuCompositor()
        renderer.available = True
        candidates = [texture() for _ in range(3)]
        factory = texture_factory(None)
        factory.side_effect = candidates
        source = image()
        expected = source.mirrored(False, True)
        expected_bytes = bytes(expected.constBits())
        with patch("rm_client.ui.startup_gl.QOpenGLTexture", factory):
            for sequence in range(1, 6):
                renderer.update_frame(source, QRectF(0, 0, 20, 20), sequence, .1, .2)
                self.assertEqual(renderer.current_key, sequence)
                self.assertEqual(renderer.texture_size, (2, 2))
                self.assertEqual(len({id(value) for value in (renderer.current, renderer.previous, renderer.spare) if value is not None}), min(sequence, 3))
            self.assertEqual(factory.call_count, 3)
        for candidate in candidates:
            candidate.setFormat.assert_called_once_with(QOpenGLTexture.TextureFormat.RGBA8_UNorm)
            candidate.setSize.assert_called_once_with(2, 2)
            candidate.setMipLevels.assert_called_once_with(1)
            candidate.allocateStorage.assert_called_once_with(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.UInt8)
            candidate.setMinificationFilter.assert_called_once_with(QOpenGLTexture.Filter.Linear)
            candidate.setMagnificationFilter.assert_called_once_with(QOpenGLTexture.Filter.Linear)
            candidate.setWrapMode.assert_called_once_with(QOpenGLTexture.WrapMode.ClampToEdge)
            self.assertTrue(candidate.uploaded_payloads)
            self.assertTrue(all(payload == expected_bytes for payload in candidate.uploaded_payloads))
            candidate.destroy.assert_not_called()

    def test_release_clears_frame_identity_so_same_sequence_can_upload_again(self):
        renderer = self.prepared()
        renderer.release()
        renderer.available = True
        candidate = texture()
        with patch("rm_client.ui.startup_gl.QOpenGLTexture", texture_factory(candidate)):
            renderer.update_frame(image(), QRectF(0, 0, 2, 2), 7, .1, .2)
        self.assertIs(renderer.current, candidate)
        self.assertEqual(renderer.current_key, 7)
        candidate.setData.assert_called_once()


if __name__ == "__main__":
    unittest.main()
