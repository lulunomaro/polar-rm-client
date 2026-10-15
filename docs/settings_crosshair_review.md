# Settings lifecycle and crosshair review

## Saved fixes

- `4d50448d6f087d27e10327c3cb4e2f3c6de8006a`: preserve independent settings drafts; reset crosshair display scale.
- `3f26030a05e5ee7ac8a70165cb48265ba25b122e`: keep positive CS strokes visible; align preview with physical viewport dimensions.
- `58aae342036904b93ff4420656efb83acde5ebc9`: magnify the target pixel floor with preview inspection zoom; support positive sub-1 DPR.
- `d16ce8acecd773d583145dd1286359408e90f7ec`: enlarge CS-only presentation 2× and fix GPU crosshair clipping with bounded uploads.

These were independently reviewed development checkpoints and are included in the combined repair branch. They were based on the exact published PR13–16 composition; main was not merged.

## Reproduced defects

### Unrelated Apply can revert a newer preference

1. Open Interface / HUD, allowing the page to load.
2. Open Audio. Turn off HUD / Jello List Sound and Apply.
3. Return to Interface / HUD. Change only ClickGui Scale, then Apply.
4. Before the fix, Jello List Sound silently becomes true again.

The same preference is exposed by two page snapshots. The fix submits only actual edits, follows current live values for clean controls, retains dirty drafts across tab switches, and treats Restore Defaults as an explicit edit even where a default equals an old snapshot. Audio edits are merged with the newest independent audio preferences.

### Crosshair Restore Defaults leaves overall scale unchanged

Set Crosshair Scale to 3, open the Crosshair page, restore defaults, and Apply. Before the fix the scale remains 3. It now stages the registry default 1, with no live mutation until Apply. Cancel discards it.

### Default CS crosshair can lose all colored pixels at 720p

The default one-pixel thickness is multiplied by 720 / 1080, producing a 0.667-pixel strip. With aliased QPainter paths, all colored strips disappear; only a 12 × 12 black outline remains. The fix keeps positive thickness at least one physical destination pixel after accounting for HUD scaling, preview zoom and DPR. Explicit zero thickness and transparent profiles remain invisible.

Real Qt raster measurements, identical at DPR 1 and 2:

| Physical height | Outer bounds | Colored pixels before | Colored pixels after |
| --- | --- | --- | --- |
| 720 | 12 × 12 | 0 | 12 |
| 1080 | 18 × 18 | 20 | 20 |
| 1440 | 24 × 24 | 56 | 56 |
| 2160 | 36 × 36 | 80 | 80 |

This stage restores visibility without changing imported geometry or making all HUD elements larger.

### Preview exaggerates actual display size

The editor previously started at a 3× zoom and a 1080-height virtual viewport regardless of the application's actual dimensions. The selected height was also interpreted as logical pixels, so a 1080-height preview at DPR 2 was physically twice the corresponding 1080p HUD size even at 1× zoom.

The fix starts at 1× zoom, selects the current host physical viewport height, labels the unit, and converts that physical height to the preview widget's logical coordinates once. Explicit preview magnification remains available. Share-code contents and the saved display-scale preference are unchanged.

## Coverage

- 21 new real-Qt settings tests pass: rapid tab changes, ready-only Apply, independent drafts, clean-control refresh, reset/cancel, search during loading, repeated reopen/F12, partial construction cancellation/deletion, and injected row/editor/style preparation failures.
- 59 existing settings, cold-path, keyboard and startup/F11 tests pass.
- 11 crosshair/editor tests pass at process DPR 1 and 2, including 60 resolution/DPR/display-scale combinations, all ten static styles, HUD transforms, explicit zero/transparent profiles, profile/share-code preservation, and preview dimensions/zoomed physical stroke bounds.
- The exact pre-fix crosshair code fails 40 subcases in that 10-test suite. The two settings regressions fail before the state fix.
- API freeze passes without updating its baseline.
- Independent review passed 43 settings/crosshair tests plus 47 keyboard/cold-start/startup tests; no settings state regression was found. It caught a preview-floor zoom issue, which the third commit fixes and covers with actual Preview.render pixel checks at zoom 1/2/3/4/8.
- The settings shell still constructs 179 widgets. A local timing probe found no return to eager page construction; its fonts were fallbacks, so its milliseconds are not a target-machine performance claim.

## Requested enlargement and GPU clipping fix

The final fourth commit implements the explicitly approved CS-only 2× presentation baseline. It does not change the saved Crosshair Scale value, imported profile, share code, normal RM styles, CombatTargetHUD, or FOV. Setting Crosshair Scale to 0.5 restores the earlier reference dimensions, while retaining the positive one-physical-pixel stroke floor.

