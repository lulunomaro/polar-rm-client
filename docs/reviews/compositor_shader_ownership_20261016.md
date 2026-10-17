# Compositor shader candidate ownership checkpoint

Parent checkpoint: `a3fddee9d8884dd6e73f71fbee40de848adc377f`.
Test-only red checkpoint: `fff2b95`.

The previous initialization audit identified a remaining failed-compile ownership
boundary. This stage reproduces and closes that specific gap; it does not change
shader text, draw passes, binding state, caches, geometry, public signatures or
GPU-first behavior.

## Reproduction

Each of the four classmethod program factories calls static `_compile`, which
constructs a native program before compiling either shader and linking. An
exception (or False converted to the existing RuntimeError) occurred before the
returned program could be assigned to its compositor slot. `initialize()` then
retired earlier programs but never explicitly retired that local candidate.

The red test checkpoint has 29 failing subcase assertions: four destination
slots, three compile/link stages, both exception and False outcomes, plus
candidate cleanup failure/retry and shader-log failure. Allocation rejection
before a candidate exists is a passing control and retires earlier programs.

## Ownership and error contract

`_GlProgramBuildFailure` carries the failed native candidate and original error
across the existing class/static factory boundary. `_install_program` adopts
that candidate into its intended compositor slot before rethrowing. The normal
initialization failure path then attempts each independent retirement exactly
once. Failed native cleanup retains the same candidate in that slot; subsequent
initialization cannot replace it until explicit retirement succeeds.

The structured exception extends the existing `_GlCleanupFailure` protocol, so
public `initialize()` still returns False and its diagnostic/log path receives
the original exact exception, traceback and cause, rather than logging the
carrier as the root. A regression uses an exception that rejects attribute
mutation and checks logger exception identity and the original cause. No global
candidate pool, class mutable state or exception-attribute mutation is used.

A deliberate private-call error-contract change remains: direct `_compile` or
`_build_*` callers now receive a structured RuntimeError subclass on a failure
after allocation, with the original exception as its cause and the candidate
retained by the carrier. Their signatures and successful return objects are
unchanged. Repository production callers use these factories through the
initializer; callers bypassing that owner would need to handle failed candidate
retirement themselves. An allocation failure before an object is returned
remains the original exception. The frozen application API check passes.

## Verification and remaining limits

`reports/clean-code/compositor_shader_green_20261016.json` records exact
production SHA-256, changed/unchanged existing method AST counts, commands, exit
codes and summaries. Red output is adjacent as
`compositor_shader_red_20261016.txt`. Use the hash-pinned verification environment
and `QT_QPA_PLATFORM=offscreen` to reproduce.

The shader suite has five test methods with matrices over all four destinations,
vertex/fragment/link failure, False return, native allocation rejection, failed
log retrieval, cleanup rejection plus later repair, and unannotatable original
exception identity/cause. Existing compositor, cache, command collection,
specialized renderer, regression, API and static checks are also run.

Evidence uses inert native handles and offscreen raster. It cannot qualify
native GL driver behavior, actual shader compilation, device context teardown,
DPI or measured performance. The previously documented raw unregistered window
callback first-cause gap and broad frame/window responsibilities remain outside
this stage. No window source was changed.
