# Private-framebuffer cache ownership audit

## Reproduction and fix

The combined PR13–16 source at `fdc665272d205edf26785e454760027a61c70d3c`
retained `_gpu_motion_layer_ready` when `ensure_size` replaced the private FBOs
without a logical resize event. With the radial or a student panel open, the
motion layer is intentionally frozen; it therefore reused an unpainted new
texture. Chat and Escape could do so until their 250 ms refresh deadline.

This is a distinct path from PR14's correctly ordered `resizeEvent` invalidation.
Qt 6.8.2 handles `DevicePixelRatioChange` directly in `QOpenGLWidget::event` and
recreates its FBO without dispatching `resizeEvent`:
https://github.com/qt/qtbase/blob/v6.8.2/src/openglwidgets/qopenglwidget.cpp#L1590-L1592

`GpuFrameCompositor.fbo_generation` now identifies the allocated storage and its
pixel-ratio interpretation. Successful allocations and a changed DPR with equal
physical dimensions advance it. `_paint_gpu_frame_cached` compares generations
around `ensure_size` and invalidates all scene keys before making reuse decisions.
The existing warm-frame cache remains unchanged when storage and DPR are stable.
No shader, blur parameters, color format, presentation policy, or visual design
changes were made.

## Evidence

`tests/test_framebuffer_generation.py` executes the production allocation,
frame-control and invalidation methods. It uses Qt 6.8.2 `QImage`/`QPainter` with
inert GL/FBO doubles, including an explicit never-drawn replacement marker.

Before the production fix: six ownership assertions failed (radial, student,
chat, Escape, a logical resize without its event, and same-size reallocation).
Two existing good-state controls passed. The generation-contract test errored
because that new field did not yet exist. After the fix: all ten tests passed,
including unchanged storage, first priority frame, failed allocations, and a DPR
change where physical dimensions remain equal.

Run with an installed PySide6 runtime:

    QT_QPA_PLATFORM=offscreen python tests/test_framebuffer_generation.py

Additional focused checks passed: renderer performance/cache (15 tests), UI state
(30), GPU warmup (10), startup/settings/fullscreen (19), and compositor CPU pixel
reference tests. The combined baseline's two known outdated fullscreen settings
fixtures still fail because their fake hosts lack `isFullScreen`; those are owned
by the integration coordinator and are unrelated to this fix.

## Verification boundary

This environment cannot create a desktop OpenGL context. These results prove the
cache ownership/control-flow defect and regression coverage, not hardware pixels,
DWM behavior, driver resource reclamation, or a target-PC frame-rate guarantee.
A real GPU validation should move an open radial/student/chat/Escape surface
between different-DPR monitors, including repeated moves and minimize/restore,
and verify the first presented frame.

Context replacement is a separate pre-existing lifecycle risk: the main window
has close-time cleanup but no `aboutToBeDestroyed` handler, while renderer
`initialize` methods may return early from old `available` state. No application
path that reparents the main window was found, and context loss was not reproduced
on hardware here; this is a source-level risk, not a confirmed new regression.

## Control-backdrop bind failure recovery

Fault injection found a separate pre-existing error-path defect:
`paint_gpu_control_backdrop` returned `False` for both an unchanged valid cache
and a failed `paint_device("control")`. Its caller interpreted the failure as
"unchanged" and still presented newly allocated, untouched control storage.

The failure now raises a specific error into the existing frame recovery path,
which rejects GPU presentation, invalidates scene metadata, schedules another
frame, and allows `paintGL` to use its established compatibility rendering.
Successful and unchanged backdrop paths are untouched.

The new injected-bind-failure regression failed before the fix because the frame
incorrectly returned `True`. It now verifies no presentation, invalidated cache
keys, scheduling, and a successful next-frame retry that clears and paints the
control target. A separate regression preserves unchanged-cache reuse. All 12
framebuffer regressions pass. This verifies recovery control flow with Qt raster
objects and inert FBO doubles, not a physical driver's failure behavior.
