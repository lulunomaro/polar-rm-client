"""Real OpenCV JPEG bytes at two publication owners; no device or network IO."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from rm_client.backend.video_backend import RmVideoBackend, ContinuousMjpegDecoder, detect_frame_codec
from rm_client.transports import UDPVideoReceiver


class MjpegFrameDeliveryTests(unittest.TestCase):
    def setUp(self):
        y, x = np.indices((24, 32))
        source = np.stack(((x * 7) % 256, (y * 9) % 256, ((x + y) * 5) % 256), axis=-1).astype(np.uint8)
        encoded, payload = cv2.imencode('.jpg', source)
        self.assertTrue(encoded)
        self.payload = payload.tobytes()
        self.expected = cv2.imdecode(payload, cv2.IMREAD_COLOR)
        self.backend = RmVideoBackend()
        self.backend.decode_h265 = False
        self.backend.h265_decoder = None
        self.decoder = ContinuousMjpegDecoder()

    def test_valid_raw_jpeg_publishes_exact_same_pixels_through_both_paths(self):
        self.backend._on_assembled_frame(self.payload, self.backend._video_generation)
        self.decoder._decode_frame(self.payload)
        self.assertEqual(self.backend.codec, 'mjpeg')
        self.assertEqual(self.backend.mjpeg_decoded_frames, 1)
        self.assertEqual(self.decoder.frames_decoded, 1)
        self.assertEqual(self.backend.decoder_backend, 'cv2')
        self.assertEqual((self.backend.output_width, self.backend.output_height), (32, 24))
        self.assertGreater(self.backend.latest_bgr_at, 0.)
        np.testing.assert_array_equal(self.backend.latest_bgr, self.expected)
        np.testing.assert_array_equal(self.decoder._latest, self.expected)
        np.testing.assert_array_equal(self.backend.latest_video_frame()[0], self.expected)

    def test_invalid_payload_does_not_replace_previous_image_or_advance_counts(self):
        # Seed each publication owner directly so this negative control also
        # passes before the UDP bytes/array correction.
        sentinel = self.expected.copy()
        self.backend.latest_bgr = self.decoder._latest = sentinel
        self.backend.latest_bgr_at = self.decoder._latest_at = 17.
        for payload in (b'not a jpeg', b'\xff\xd8broken', b''):
            with self.subTest(payload=payload):
                self.backend._decode_mjpeg(payload)
                self.decoder._decode_frame(payload)
                self.assertIs(self.backend.latest_bgr, sentinel)
                self.assertIs(self.decoder._latest, sentinel)
                self.assertEqual((self.backend.latest_bgr_at, self.decoder._latest_at), (17., 17.))
                self.assertEqual((self.backend.mjpeg_decoded_frames, self.decoder.frames_decoded), (0, 0))

    def test_recognized_four_byte_prefix_is_normalized_only_by_udp_adapter(self):
        prefixed = len(self.payload).to_bytes(4, 'little') + self.payload
        self.assertEqual(detect_frame_codec(prefixed), 'mjpeg')
        self.backend._on_assembled_frame(prefixed, self.backend._video_generation)
        self.decoder._decode_frame(prefixed)
        np.testing.assert_array_equal(self.backend.latest_bgr, self.expected)
        self.assertEqual(self.backend.mjpeg_decoded_frames, 1)
        self.assertIsNone(self.decoder._latest)
        self.assertEqual(self.decoder.frames_decoded, 0)

    def test_wrong_missing_soi_and_truncated_envelopes_remain_rejected(self):
        for payload in (b'1234', b'1234notjpeg', b'12345' + self.payload,
                        b'1234\xff\xd8', b'1234\xff\xd8broken'):
            with self.subTest(payload=payload[:16]):
                self.backend._on_assembled_frame(payload, self.backend._video_generation)
                self.assertIsNone(self.backend.latest_bgr)
                self.assertEqual(self.backend.mjpeg_decoded_frames, 0)

    def test_decode_started_before_stop_cannot_publish_after_restart(self):
        real_decode = cv2.imdecode
        entered, resume = threading.Event(), threading.Event()
        errors = []
        generation = self.backend._video_generation
        def held_decode(*args):
            image = real_decode(*args)
            entered.set()
            if not resume.wait(3):
                raise AssertionError('decode not released')
            return image
        def decode():
            try:
                self.backend._decode_mjpeg(self.payload, generation)
            except BaseException as error:
                errors.append(error)
        with patch.object(cv2, 'imdecode', side_effect=held_decode):
            worker = threading.Thread(target=decode)
            worker.start()
            try:
                self.assertTrue(entered.wait(1), 'valid JPEG never reached decode completion')
                self.backend.request_stop()
                with patch.object(UDPVideoReceiver, 'start'):
                    self.backend.start()
                self.assertGreater(self.backend._video_generation, generation)
                resume.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())
            finally:
                resume.set()
                worker.join(3)
        self.assertEqual(errors, [])
        self.assertIsNone(self.backend.latest_bgr)
        self.assertEqual(self.backend.latest_bgr_at, 0.)
        self.assertEqual(self.backend.mjpeg_decoded_frames, 0)
        self.backend._decode_mjpeg(self.payload, self.backend._video_generation)
        self.assertEqual(self.backend.mjpeg_decoded_frames, 1)
        np.testing.assert_array_equal(self.backend.latest_bgr, self.expected)

    def test_raw_soi_precedence_does_not_strip_an_embedded_soi(self):
        payload = b"\xff\xd8ab\xff\xd8opaque"
        with patch.object(cv2, 'imdecode', return_value=None) as decode:
            self.backend._decode_mjpeg(payload)
        encoded = decode.call_args.args[0]
        self.assertEqual(encoded.dtype, np.uint8)
        self.assertEqual(encoded.tobytes(), payload)

    def test_late_decode_error_cannot_overwrite_new_generation_diagnostic(self):
        entered, resume = threading.Event(), threading.Event()
        generation = self.backend._video_generation
        def held_error(*args):
            entered.set()
            if not resume.wait(3):
                raise AssertionError('decode not released')
            raise ValueError('retired decode error')
        with patch.object(cv2, 'imdecode', side_effect=held_error):
            worker = threading.Thread(target=self.backend._decode_mjpeg,
                                      args=(self.payload, generation))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.backend.request_stop()
                with patch.object(UDPVideoReceiver, 'start'):
                    self.backend.start()
                self.backend.decoder_backend = 'current-generation'
            finally:
                resume.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.backend.decoder_backend, 'current-generation')
        self.assertEqual(self.backend.mjpeg_decoded_frames, 0)

    def test_exception_reporting_stays_owned_by_each_publication_adapter(self):
        with patch.object(cv2, 'imdecode', side_effect=ValueError('decoder unavailable')):
            self.backend.decoder_backend = 'cv2'
            self.backend._decode_mjpeg(self.payload)
            self.decoder._decode_frame(self.payload)
        self.assertEqual(self.backend.decoder_backend, 'none')
        self.assertEqual(self.backend.last_error, '')
        self.assertIn('ValueError: decoder unavailable', self.decoder.last_error)
        self.assertEqual((self.backend.mjpeg_decoded_frames, self.decoder.frames_decoded), (0, 0))


if __name__ == '__main__':
    unittest.main()
