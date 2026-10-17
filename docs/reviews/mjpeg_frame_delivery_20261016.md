# MJPEG byte decoding and frame-delivery ownership

Baseline: `66deab1d34ea56e0e9ce07812ef6b565491b704f`.
Red checkpoints: `69ae950` (raw JPEG) and `c34a8e3` (recognized UDP envelope).

## Concrete defect

`RmVideoBackend._decode_mjpeg` passed Python `bytes` directly into
`cv2.imdecode`. The pinned real OpenCV rejected that argument: it expects a
NumPy byte array (or UMat). The broad existing exception handler silently set
`decoder_backend` to `none`, leaving a valid MJPEG packet without a displayed
frame. The continuous ffmpeg-output adapter already converted bytes through
`np.frombuffer(..., dtype=np.uint8)` and decoded the identical JPEG correctly.

A locally generated 32x24 patterned JPEG reproduces the difference without
network traffic, camera access or an ffmpeg process. The raw red checkpoint has
two failing tests; the envelope red checkpoint has three. Negative controls
continue to reject invalid payloads without advancing decoded-frame counters.

## Minimal seam and intentional correction

Private `_decode_jpeg_bgr` owns only the encoded-bytes-to-NumPy adapter and real
OpenCV decode call. Both existing owners use it, but retain their own locks,
generations/session checks, timestamps, counters, latest-frame slots and error
policies. The continuous decoder still expects a raw image and does not strip
transport envelopes. Dependency imports remain lazy.

At the UDP adapter only, an SOI at offset four is stripped when the payload does
not already start with SOI. This exactly matches the optional four-byte prefix
recognized by the existing codec detector; it does not parse a new header,
interpret a length/endian field, scan for arbitrary SOI positions or change
stream framing. Raw SOI takes precedence. Missing/wrong-offset SOI and truncated
payloads remain rejected. Prefix recognition was already present, but delivery
was broken; successful raw and recognized-prefix MJPEG display is an intentional
bugfix exception to the old failure baseline.

No image mirroring, color format, resize, HUD composition, inference, GPU/CPU
selection or shader behavior changes. Expected pixels are compared to decoding
the same JPEG with real OpenCV, not to the pre-compression source (JPEG is lossy).

## Evidence and boundaries

`tests/test_mjpeg_frame_delivery.py` covers exact decoded pixel equality on both
paths, counters/dimensions/timestamps, invalid payload retention, recognized
prefix vs continuous raw-only behavior, malformed/truncated envelopes, raw SOI
precedence, decode paused across stop/restart, late error suppression and each
adapter's distinct exception-reporting policy. Generation tests run real Python
threads and the existing stop/restart methods, replacing only socket admission;
no real network or device is opened. Existing continuous-session late-publication
checks are retained in the decoder lifecycle suite.

Command results and the exact production SHA-256 are in
`reports/clean-code/mjpeg_frame_delivery_green_20261016.json`; baseline failure
output is adjacent as `mjpeg_frame_delivery_red_20261016.txt.gz`, preserving
its exact original bytes. The readable `.txt` copy strips trailing ASCII spaces,
tabs and carriage returns only; this avoids a NumPy-generated `AssertionError: `
line violating the repository whitespace gate. Original, compressed and readable
SHA-256 values are recorded in `mjpeg_frame_delivery_red_20261016.evidence.json`.
Use the repository's
hash-pinned Linux verification requirements. API freeze and static quality gates
are included; integration aggregate results belong to the parent candidate.

The tests qualify actual JPEG byte decoding. They do not qualify live UDP packet
traffic, physical camera drivers, ffmpeg subprocess output, native GPU pixels,
DPI or performance. The existing camera generation/retirement owners were
reviewed without finding a fresh concrete blocker; no additional abstraction was
introduced there.
