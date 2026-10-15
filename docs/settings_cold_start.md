# First Esc/settings cold-path hardening

## Cause and scope

The settings center used to build all six pages, enumerate audio outputs and
create 1,618 QWidget descendants before the dialog could appear. It then applied
styles to embedded editors and repolished the complete descendant tree again.
The native Jello helper also created a second font bank on first use; missing
optional faces triggered recursive searches through the entire resource tree.
These are synchronous GUI-thread operations. A repeated open rebuilt everything.

The Esc menu is a distinct path. It does not construct settings until Settings
is chosen. The normal window has already loaded its CS2 pack, shaders and FBOs.
An isolated real-font raster menu paint measured 22.8 ms cold, then 2.1/2.0/2.0 ms;
this did **not** reproduce a separate seconds-scale GPU Esc stall. Optional
queued atlas preparation was still allowed to run during Esc/modal interaction.
The patch pauses that queue without consuming or discarding jobs; the existing
successful-frame resumption continues it after closing the menu/dialog.

No compositor invalidation, blur, shader, animation duration, rendering quality,
telemetry cadence or robot-control authorization has been weakened.

## Fix

- Construct the visible video page plus lightweight shells (179 widgets).
- Fill only the selected registry page, one row at a time, on the GUI thread.
  At most four steps run per turn, with a 3 ms **soft** scheduling target and
  an 8 ms yield. Hidden tabs and the cached entrance animation pause preparation.
- Split crosshair construction, styling and attachment into separate turns.
- Keep each page's staged edits when switching tabs. Apply/reset stay disabled
  until that page is complete. Closing cancels pending jobs; a new dialog starts
  from current live settings. Search applies before and after deferred loading.
- Prepare CJK style selectors before installing/inheriting the stylesheet;
  remove the redundant per-descendant unpolish/polish pass.
- Reuse the window's loaded font bank for the first native settings dialog.
  Standalone native helpers load only their three Jello faces. Font discovery is
  confined to `assets/fonts`, including nested font packs and the icon wildcard.
- Leave F12 processing live during preparation, including nested modal dialogs.

This does not simply move the entire stall to application startup. Unvisited
settings pages do no preparation work.

## Measured comparison

Fresh processes, same Python/Qt runtime, same exact repository assets and
1280x720 inert host. There was no OpenGL context, vehicle, camera or audio device.
All 56 materialized asset blobs matched their Git object IDs at main `9599a1c5`;
all nine bundled font files registered successfully. Linux CJK fallback is not a
claim about Windows YaHei rendering or font lookup cost.

| CPU/raster stage | Main | Patched |
|---|---:|---:|
| First settings build | 1,019 ms | 117 ms |
| First show call | 85 ms | 27 ms |
| Repeated settings builds | 779–905 ms | 64–100 ms |
| Widgets constructed before first show | 1,618 | 179 |
| First-tab selection handler | eager work paid on open | 0.5–2.6 ms |

First-tab completion includes cooperative waits: audio about 156 ms, game
232 ms, keyboard 348 ms, crosshair 221 ms and HUD 486 ms in the first run.
That is elapsed preparation time, **not one blocking GUI callback**. The first
run's largest ordinary game/keyboard/HUD slices were 5.0–5.4 ms. The audio backend
initialization/query took 32.7 ms in one indivisible step. Crosshair creation/style
steps reached 65.4 ms. Across all three patched runs an ordinary keyboard slice
also reached 17.1 ms. These overruns are recorded rather than hidden behind a
claimed hard 3 ms budget. Qt operations and the OS audio query are not preemptible.

Raw measurements: [settings_cold_start_qt682.json](benchmarks/settings_cold_start_qt682.json).
Reproduce on each revision in a fresh process, with identical assets:

```sh
QT_QPA_PLATFORM=offscreen python tools/profile_settings_open.py --source /path/to/revision
QT_QPA_PLATFORM=offscreen python tools/profile_settings_open.py --tabs
```

Use `--profile /tmp/settings.prof` for cProfile data. Do not compare profiled
wall-clock numbers with an unprofiled run or infer GPU frame rate from this test.

## Regression coverage and limitations

The focused/broader real-Qt run passes 132 tests across settings, keyboard,
Escape input safety, queued preparation, font caches, GPU warmup ownership,
startup labels, ClickGUI dismissal, UI state and compositor logic. Dedicated
checks cover selection during preparation, F12, draft persistence, search,
ready-only application, cancellation, fresh reopen, stage boundaries, hidden
owners and destruction of a crosshair editor before attachment. API freeze also
passes without a baseline update. Standalone startup and integration checks
pass 73 and 51 assertions respectively. Independent review found no blocking defect.

The repository-wide aggregate is **not a complete pass** in the isolated source
subset: most firmware and unrelated test scripts are not materialized. The
aggregate reports **0 FAIL, 79 INCOMPLETE / 88 checks** (exit 1), not GO. No real Windows/GPU latency, display-driver,
DPR/refresh-rate, audio-device or vehicle acceptance is claimed.

On the target computer, compare first Esc immediately after startup, first Esc
after atlas warmup completes, subsequent Esc, and first/settings tab opens.
Keep `ui_tick_timing`, `frame_timing`, warmup maxima and compositor dynamic/blur
stages distinct. A remaining GPU-specific first-Esc hitch needs that evidence;
this patch deliberately does not invalidate less scene state without proof.


## Compatibility with the separate startup/F11 PR

A disposable local composition with PR #15 passed all 82 combined startup/F11,
settings, keyboard, Escape and warmup tests. It did not merge or change either
published branch. There is one adjacent-line Git conflict in the keyboard hint:
retain PR #15's `F11全屏、F12急停...` text **and** this patch's following
`apply_jello_widgets(note, stylesheet=False)` call. The PolarRMWindow changes
and aggregate-test registration combine cleanly. PR #15's fullscreen shortcut,
startup settings entry and reserved-key behavior are not included in this
independent PR against main.
