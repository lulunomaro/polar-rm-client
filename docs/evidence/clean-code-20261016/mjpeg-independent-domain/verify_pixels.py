"""Independent actual JPEG decoding matrix; no socket/camera/process admission."""
import cv2
import numpy as np
from rm_client.backend.video_backend import RmVideoBackend, ContinuousMjpegDecoder, detect_frame_codec

count = 0
for shape in ((1, 1), (7, 13), (48, 64)):
    for channels in (1, 3):
        rng = np.random.default_rng(20261016)
        source = rng.integers(0, 256, shape if channels == 1 else (*shape, 3), dtype=np.uint8)
        ok, data = cv2.imencode('.jpg', source)
        assert ok
        payload = data.tobytes()
        expected = cv2.imdecode(data, cv2.IMREAD_COLOR)
        for prefix in (b'', b'abcd', b'\x00' * 4, len(payload).to_bytes(4, 'big')):
            backend = RmVideoBackend()
            backend.h265_decoder = None
            backend.decode_h265 = False
            wrapped = prefix + payload
            assert detect_frame_codec(wrapped) == 'mjpeg'
            backend._on_assembled_frame(wrapped, backend._video_generation)
            np.testing.assert_array_equal(backend.latest_bgr, expected)
            assert backend.mjpeg_decoded_frames == 1
            assert (backend.output_width, backend.output_height) == shape[::-1]
            decoder = ContinuousMjpegDecoder()
            decoder._decode_frame(payload)
            np.testing.assert_array_equal(decoder._latest, expected)
            count += 1
print(f'PASS: {count} independent real-OpenCV grayscale/color/small-frame/raw/four-byte-prefix cases')
