# Normal-video source/timeline baseline (2026-10-16)

Fixed production source: `9d3a849ec0fe3328f8fb3d5ae5506d5a28bb6279`.
This test-only checkpoint adds `tests/test_normal_video_timeline_contract.py`;
production, historical fixtures, cache renderers and aggregate reports are unchanged.

## Observations that extraction must preserve

- Source comparisons strip/lower, but backend lookup receives the original text.
  Empty/whitespace source consumes the backend path, while readiness treats it as
  webcam. Unknown source names select backend. These existing quirks are frozen.
- A mode switch calls clear before publishing the mode. Glass discard sees the
  old timeline; failure stops both publication and producer lookup. Capture's
  backend branch reads source again, so reentrant discard can cause another switch.
- Webcam sequence advances before luminance/image/copy/mirror work. An image
  failure consumes that sequence; luminance failure alone is ignored. Webcam's
  original CameraFeed module remains the mirror source after producer callbacks.
- Backend same/older timestamps return unchanged. Copy failure and malformed
  array dimensions do not consume timestamps. Malformed timestamp/shape conversion
  errors propagate. NaN publishes a frame but is never fresh, then permits an older
  numeric timestamp. No sanitization was added.
- Backend image/copy/mirror precedes timestamp and display-counter publication.
  It rereads CameraFeed for Mirror after the producer callback. Timestamp/display
  and the next inference sequence publish before submit; image/source/pixmap and
  luminance publish afterward. Failed submit leaves that partial state and prevents
  retrying the same timestamp. Pixmap/glass failures have distinct partial states.
- Vision source/running/worker are observed at their existing late boundaries.
  A running getter replacing the worker sends submit to the new worker. Reentrant
  submit can clear the timestamp; the remaining adapter still publishes the image.
- Freshness uses two separate source reads: normalized mode comes from the first,
  backend-selection eligibility from the second. Backend readiness performs three
  source reads in total; webcam/synthetic readiness performs one. Freshness includes
  the exact stale boundary, accepts future timestamps, and rejects NaN.
- Stale backend data calls UDP stale-clear before glass discard. Either failure
  short-circuits subsequent cleanup. Direct clear always discards glass first,
  including a timeline no-op, and rechecks timeline after that callback. Mode,
  capture/display/inference counters and luminance survive clear.
- Switching webcam → backend → synthetic → webcam preserves independent capture
  and display counters. The previously consumed webcam sequence is still rejected.
  The authoritative deploy state is rechecked after its native-frame callback.

A worthwhile pure owner can centralize normalization, source eligibility,
freshness, duplicate rejection and counters. It must expose staged decisions and
publication points. A single precomputed source/frame transaction loses the above
callback observations; image construction, vision submission, luminance and glass
remain adapter effects. This is characterization, not approval of every quirk.

## Reproduction and results

Use `/workspace/shared/client-quality-venv/bin/python`, a temporary
`POLAR_USER_DATA_ROOT`, `POLAR_AUTOSTART=0`, `CI=true`, `QT_QPA_PLATFORM=offscreen`,
`POLAR_DISABLE_LOCAL_CAPTURE=1`, `POLAR_DISABLE_MEDIA_PRELOAD=1`, and
`XDG_CACHE_HOME=/tmp/polar-window-cache`.

- `python tests/test_normal_video_timeline_contract.py`: 30 PASS.
- With `PYTHONPATH=tests:$PWD`, `python -m unittest
  test_normal_video_timeline_contract test_window_normal_frame
  test_window_construction test_configuration_transactions`: 49 PASS, 0 skips.
- Direct existing smoke functions
  `test_hero_native_video_isolation_and_authoritative_lock` and
  `test_normal_backend_video_freshness_and_source_ownership`: 2 PASS.
- Six in-memory negative controls, leaving disk source unchanged, each failed its
  corresponding focused assertion: publish mode before clear; omit early capture
  consumption; omit pre-submit backend timestamp; collapse freshness source reads;
  accept an equal backend timestamp; skip glass discard on no-op clear.

Real PolarRMWindow construction/lifecycle, registry, Qt QImage/QPixmap, numpy
buffers and CPU glass are exercised. Native connection starts are inert; Python
connect/bind/sendto/address lookup are rejected by audit hooks. Expected offscreen
QOpenGLWidget/audio-device warnings remain visible. No physical camera, native
GPU presentation, inference worker, robot/network peer or release qualification
is established; no full aggregate run was performed for this checkpoint.

## Implemented owner and preserved contract

The extraction keeps all 30 original test methods and helpers unchanged and
adds one alias/owner-identity method. `NormalVideoTimeline` centralizes the five
provenance/timeline fields and their newness, freshness, readiness and conditional
retirement rules. Source normalization and repeated registry observations remain
in the adapters, as do Qt images, inference and cache effects. Its 18 pure tests
need neither Qt nor transport imports. The combined focused run passes 182
methods, both selected smoke functions, the actual API freeze and static gate.
The historical non-atomic/reentrant/NaN/empty-source behavior above is preserved,
not silently corrected or promoted to a stronger guarantee.
