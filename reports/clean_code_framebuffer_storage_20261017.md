# Framebuffer storage ownership checkpoint

Scope: compositor framebuffer allocation, validation and retirement only. The
ordered storage specification now owns the twelve wrappers and their formats,
including immediately retaining each partially created prefix. The compositor
continues to own resize/DPR cache invalidation, render fault diagnostics and the
GL context. Existing writable FBO attributes forward to that storage owner.

## Evidence

- Baseline 1b2f580: eight characterization tests passed before production edits.
- Ten storage tests pass after refactoring: all twelve allocation-fault positions,
  all twelve invalid-handle and throwing-validation positions, short-circuit
  validation, resize replacement, public slot assignment, retirement order,
  physical-size/DPR reuse, minimum dimensions and unchanged scratch admission.
- Direct suites passed: compositor initialization ownership (9), compositor fault
  diagnostics (28), shader ownership (5), renderer ownership (8), compositor plan
  (18), render performance regressions (15), crosshair bounds (11).
- Total: 104 unittest tests, plus `tests/gpu_compositor_test.py` assertion script.
- Five shader-builder methods match baseline AST exactly. No shader, draw-order,
  texture format, attachment, pixel-size formula or visual default changed.
- `git diff --check` passed.

Commands use `/workspace/shared/client-clean-venv/bin/python` with
`QT_QPA_PLATFORM=offscreen CI=true PYTHONPATH=.`. Running the crosshair test without
PYTHONPATH initially failed importing rm_client; it passed with the repository's
root on the module path. Qt emitted non-fatal fontconfig cache warnings.

## Limits and next work

This is inert GL-handle and offscreen Qt evidence, not native-driver qualification.
Windows native GL framebuffer capture and interrupted context teardown remain
external gates; no claim of visual equivalence from desktop pixels is made.

Same-size reuse historically omits `blur_temp_fbo` and `treated_blur_temp_fbo` from
its completeness check. This unusual policy is explicitly characterized and
preserved; altering it requires a separate behavior-change decision and tests.
The compositor's glass command/cache responsibility remains sizeable and is a
reasonable next ownership boundary. Aggregate verification is integration-owned.
