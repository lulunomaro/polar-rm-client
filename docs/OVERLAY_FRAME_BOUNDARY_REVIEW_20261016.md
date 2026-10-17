# Cached overlay frame boundary review — 2026-10-16

## Result and exact scope

Candidate source: `ba96e42d5ec20c95acb4b9b4404f423912e6da76`.
Reference source: `71ebb0f570441c12eb0d15b0a992c9cc3e805aeb`.
The exact clean candidate passed **252 unittest methods across 17 focused suites**, the
unchanged historical API freeze, and the unchanged static quality gate. See
[results and per-suite logs](evidence/overlay-frame-boundary-20261016/results.json),
[architecture evidence](evidence/overlay-frame-boundary-20261016/architecture-summary.json),
and [the measured hotspot report](evidence/overlay-frame-boundary-20261016/architecture.md).

This is actual Python/Qt raster and command-contract evidence with controlled native
GL edges. It is not an aggregate `verify_all`, Windows/DWM, physical-device, native
GPU pixel, performance-budget, or release qualification. The parent integration
must rerun its combined checkpoint at the resulting integrated source.

## Responsibility and state ownership

The original 353-line `PolarRMWindow._paint_gpu_frame_cached` mixed source drawing,
layer scheduling, cache decisions, resource handling, and presentation. The new
`polar.overlay_layers` boundary owns only the static/overlay/dynamic layer schedule.
It never receives a window and has no arbitrary attribute-forwarding proxy.

- `FrameViewport`: frozen, validated logical dimensions and finite positive DPR;
  both logical and rounded physical dimensions fit Qt's integer storage.
- `OverlayLayerPlan`: frozen scene, generation, explicit static mode, cache keys,
  renderer selection, and motion inputs. Contradictory startup/economy/ClickGUI
  ownership is rejected before drawing.
- `LayerCacheSnapshot`: frozen published keys, motion readiness, and timestamp.
  The executor works on replacement values. Mutable key payloads, nonfinite or
  nonrepresentable timestamps, and non-boolean ownership are rejected.
- `OverlayDrawPorts`: nine named callbacks for retained layout/painter work:
  clear startup interactions, startup commands, startup interaction, static HUD,
  economy commands, ClickGUI frame, motion HUD, priority interaction, target HUD.
- `OverlayLayerRenderer`: borrows the narrow compositor/command-renderer protocols
  and a presentation clock. It retains no window, generated frame, or source cache.
  Metadata returns only after accepted layers and matching framebuffer generation;
  a generation change during atlas preparation cannot reach motion/target drawing.
- The window retains context admission, background/control/vision sources, existing
  source-specific caches, adapters, final presentation, and restore generation commit.
  Layer metadata is applied at the historical point before presentation; any failed
  frame invalidates it. Economy restore commits only after accepted presentation and
  the matching generation. Rejected/stale restore attempts remain pending.
- The existing `LayerPaintSession` owns painter and per-layer cleanup. Motion drawing
  registers explicit command-collection discard before its first collection begins.
  The window still owns the compatibility collection fields; no constructor or state
  proxy was introduced. Discard disables every capture flag before fallible clearing,
  submits no partial command batch, and allocates no successful-frame replacement lists.

The retained window adapters still reach application/painter state. Startup's legacy
layout still publishes hotspot geometry while building; this change does not claim
transactional isolation for every historical layout. ClickGUI's pre-existing isolated
command/interaction frame remains responsible for its own publication rules.

## Characterization before extraction

`f9c4207` committed the original frame method as an exact source fixture before the
extraction `a65c982`. `e7b30f6` later added the three original flush methods without
changing that frame body. Source SHA checks protect all four original method bodies.

- Frozen/current production entrypoints run 16 scene families through three normal
  frames, 23 exception/recovery sequences, and 12 False/retry sequences: 118 frames
  per implementation. Ordered pass/owner/cache/interaction traces are compared.
- `494d710` added actual constructed-window comparisons. Eleven scenes, two logical
  size/DPR configurations, cold/warm caches, and both implementations give 88 frames.
  Ordered command payloads, interaction and scroll geometry, glass commands, cache
  metadata, and all six QImage layer hashes match. Actual restore and economy rejection
  tests exercise the post-present owner rather than replacing it with a mock.
