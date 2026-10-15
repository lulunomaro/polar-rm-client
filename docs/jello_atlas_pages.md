# Bounded immutable Jello text pages

## Verified defects

The renderer repacked all historical lines for the same font/DPR whenever a
new module added a label. The 2,048-line count limit did not guarantee that the
result fit into its single 4,096-pixel texture. Successive module settings could
therefore block the GUI thread for hundreds of milliseconds and eventually
raise before the selected settings panel opened.

Both glyph and shaped caches also advertised new characters/lines before a
texture upload succeeded. An overflow, locked-pass rejection or upload failure
could leave the old texture paired with false coverage, so a subsequent retry
reported a cache hit for missing text.

## Change

- Shape and rasterize only missing lines into immutable pages. Existing glyph
  pixels, metrics, full-line shaping, original fonts, oversampling, filters,
  alignment, animation and opacity remain unchanged.
- Target at most 64 lines / one million source pixels per new page. Retain
  at most 64 pages, 2,048 lines and four million source pixels per exact
  font/DPR key, except when pages needed by the current request exceed those targets. Evict whole
  old, unrequested pages, including their cached QTextLayouts.
- Preserve command order and batch adjacent commands only when they use the
  same page. Page splitting can add draw calls; actual GPU impact is unmeasured.
- Publish textures and known coverage together. Failed multi-page preparation
  destroys newly created textures while keeping the prior snapshot valid.
- Preserve every currently requested glyph when pruning ordinary glyph pages.
- Wrap module descriptions at the existing panel's 440 logical pixels and
  setting help at its 460-pixel inner width. Presentation and prewarm use the
  same lossless Qt line breaks, original fonts and unchanged DPR. UTF-16-aware
  slicing preserves supplementary characters and grapheme clusters.
- Include module descriptions in settings scrolling. Clamp the help card to
  the viewport and allow exceptionally long help to scroll while hovered.
  Mixed CJK fallback heights determine row spacing, avoiding clipped strokes.
  No description text is elided, truncated or downsampled.

## Reproduction and results

Fresh offscreen Qt 6.8.2 processes, actual bundled fonts, default 74-module
registry, 1280x720 layout. Atlas CPU/raster work is production code; only
QOpenGLTexture allocation/upload is stubbed. Context creation returned false.
The startup queue is completed before opening modules in registry order.

| CPU/raster module preparation | Before DPR1 | Final DPR1 | Before DPR2 | Final DPR2 |
|---|---:|---:|---:|---:|
| Successful module preparations | 63/74 | 74/74 | 18/74 | 74/74 |
| Median successful preparation | 102.8 ms | 4.7 ms | 300.0 ms | 12.7 ms |
| Maximum successful preparation | 247.6 ms | 82.0 ms | 567.2 ms | 108.1 ms |

These are single-process sequential-browsing measurements, not hard callback
budgets, first-Esc timings or GPU frame rates. Cache history is intentionally
part of the reproducer. Raw data: `benchmarks/jello_atlas_pages_qt682.json`.

Run each source revision in a fresh process with the same assets:

```sh
QT_QPA_PLATFORM=offscreen python tools/profile_jello_atlas.py --source /path/to/revision --module-pages
QT_QPA_PLATFORM=offscreen python tools/profile_jello_atlas.py --source /path/to/revision --module-pages --dpr 2
```

## Tests and remaining limits

The focused run passes 85 tests covering immutable pixels, historical overflow,
coverage rollback/retry, partial upload cleanup, per-key eviction, adjacent draw
order, release-once ownership, existing cache guards, Esc input safety,
warmup pause/resume, settings input, and description layout. The new regression
prewarms all 74 default modules and prepares their actual English/Chinese draw
lists at DPR 1 and 2. It also covers complete text reconstruction, scrollable
help, full-card wheel and click ownership, small-window bounds and mixed CJK line heights. API freeze passes without a baseline change; the compositor
logic test also passes. The added tests are CPU/raster tests, not real GL tests.

The first immutable-page implementation still rejected AutoSpinOnHit and
RobotLink at DPR 2 because individual descriptions exceeded the 4,096-pixel
texture limit. The lossless width-aware wrapping follow-up resolves those two
remaining default-module failures. The raw data retains that intermediate run
as well as baseline and final runs. CPU raster previews of both panels were
visually inspected with original fonts; these are not GPU screenshots.

Per-module preparation can still exceed one display frame (108 ms in this probe),
and startup warmup retains its soft scheduling target. Hardware upload,
Windows CJK fallback, high-DPI driver timing and compositor rendering must be
measured on the target system before declaring hardware acceptance.
