# Glass command buffer and per-command geometry ownership

Baseline: 666c0408dee5b2d4025faab580ffa31557dd78c0.
Characterization commit: b1441d5, eleven tests passed before production edits.

The compositor previously maintained four independent fields for glass command
membership and recording scope, repeated reset rules, filtered commands inside
native submission, and mixed logical-to-device clipping math with GL uniforms.
A single GlassCommandBuffer now owns static/frame collection lifetimes, owner
scope and membership selection. A separate per-command geometry value isolates
DPR/clipping evaluation from GL calls. The compositor still owns GL state,
painter cleanup, cache acceptance, material defaults and all shader code.

## Preserved read timing and state

- Selection still occurs after destination bind/clear. It snapshots membership,
  not command or QRectF objects. Commands added by clear are included; commands
  added after selection are deferred until the next submission.
- Geometry is evaluated immediately before each command's scissor call. A prior
  draw can mutate the next command's rectangle; it is not computed ahead for the
  whole frame. DPR retains its original once-per-pass sampling point. Material
  uniforms retain their existing reads after scissor submission.
- Existing collection inspection/list-mutation aliases and recording flag remain.
  Frame reset and transient invalidation preserve their respective clear orders.
- Rejected GL/painter cleanup does not consume buffered commands or publish cache
  metadata. The next frame reset rebuilds rejected static content as before.
- Existing out-of-viewport scissor arithmetic is explicitly preserved, including
  negative extent results. This stage does not change clipping policy.

## Evidence

Environment: shared hash-pinned verification Python with
`PYTHONPATH=. QT_QPA_PLATFORM=offscreen CI=true` from the checkout root.

- tests/test_glass_command_lifetime.py: 12 pass; includes actual Qt painter failure
  and independent FBO cleanup, owner restoration, no cache commit, retry cleanup,
  clipping thresholds and callback-induced live geometry changes.
- tests/test_compositor_fault_diagnostics.py: 28 pass.
- tests/test_overlay_layer_boundary.py: 12 pass.
- tests/test_framebuffer_generation.py: 27 pass.
- tests/test_cached_window_layers.py: 3 pass, frozen-source comparisons of eleven
  real window scenarios at two viewport/DPR combinations, warm and cold frames.
- tests/test_cached_frame_contract.py: 4 pass.
- tests/glass_submission_compare.py: 48 baseline/current complete GL call-trace
  pairs pass, each containing 100 deterministic commands. Every shader builder
  and every command normalization statement also match the baseline AST.
- Static quality gate passes unchanged baseline; git diff --check passes.

The first multi-script command guessed a nonexistent test_cached_frame_traces.py;
its real test_cached_frame_contract.py entry was subsequently run and passed.
Offscreen Qt emitted expected GL/audio/font-cache warnings. None of this is
native desktop pixel, GPU performance, driver or hardware qualification.

## Remaining boundaries

Command normalization remains in the compositor to preserve the current public
signature and enum types. No broad transaction/cache behavior has changed. Blur
pass submission and framebuffer paint-device selection remain separate future
candidates, only if characterized and justified as responsibility boundaries.
