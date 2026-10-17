# Independent MJPEG decoder correction review

Fixed candidate: `24160858b8f322eefacd4cce7a7b77786f56a3df`.
Independent red reproduction: detached `c34a8e3` worktree.
Verdict: PASS for the concrete OpenCV byte-adapter defect and existing-envelope
normalization. No production changes made by this reviewer.

The red checkpoint independently yields exactly three failures among six tests:
valid UDP JPEG delivery, recognized four-byte envelope delivery and the paused
real-decode restart case. Candidate delivery tests pass all eight methods.
A combined independent run of delivery, decoder lifecycle, backend source
nonblocking and transport safety modules passes 72 methods. The candidate's
entire 206-test author result is not claimed as independently rerun here.

An additional committed script generates actual JPEG bytes and compares decoded
pixels against actual OpenCV output in 24 cases: grayscale and color images,
1x1/7x13/48x64 dimensions, raw packets and three four-byte prefixes. All pass,
with exact dimensions and one published-frame count. JPEG output is compared to
decoding the same encoded bytes, not to the lossy pre-encoding image.

Reviewed effects and limits:

- Shared helper does only lazy cv2/numpy import, uint8 frombuffer and imdecode.
  It does not own locks, sessions, timestamps, counters or state publication.
- UDP normalization matches the existing detector's SOI-at-offset-four rule;
  the four bytes are not interpreted as a new validated length/header. Raw SOI
  has priority. No arbitrary scanning or new frame-reassembly protocol appears.
- Continuous decode retains its existing raw-input policy and error diagnostic.
  UDP retains its distinct backend marker and error behavior.
- Original generation/session checks remain under the original publication locks.
  Paused real decode cannot publish after restart; late decode errors cannot
  overwrite current-generation diagnostics. Existing lifecycle tests also pass.
- Successful UDP bytes decoding is intentionally different from the defective
  baseline. This is not claimed as extraction equivalence for the old failure.

Evidence: `docs/evidence/clean-code-20261016/mjpeg-independent-domain/`.
Run `PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest
 test_mjpeg_frame_delivery test_decoder_lifecycle_quality
 test_backend_video_source_nonblocking test_transport_safety_regressions -v`,
and `PYTHONPATH=. python /path/to/verify_pixels.py` from the candidate checkout.

No live UDP/camera/device or ffmpeg-process test was performed. Native GPU output,
packaging and throughput remain unqualified. API/static reports were inspected,
not independently rerun in this narrow review; parent integration owns them.