- Only native GL allocation/submission is replaced in these actual-window comparisons.
  Fonts, layouts, QPainter drawing, source caches, interaction builders, pass sessions,
  and window lifecycle execute. Fixed presentation origins avoid comparing incidental
  clock drift; they do not replace backend/control clocks.
- Five compositor GLSL string literals have identical SHA-256 values at both source
  revisions. Other shader-bearing production files were not edited.

## Two separately reproduced behavior corrections

### 1. Child command False was falsely accepted

Red: `e7b30f6`; initial correction: `e26a808`.
The frozen real window accepted and presented a HUD frame after its command renderer
returned False, marked the motion cache ready, and could finalize economy restore.
The actual renderer's caught `TypeError` likewise failed to reach the frame owner.

HUD, island, wheel, and wheel-label flush methods now register a child rejection.
Their historical `None` return contract and normal flush ordering remain unchanged.
The motion callback returns the compositor's rejection state, preventing layer cache,
final presentation, and restore publication. Retry succeeds without changing the saved
Render Backend preference. Renderer fault diagnostics remain with the renderer;
ordinary False does not invent an exception counter.

A mixed child False + FBO release False red sequence was committed at `b411251` and
fixed at `318ec2e`. Existing registered child reasons now survive later framebuffer or
vision cleanup rejection while independent rejected-layer evidence remains visible.
This is a **first registered child cause** guarantee, not a universal earliest-operation
rule: a raw generic paint callback returning False without registering a reason can
still receive a later cleanup reason before its generic pass reason is recorded.
Typed draw/cleanup exceptions retain their separate existing diagnostic ownership.

### 2. Abandoned collections swallowed raster fallback text

Initial red: `e12ec8d`; corrected native-edge fixture/red: `df8565d`;
correction: `615aed5`.
The frozen original leaves GPU collection flags active after an interrupted HUD painter.
Its actual `draw_glass_text` then collects commands instead of drawing into the fallback
QImage, producing a completely blank text image.

The corrected red fixture adds an inert FBO texture handle so the wheel fault injection
actually reaches its renderer. It produces 11 genuine failures: ten leaked-collection
stages and the combined cleanup case. The wheel-render exception is reached but already
passes because that existing flush detaches its buffers before native submission.
The initial missing-handle failure is not counted as a product defect.

The fixed suite covers eleven acquisition/draw/flush stages plus recovery, actual raster
fallback pixels, original draw + painter/FBO cleanup faults, and an intentionally failing
command discard. All capture flags are disabled before fallible collection cleanup;
independent layer cleanup still runs and the original typed draw fault survives.

## Static measurements, without relocation credit

The frame entry moves from 167 statements / 69 decisions / depth 5 / 353 lines to
97 / 46 / depth 3 / 238 lines. Nested adapter bodies are measured separately in the
artifact; these figures alone are not the acceptance argument.

Whole-tree measurements **increase**: 190 to 191 Python modules, 3,211 to 3,242 functions,
36,668 to 36,797 executable statements, and 12,901 to 12,970 decisions. There are zero
module-initialization cycles in both trees. The accepted change is explicit ownership
and fault-safe behavior, not a claim of whole-project complexity reduction.

No API snapshot or quality baseline was relaxed. The constructor, economy scene
implementation, core model, referee, key handlers, prewarm scheduling, GL texture-unit
restore code, and shader bodies were not edited by this branch.

## Reproduction

Use Python 3.12 with the repository requirements installed (the recorded run used
PySide6 6.8.2). Set `CI=true`, `QT_QPA_PLATFORM=offscreen`,
`POLAR_DISABLE_LOCAL_CAPTURE=1`, and `POLAR_DISABLE_MEDIA_PRELOAD=1`.
Run each `test` path listed in `results.json` with that Python interpreter. The 18
entries include the standalone historical API freeze, which is not a unittest count.
Then run `python tools/quality_gate.py --json <output.json> --markdown <output.md>`.
Do not regenerate API snapshots or quality baselines to make these checks pass.

Expected offscreen/audio/font warnings are retained in the full logs. They are not
native desktop/GPU qualification. Raw red logs are preserved beside the final logs.
