# Startup activation boundary

## Scope

Based on `fa2c2b8397295d27ada5d105854b751b7dd0940d`. This stage separates the
startup workflow from `PolarRMWindow` service construction. It changes no draw
code, public window API, default setting, timer ownership or backend admission
implementation.

`startup_activation.py` depends only on frozen, named callback ports, grouped
into profile preparation, presentation and scenario admission. It cannot read a
window, registry, filesystem, environment or backend. The composition root maps
those capabilities explicitly; the workflow owns their ordering and the
conditional admission decision. This is a dependency boundary, not a reduction
claim based on constructor line count (the explicit adapter is longer).

The ordered contract is:

1. Sync wheel; apply match and robot baseline; refresh profile, wallpaper and
   sound options; create the default config; load the saved profile.
2. Project the loaded language/title and feedback preset, apply environment
   autostart, then sync the GUI mode.
3. Read launcher visibility. Only when hidden, read pending configuration.
   Only when neither owns startup, request the current scenario and capture the
   exact returned generation. Loading a profile can already request a generation;
   startup must not supersede it.
4. Announce readiness. The existing constructor then installs shortcuts,
   publishes the frame timer, and attempts timer admission with its existing
   retained failed-launch cleanup.

All reads whose values depend on earlier steps remain lazy. Exceptions stop the
workflow immediately without rollback or later effects; an already requested
scenario is not reported as captured if the capture callback fails. The workflow
is a one-construction operation, not a retry mechanism or a second lifecycle
owner.

## Evidence

Run with the hash-pinned Linux verification environment and
`QT_QPA_PLATFORM=offscreen`:

- `test_startup_activation.py`: 7 tests, including every workflow effect/query
  failure prefix, original exception identity, hidden/visible and pending gates,
  late profile/mode/autostart changes, and a subprocess proving imports do not
  load Qt, backend or window modules.
- `test_startup_activation_adapter.py`: 3 differential tests covering all effects
  and their injected failure prefixes across four visibility/profile/autostart
  cases. It executes the actual constructor adapter AST and compares arguments,
  effects and generation capture to the fixed source-referenced baseline block.
- Existing real-window construction: 6 tests; assembly: 12; async lifecycle: 21;
  delayed constructor cleanup: 1; constructor failure: 4; failed launch: 4;
  Qt launch abort: 7; API contract: 41. All pass (106 focused unittest tests in
  total including the two new suites).
- `tools/quality_gate.py`: passes with no initialization SCC. No baseline was
  refreshed.

Offscreen construction is not native OpenGL, audio-device or visual
qualification. Existing unsupported-GL/audio warnings remain environment
limitations; no native visual/performance claim is made.

## Remaining debt

- The constructor still constructs and publishes many independent window
  services and compatibility aliases. Their lifetimes are not newly unified.
- The UI profile loader still combines profile identity, asynchronous completion,
  registry projection and error presentation. Callback ports isolate startup
  ordering but do not make that loader pure or atomic.
- Earlier partial-construction failures retain their existing semantics. Only
  the already-complete timer-admission failure path has a retained abort
  contract; this stage does not extend cleanup to every constructor prefix.
- Key routing and window-wide presentation adapters remain separate future
  work. No rendering, economy, transport or key ownership changes are claimed.

## Independent review correction

Review of the first extraction (`0167a3c`) found eagerly captured bound methods:
a later method lookup could fail before earlier startup effects ran, and an
earlier callback replacing a later adapter would not be observed. The inlined
feedback expression also looked up the feedback service before reading the
EliminationFX module, reversing the original attribute-observation order.

All window method ports now defer lookup until invocation. A named local
feedback callback reads the registry first, then looks up feedback playback.
New characterization compares every attribute lookup and injected lookup
failure with the original block, and checks reentrant callback replacement.
The correction preserves the original behavior rather than weakening the
failure contract. The initial reviewer probe is retained as `lookup-red.log`.
