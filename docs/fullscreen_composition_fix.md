# Fullscreen composition compatibility repair

Base: `9599a1c5fd8742fd5dd53c1b57fec1effd4827bd` (main, 2026-10-15).
This change is independent of the repeated-kill audio fix.

## What was established

The application uses the same GPU compositor in windowed and fullscreen modes.
`apply_window_settings()` enters fullscreen through Qt's `showFullScreen()`.
No existing native fullscreen-border workaround was found in `rm_client`.

Two targeted changes address different risks:

1. **Confirmed resize-cache ordering defect.** Qt 6.8.2's
   [QOpenGLWidget resize handler](https://github.com/qt/qtbase/blob/v6.8.2/src/openglwidgets/qopenglwidget.cpp#L1361-L1379)
   recreates its framebuffer and synchronously invokes painting. The client
   previously invalidated caches only after calling that handler. With
   Escape/chat/radial overlays active, the cached motion-layer readiness could
   remain true while `ensure_size()` allocated a new, uninitialized dynamic
   texture. The regression fails against the original method and passes after
   invalidating all scene metadata before Qt's resize handler. This establishes
   a transition-frame defect, not a diagnosis of every steady-state flicker.

2. **Windows-only fullscreen compatibility guard.**
   [Qt's Windows documentation](https://doc.qt.io/qt-6/windows-issues.html#fullscreen-opengl-based-windows)
   documents an OpenGL/fullscreen DWM composition limitation affecting other
   top-level windows, including menus and dialogs. Its prescribed workaround
   adds a native `WS_BORDER`. The guard follows that recipe after fullscreen
   entry, and rechecks state/native-handle changes. It is idempotent, preserves
   unrelated style bits, skips hidden/minimized/windowed windows, uses checked
   pointer-width Win32 calls, and lets Qt restore the normal window style.
   It deliberately does not force `SWP_FRAMECHANGED`: Qt's private fullscreen
   margin bookkeeping can disagree with a native client-size change.

The compatibility guard may produce a **one-pixel edge in Windows fullscreen**;
this is also disclosed in the video-settings page. It does not disable glass,
change shaders, cap FPS, change VSync/MSAA preferences, or select a CPU renderer.
Control lock/release still runs before any display-mode change.

## Verification completed

Environment: Python 3.12, PySide6/Qt 6.8.2, Linux offscreen Qt. Win32 calls were
mocked; no physical Windows GPU/display was available.

- `tests/test_fullscreen_composition.py`: 15 tests passed, including the real
  QWidget fullscreen/normal/minimize/restore state sequence and queued teardown,
  with only the native style operation mocked in that lifecycle test.
- `tests/test_cs2_settings_center.py`: 12 tests passed.
- `tests/test_ui_state_regressions.py`: 30 tests passed.
- `tests/test_gpu_warmup_budget.py`: 9 tests passed.
- `tests/test_render_performance_regressions.py`: 15 tests passed.
- `tests/api_freeze_test.py`: unchanged baseline passes, 1,132 members compared.
- Syntax compilation and `git diff --check` passed.
- Independent review found no blocking issue in the implementation.

The new suite is registered in `tools/verify_all.py`. The aggregate command was
invoked on the materialized source subset and reported **NO-GO / incomplete**
because unrelated test suites, firmware checks and audit inputs were absent.
This is not a full repository verification pass. Offscreen OpenGL context
creation failed, so these tests do not prove Windows presentation or resolve
all possible fullscreen flicker. No audio changes are included.

## Windows validation still required

On the affected Windows PC, use the normal source launch workflow and confirm:

1. Windowed baseline remains stable. Enter fullscreen from video settings;
   observe startup, battle HUD and moving video for several minutes.
2. Open and close Escape, chat, the radial wheel and settings while fullscreen.
   Toggle windowed/fullscreen repeatedly with each overlay open, including a
   resize while the motion layer would otherwise be cached.
3. Alt-Tab, minimize/restore, and repeat on each monitor/DPI scale used in
   practice. Dialogs must remain visible and interactive. Confirm the expected
   thin edge and that normal-window geometry/style restore correctly.
4. Verify control stays locked after video-settings application; mode changes
   must never re-arm or resume stale inputs.
5. Check logs for `Windows fullscreen composition guard failed`. If steady
   flicker remains, collect `renderer_init` and `frame_timing` records and a short
   recording distinguishing whole-window blackouts from individual HUD layers.
   Compare the same scene at a similarly sized windowed resolution.

Do not use Win32 `PrintWindow` screenshots as proof that an OpenGL surface is
blank; use the project's framebuffer capture workflow for renderer evidence.
