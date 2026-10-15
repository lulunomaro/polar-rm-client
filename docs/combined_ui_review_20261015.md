# Combined client UI repair review

## Exact source and integration

This branch is a single combined candidate based on main
`9599a1c5fd8742fd5dd53c1b57fec1effd4827bd`. It includes these existing drafts:

| PR | Verified source head |
| --- | --- |
| [#13 repeated kill audio](https://github.com/lulunomaro/polar-rm-client/pull/13) | `066fda7a11a73c7c5e50dca3786f8198bc182482` |
| [#14 fullscreen composition](https://github.com/lulunomaro/polar-rm-client/pull/14) | `dfe06e4ebff1276eb9a11111d1b47bfbbec209f0` |
| [#15 startup settings/F11](https://github.com/lulunomaro/polar-rm-client/pull/15) | `cfd45d69766ba673dfb391f340822527a7a3fc5a` |
| [#16 first settings/atlas warmup](https://github.com/lulunomaro/polar-rm-client/pull/16) | `dd33ff54768b4f7de9d33cb29dd5880d6be85ad3` |

Every materialized unchanged source file and each original PR file was checked
against its upstream Git blob ID before integration. The shared fullscreen
helper retains #15 geometry restoration and applies #14's Windows guard before
and after `showFullScreen`. Keyboard hints retain F11 and lazy styling; the
aggregate keeps every test registration. Older test doubles now expose the
shared helper's window API, and the resolution assertion uses physical/DPR units.

The candidate is a **superset**, not an extra patch to apply after all four old
drafts. The old PRs and main are unchanged; no merge, close or deployment is part
of this review.

## Further defects reproduced and repaired

1. **Stale kill cues after mute/focus transitions or close.** Both the decode
   waiting queue and playback queues are cancelled together. Requests are gated
   before cold decoding; close stops active players and refuses new requests.
   [Audio evidence](audit_audio_settings_focus_20261015.md).
2. **Cross-tab preference overwrite and incomplete crosshair reset.** Apply sends
   actual edits only. Clean fields follow live values; dirty drafts survive tab
   changes. Reset stages explicit defaults and restores the display scale.
   [Settings/crosshair evidence](settings_crosshair_review.md).
3. **Stale framebuffer pixels on DPR/storage changes.** A successful storage
   generation change invalidates all scene metadata even without resizeEvent.
   A failed control paint target now enters frame recovery rather than presenting
   untouched storage. [Framebuffer evidence](framebuffer_cache_audit.md).
4. **F11 replays stale mouse gestures.** Fullscreen transitions cancel pending
   background/module/slider/menu presses while retaining release ownership, so
   the delayed release neither changes a module nor leaks into driving input.
5. **Jello text history overflow and false cache hits after failed upload.**
   Immutable shaped pages prepare only missing lines, retain bounded history,
   and publish coverage atomically. Lossless width-aware module/help wrapping
   allows all 74 default modules at DPR 1 and 2. The whole help card owns wheel
   input, including short hints. [Atlas evidence](jello_atlas_pages.md).
6. **CS crosshair too small, sometimes invisible, and clipped at large scale.**
   CS presentation is now 2x while raw imported geometry, saved Scale values and
   share codes stay unchanged. Positive strokes keep at least one physical pixel.
   Preview starts at physical 1:1. Actual bounds drive padded, viewport-clipped
   raster tiles of at most 512x512, rather than the unrelated combat-ring crop.
   RM Dot/Circle/Cross and active CombatTargetHUD retain their rendering path.

Default CS outer bounds at physical 1080p/1440p/2160p are 36/48/72 pixels at both
DPR 1 and 2, previously 18/24/36. The existing Crosshair Scale slider remains
adjustable; 0.5 restores the earlier reference size without rewriting a profile.

## Verification

Final verification: **290 combined tests pass**, plus **53 tests at process DPR 2**.
API freeze compares 1,132 members without a baseline change. Startup has 73
passing assertions and backend integration has 51. The aggregate is **0 FAIL,
78 INCOMPLETE / 98 checks**, exit 1, because unrelated inputs are absent.

Final results and reproducible commands are recorded with this candidate. The
focused run uses real Qt 6.8.2 offscreen widgets, original repository fonts and
inert device adapters. Native Windows APIs and GL uploads are explicitly mocked.

```sh
export QT_QPA_PLATFORM=offscreen
export POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1
python -m unittest discover -s tests -p 'test_*.py'
python tests/api_freeze_test.py
python tests/polar_startup_test.py
python tests/polar_integration_test.py
python tools/verify_all.py
```

For the isolated materialized source subset, the exact focused suite list and
results are in `evidence/combined-review-20261015/results.json`. This is separate
from a full repository/firmware pass. The aggregate reports missing unrelated
inputs as INCOMPLETE, never as success.

The final combined real-Qt audio probe also passes: all four stale mute/focus
cases emit zero PCM, native CS2 emits four normal cues at gain 0.33, and twelve
production preview cues survive settings dismissal plus four F11 transitions.
Warm close leaves the actual QMediaPlayer stopped and rejects further play.
See `evidence/combined-review-20261015/audio-pcm.json` for controlled seams.

Independent reviews include repeated modal open/close and 100 rapid F11 pairs,
F12 during lazy construction, DPI/geometry restoration, dirty draft preservation,
audio PCM capture, failed atlas publication/eviction, and crosshair pixel parity.
Default text histories and every English/Chinese description are exercised at
original font density. A prior state had 269 passing combined tests before the
last CS sizing, long-description and control-target recovery changes.

## Qualification limits

- No usable OpenGL context, Windows desktop, physical sound device or vehicle
  was available. Raster/command/PCM tests do not certify DWM, GPU driver latency,
  actual audible output, hardware leak behavior or robot operation.
- Settings construction measured about 1,019 to 117 ms in the earlier identical
  real-asset comparison. Jello sequential module preparation now has no default
  page failure, but a CPU callback still reached 108 ms at DPR 2. Soft scheduling
  budgets are not a hard frame-time guarantee.
- Extreme offscreen CS geometry can still consume CPU raster time even with no
  upload. Tiles bound temporary allocation and preserve visible pixels; this is
  not a blanket performance claim for every imported profile.
- Keep the draft until target-machine fullscreen/DPI, first-Esc/settings latency,
  crosshair appearance and repeated kill sound have been checked. No rendering
  quality, animation duration, telemetry cadence or control authorization was
  reduced to obtain these results.
