# Compositor initialization ownership checkpoint

## Scope and reproduced defects

Baseline: `fa2c2b8397295d27ada5d105854b751b7dd0940d`.
Test-only red checkpoint: `8ec5f3a`.

The compositor already retained a program whose `removeAllShaders()` failed,
but its next `initialize()` replaced that owner without completing retirement.
A failed native-buffer destruction could likewise be followed by recreation.
Failed resize made the compositor unavailable while keeping existing programs;
reinitialization also replaced those programs without explicit retirement.

Initialization additionally combined binding, geometry upload and publication
without independent binding unwind. A partial bind or allocation exception
skipped unbind; a False unbind could still publish `available=True`.
The red checkpoint records eight assertion failures (including the two partial
bind subcases), with successful initialization retained as a passing control.

## Boundary change

- `initialize()` now admits a new lifetime only after existing program owners
  and any failed retirement have been released. Failed retry does not call
  builders or recreate native buffers.
- `_initialize_quad_storage()` owns only pass-local bindings and geometry
  upload. It attempts both unbinds before lifetime retirement, preserving the
  original operation error and both independent cleanup errors.
- `release()` records pending retirement even when the only failed owner is a
  buffer/VAO and all program slots are already empty.
- Public signatures, shader source, quad payload, frame order, cache commits,
  CPU fallback choices and GPU-first rendering remain unchanged.

Two existing shader-fault tests previously constructed already-owned programs,
set `available=False`, and expected builders to run immediately. Those fixtures
now explicitly clear initial program slots so they still exercise a *new*
shader lifetime and retain their original primary-cause/cleanup assertions.
They are not converted into passing expectations for retirement failures; the
new tests separately demand rejection and no builder calls for retained owners.
The original versions remain in the baseline and red checkpoint.

## Evidence and limits

See `reports/clean-code/compositor_initialization_green_20261016.json` for the
exact production file SHA-256, commands, exit codes and suite summaries, and
`reports/clean-code/compositor_initialization_red_20261016.txt` for the baseline
failures. Use the hash-pinned Linux verification requirements and
`QT_QPA_PLATFORM=offscreen` to reproduce. The new ownership suite contains nine
tests covering successful/inert repeated initialization, retirement retry,
failed initial setup/retry, failed resize, bind exceptions/False, upload faults,
multiple cleanup defects and cleanup rejection.

This is inert native-handle and offscreen raster evidence. It does not establish
native desktop GL output, driver-state correctness, DPI behavior or frame
cadence/performance. Shader factories still construct their local compile
candidate before publishing a compositor program slot; failed compilation
ownership is a separate remaining audit target. Raw unregistered window
callback first-cause behavior and broad frame/window ownership debt are outside
this change. No window source was edited.