Final default physical outer bounds, equal at DPR 1 and 2:

| Physical height | Previous reference | Final default | Final with Scale 0.5 |
| --- | --- | --- | --- |
| 720 | 12 × 12 | 24 × 24 | 12 × 12 |
| 1080 | 18 × 18 | 36 × 36 | 18 × 18 |
| 1440 | 24 × 24 | 48 × 48 | 24 × 24 |
| 2160 | 36 × 36 | 72 × 72 | 36 × 36 |

The old GPU patch size came from CombatTargetHUD rings. At 4K / DPR 1 / HUD scale 1.34, its 204-pixel patch clipped the old roughly 288-pixel Scale 8 crosshair; the new doubled crosshair needs roughly 576 pixels. Regression tests fail against commit 58aae34 before the new fix.

The CS path now records the actual vector primitives in physical coordinates once, pads their bounds and intersects the viewport, then rasterizes bounded tiles. Each temporary raster allocation is at most 512 × 512; the 508-pixel tile core includes a 2-pixel rendering guard to prevent curve alias seams. Transparent tiles are skipped and sparse uploads are cropped with padding. The existing target FBO receives glTexSubImage2D updates, with no new texture allocation. Actual active CombatTargetHUD stays on its original direct raster path; presence/interpolation updates once per frame.

Independent review caught and resolved two candidate regressions before commit: recording target text in QPicture lost glyphs, and large offscreen profiles wasted transparent full-frame-equivalent uploads. Active HUD is now excluded from recording, and empty tiles upload zero bytes.

## Final verification

- 112 settings, keyboard, startup, crosshair and target-bounds tests pass in one real-Qt offscreen run (46.153 seconds).
- All 22 crosshair tests also pass in a fresh process at QT_SCALE_FACTOR=2.
- Actual preview render checks pass at DPR 0.75, 1, 1.25, 1.5, 2 and 2.5, including zoomed minimum-pixel strokes.
- Independent 100 randomized CS cases match direct raster exactly across all ten styles, fractional DPR/HUD transforms, large/negative gaps, length 255, thickness 32, Scale 8 and outline 127.5.
- Independent 54 active TargetHUD cases match the previous committed GPU method pixel-for-pixel, across Classic/Clear/Enhanced, bars on/off, visible/grace/fading, and fractional DPR/HUD scale.
- Tests execute the actual GPU compositor upload method against a GL command recorder: subimage dimensions and payloads are bounded and valid, premultiplied channels respect alpha, clearing occurs once, and no glTexImage2D texture allocation occurs.
- API freeze passes unchanged; git diff --check passes; the worktree is clean after commit.

Diagnostic local CPU-plus-sanitization measurements, not GPU benchmarks: default CS ~0.41 ms, Scale 8 cross ~5.05 ms; a fully offscreen gap profile improved from ~141 ms / 33.18 MB uploaded to ~4.20 ms / 0 bytes. An extreme offscreen circle still takes ~15.43 ms to rasterize bounded tiles but uploads zero bytes. Hardware/driver performance and visual acceptance remain target-machine checks.

## Reproduction artifacts

- Patch: /tmp/cs-crosshair-2x-bounded-gpu.patch
- Full regression log: /tmp/audit_settings_crosshair_final.log
- Fresh DPR 2 log: /tmp/audit_cs_crosshair_final_2x_dpr2.log
- Pre-fix sizing/bounds failures: /tmp/audit_cs_size_bounds_negative.log
- API result: /tmp/audit_cs_crosshair_final_api.log
- Final physical pixel measurements: /tmp/cs-crosshair-2x.json
- Independent randomized renderer: /tmp/review_crosshair_tiles.py
- Independent old/new target-HUD comparison: /tmp/review_target_legacy_parity.py
- Diagnostic upload-cost harness: /tmp/review_crosshair_cost.py

Run tests from the isolated tree with:

```sh
PYTHONPATH=/tmp/kill-audio-qt:.:tests XDG_CACHE_HOME=/tmp/polar-cache \
QT_QPA_PLATFORM=offscreen POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1 \
python -m unittest test_cs_crosshair_target_bounds test_cs_crosshair_visibility \
test_lazy_settings_state test_cs2_settings_center test_settings_cold_start \
test_jello_settings_keyboard test_startup_display_locale test_startup_settings_fullscreen -v
```

No Windows DWM, physical GPU output, monitor, speaker or vehicle acceptance is claimed. The real Qt tests ran offscreen with inert external adapters.
