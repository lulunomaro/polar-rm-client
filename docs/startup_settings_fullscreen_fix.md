# Startup settings and F11 (issue #12)

Base: `main` / `9599a1c5fd8742fd5dd53c1b57fec1effd4827bd`.
This is an independent fix; it does not include or merge the audio or fullscreen-composition PRs.

## Established causes and changes

- The startup `Config` and `ClickGUI` actions intentionally leave the launcher and start a scenario. There was no route to the genuine modal settings center. A separate **Settings** button now opens the existing center without changing `home_visible`, `startup_step`, selected scenario, or the operator menu. It is present on scenario, match, and robot pages, both GPU and CPU paths, and both `LiquidMenu` and `PolarLauncher` styles. Config/ClickGUI retain their existing purposes.
- There was no F11 event route. A window-owned application filter now handles F11 before page handlers, search fields, shortcut/binding capture, and owned modal dialogs. It consumes shortcut-override, press, and release events, but toggles only on a non-autorepeat press. QObject ownership is followed across dialog boundaries; unrelated windows are unaffected. The host's QObject lifetime owns the filter.
- F11 changes the actual Qt window state without saving a resolution, refresh, or quality preference. Windowed geometry is retained and a previously maximized window returns maximized. Returning directly to maximized is important: an intermediate `showNormal()` can corrupt restore geometry during queued Qt state changes. Video settings use the same transition helper; explicit windowed resolutions still unmaximize and resize as requested. The video page initially displays the actual current window mode.
- Before a transition, held vehicle inputs are released, control is locked, the mouse anchor and held map view are cleared, and cached compositor layers are invalidated. Nothing re-arms the vehicle. F12 remains handled by the existing emergency-stop routes. F11 is excluded from driver and new module binding choices. Historical module F11 strings are preserved in profiles but cannot actuate a module through the window's event route.

## Verification

Python 3.12, PySide6 / Qt 6.8.2, Linux `offscreen` platform; devices/native APIs were inert or mocked.

- New suite: **19 tests passed**, including repeated and rapid toggles, both views, resize/restore, maximized restore, text focus, legacy binding capture, overlays, nested modal dialogs, F12, foreign-window isolation, ownership/GC, settings close/accept/reject/Escape, and actual launcher render methods.
- Existing focused suites: **118 tests passed** (settings center 12, Jello keyboard/settings 15, Escape 13, blank-area dismissal 14, startup locale 10, UI state 30, GPU warmup 9, renderer caches 15). Each suite was run in its own process.
- Startup integration: **73 assertions passed**; backend integration: **51 assertions passed**.
- Unchanged API freeze: **1,132 members compared, no difference**.
- Negative control: the original main constructor and launcher renderers failed all 16 applicable integration assertions (no Settings hotspots and no installed window shortcut owner); the patched versions pass.
- Independent review found no blocking defect. Extra real-Qt checks passed for parent-only filter lifetime after GC, two independent hosts, and an active combo popup. A combined-process run of five adjacent suites produced 63/64 passes and a checkbox-animation timing failure; the exact same failure reproduced from untouched baseline snapshot `c8501fb6556d052decd126ef331a1c8365a8601d`. Isolated suite runs pass, so this pre-existing combined-order issue is not claimed fixed here.
- Syntax compilation and `git diff --check` passed.
- Aggregate `tools/verify_all.py`: **0 FAIL, 79 INCOMPLETE / 88 checks** on the materialized source subset. Missing unrelated suites, firmware and audit inputs prevent a full-repository pass. An earlier attempt lacked `polar_integration_test.py`; after materializing the exact upstream fixture, the startup suite and aggregate rerun passed all available checks.

Reproduce the focused suite in a normal checkout with dependencies installed:

```sh
QT_QPA_PLATFORM=offscreen POLAR_DISABLE_MEDIA_PRELOAD=1 python tests/test_startup_settings_fullscreen.py
```

For API freeze, use a fresh `POLAR_USER_DATA_ROOT`; pre-existing local preferences are intentionally not the immutable baseline.

## Integration with open PR #14

PR #14 head: `dfe06e4ebff1276eb9a11111d1b47bfbbec209f0`.
The two branches overlap in `cs2_video_settings.apply_window_settings`; do not resolve that conflict by discarding either behavior.

Minimal resolution:

1. Keep this PR's shared `set_fullscreen` call, pre-transition input/cache cleanup, actual-mode display, and explicit-resolution handling in `cs2_video_settings.py`.
2. Keep PR #14's `fullscreen_composition.py`, native-style tests, video-settings border disclosure, and `PolarRMWindow.resizeEvent` invalidation-before-Qt ordering.
3. Move PR #14's guard installation/application into the fullscreen-entry branch of `window_shortcuts.set_fullscreen`, surrounding its `showFullScreen` call. This covers both F11 and settings entry:

```python
from .fullscreen_composition import install_fullscreen_composition_guard
guard = install_fullscreen_composition_guard(host)
host.showFullScreen()
if guard is not None:
    guard.apply()
```

4. Keep both new suites in `tools/verify_all.py`. Update the two PR #14 application-order test doubles for the shared helper's `isFullScreen`, `normalGeometry`, and `isMaximized` contract and its pre-transition invalidation; native ABI, guard lifecycle, and resize-order assertions remain unchanged.

This exact explicit resolution was exercised in a **separate temporary source tree**, using PR #14 source files verified against their remote blob hashes. **92 tests passed**: issue #12 (19), composition guard (15, with the two interface/order fixtures adapted), an additional F11/settings-to-native-guard integration (1), settings center (12), UI state (30), and renderer caches (15). The integration check toggled each view three times, verified guard calls only after fullscreen entry, kept normal geometry after exit, and covered settings-to-F11 exit. Native Win32 calls were mocked. No shared branch or main branch was merged or changed by this compatibility test.

## Remaining Windows acceptance

This environment cannot qualify a physical Windows display, native DWM/driver behavior, or real OpenGL presentation. Keep the PR in draft until those are checked:

1. On launcher scenario/match/robot pages, open Settings, edit/apply a preference, close through Return, Escape, or the window close action, and confirm the same launcher page remains.
2. Enter the operator view; confirm Escape → Settings still returns to the operator menu.
3. In both views, press F11 repeatedly, hold it to generate repeat events, and try it while a search field or binding editor is focused and while settings/nested dialogs are open.
4. Repeat from a resized normal window and a maximized window. Verify normal size/position, layout, and maximized restoration; then apply an explicit windowed resolution.
5. Verify F12 remains functional and control stays locked until the operator deliberately re-arms it. Validate multi-monitor/DPI behavior on the target setup.
6. If PR #14 is also integrated, run its separate physical fullscreen/composition checklist. Offscreen state tests do not establish a flicker fix on a real GPU.
