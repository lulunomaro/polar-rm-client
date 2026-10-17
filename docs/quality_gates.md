# Reviewable quality gates

These checks support the Clean Code refactor. They do not define “clean” by a
20-line function limit, a giant formatting diff, or a new passing snapshot.

## Commands

Use the same Python environment as the client. The static gate and its focused
self-tests require only the standard library; they do not import the client.

```sh
python tools/quality_gate.py
python tools/quality_gate.py --json tmp_quality/architecture.json --markdown tmp_quality/architecture.md
python tests/test_quality_gates.py
python tools/verify_all.py --test test_quality_gates --test test_test_registry --json tmp_quality/quality-tests.json
```

`verify_all.py` discovers the new suite automatically. It must record a nonzero
unittest count, and dependency skips remain SKIP/INCOMPLETE. Unittest's
`expectedFailure` also remains INCOMPLETE with its own reported count: a known
failing assertion is not a passed test even though unittest exits zero. Use an
explicit negative assertion (such as `assertRaises`) to verify intended rejection
behavior. A successful static
check is not an application, GPU, firmware, robot, or visual qualification.
The new suite also runs the real repository gate, so the existing aggregate
verification command cannot silently omit architecture checks.

## Reproducing the Linux verification dependencies

`requirements-verification-linux-cp312.txt` records the exact 2026-10-16 Linux
x86_64 CPython 3.12.14 environment separately from the supported runtime ranges.
It pins all ten runtime/transitive wheels, including `openvino-telemetry`, and
requires their official PyPI SHA-256 hashes. Wheel names, metadata-source URLs,
download URLs and platform scope are recorded in
`tools/quality/verification_environment.json`. No installer runs during tests.

On a matching Linux x86_64 host with glibc 2.28 or later and Python 3.12.14:

```sh
python3.12 -m venv .venv-verification
CI=true .venv-verification/bin/python -m pip install -r requirements-verification-linux-cp312.txt
.venv-verification/bin/python -m pip check
CI=true .venv-verification/bin/python tests/test_verification_dependency_lock.py
CI=true .venv-verification/bin/python tools/verify_all.py --category offline --json tmp_quality/offline.json --log-dir tmp_quality/logs
```

Check `python3.12 --version` first; the command name alone does not guarantee the
recorded patch version. Unavailable versions, unsupported wheel tags or hash
mismatches must stop setup rather than silently selecting newer packages.
The lock is intentionally Linux/CPython-3.12-specific; it is not a Windows
packaging lock or a claim that another platform cannot run the application.
The ordinary runtime/build requirements remain unchanged.

Dependency hashes do not pin the OS, graphics driver, fonts, compiler, FFmpeg,
media/model assets or external firmware. Offline tests that need those inputs
must still report their actual missing prerequisites. `--category offline`
selects local checks; the default `--profile offline` additionally includes
firmware-category checks and cannot fully pass without the required prepared
reference. Native desktop and field qualification remain separate gates.

For disconnected reproduction, prepare a wheel directory on a matching connected
host with `python -m pip download -r requirements-verification-linux-cp312.txt`;
then install with `--no-index --find-links <wheel-directory>` and the same lock.
The 2026-10-16 restoration checked all ten downloaded wheel hashes, installed
that wheel directory into a second empty virtual environment with `--no-index`,
and passed `pip check`, exact-version checks, native imports and an offscreen Qt
`QImage` construction. That validates this dependency-setup path, not the full
application suite or native desktop rendering.

Keep the lock and wheel directory together. Package installation is an explicit
setup action, never a side effect of `verify_all.py` or a unit test.

## Verification report provenance

Execution reports from `tools/verify_all.py --json <path>` now include an
additive `provenance` object with its own `schema_version: 1`. Existing top-level
schema, results, counts, selection and PASS/SKIP/INCOMPLETE/FAIL meanings are
unchanged. Inventory-only `--list` does not run Git or collect this metadata.

The report records the UTC capture time, Python implementation/version, OS
system/release/machine, and the installed distribution versions of the declared
runtime/build dependencies plus PySide6's key companion packages. Versions come
from `importlib.metadata`, without importing Qt, OpenCV, OpenVINO or other
optional runtime packages. Missing packages and unreadable metadata have
separate explicit statuses; collecting a version does not verify native imports,
GPU support, hardware access, or a reproducible dependency lock.

`source_at_start` records Git HEAD, the HEAD tree ID, and a dirty flag covering
tracked changes and nonignored untracked files. The tree ID describes the
committed tree, not a dirty working copy; ignored resources are outside the
status check. Preserve the actual patch/resource manifest when that distinction
matters. Git is queried read-only with optional index locking disabled; a
missing executable, source snapshot without Git, invalid object ID or failed
query is `unavailable`, never silently reported as clean. No checkout, fetch,
download, baseline rewrite, dependency installation or environment-variable
dump is performed. Executable paths, remote URLs, filenames from dirty status,
and Git error text are not included in this metadata.

### Report attempt identity and interrupted runs

Each execution report has a unique `run_id`, UTC start time and `run_state`.
Once argument parsing, timeout validation and registry selection succeed, and
before firmware validation or subprocess execution, the requested JSON
destination receives an explicit nonpassing INCOMPLETE checkpoint. A lock
conflict or invalid firmware reference records a blocked attempt; an unexpected
interruption after that checkpoint leaves the attempt visibly unfinished. A
completed run replaces the checkpoint with its actual selected-scope results.

Malformed arguments, invalid selectors and discovery failures occur before an
execution attempt is established; they return an error without overwriting an
existing report. Always match the invocation's exit status and new run ID before
accepting a JSON file. An old PASS file's existence alone is never evidence that
the latest command completed successfully.

Reports are published by writing a complete sibling temporary file and atomically
replacing the destination. A write/replace error still fails the command; it must
not be treated as a successfully delivered new report. Use a distinct destination
per concurrent/requested attempt and retain the matching exit status and run ID.
An earlier verifier can still finish its own run after another invocation is
blocked, so reusing one filename across overlapping invocations is ambiguous.
Inventory-only `--list` retains its separate nonexecution format.

### Start/end source stability

Execution reports also contain an additive `source_stability` object. The
runner hashes relative names and exact bytes of Python/native/build source and
JSON/configuration contracts beneath `rm_client`, `tests`, `tools` and `configs`,
plus `main.py` and root dependency declarations. Its complete suffix list and
exclusions are embedded in the report. Captures are independent of Git history
and timestamps, so a supplied source snapshot can still be checked.

A persistent content change, addition, deletion or rename during verification
adds a FAIL evidence result even if every selected child reported PASS. If a
source capture is unavailable, that evidence result is INCOMPLETE. Unchanged
source fingerprints do not add a fictitious executed test to the suite counts.
`test_verification_sources` proves the distinction using real inert subprocesses
that modify their own code or add a test after inventory selection.

This is a start/end comparison, not a filesystem freeze: it cannot detect an
edit restored between captures. It excludes binary/media assets, external
fixtures and installed packages; preserve those separate manifests and the
actual source revision when reproducing a full result. Writes to reports and
logs outside the declared source/contract scope do not invalidate evidence.
The existing Git identity remains the start-of-run committed identity and dirty
flag; the new digest describes the declared working-file scope, not that Git tree.

## What is enforced

- Every discovered production Python file must parse. An empty source tree,
  unreadable/malformed baseline, or invalid syntax cannot yield PASS.
- The backend, model, configuration, lifecycle, runtime-path, and state-overlay
  layers cannot import `rm_client.ui`, even via a function-local or type-only
  import. The model boundary includes its extracted `module_catalog` and
  `module_settings` domains; emergency-action coordination and qualification
  observation predicates, pure referee profiles and economy value types are
  also protected. UI depends on those layers, not
  the reverse. The two pure roots `referee_profiles` and `economy_values` also
  reject backend, Qt-binding and Tk imports, including deferred, type-only and
  unresolved declarations. This narrow rule protects their reviewed value-only
  boundary; it is not a general ban on backend imports in every domain adapter.
- No new module-initialization import cycles. An additional full-import-graph
  guard also rejects cycles containing function-local imports; those are still
  reported separately and never falsely called initialization cycles.
  Type-checking-only edges remain excluded from both runtime graphs.
- No increase in scoped existing debt for bare `except`, wildcard imports,
  shadowed definitions, inline compound suites, or semicolon-separated code.
  Strings and comments containing semicolons are not violations. Property
  setters/deleters and typing overloads are recognized.
- Nine existing orchestration hotspots have explicit no-growth budgets for
  executable statements, decisions, and control nesting. Reducing a hotspot
  through a tested responsibility boundary is allowed. Physical line count is
  never a failure condition.

The initial baseline is tied to the published commit
`d64f1528e2e1864ef369537c026b6d3f7127f590`, Git tree
`46333e99db86988f503b4ff31cd651d0e07eed86`. Its source-equivalent local staging
commit was `da91fc54b97caffd8f76b1aba86f88105d3c0379`; that local object is not
required to run the checks or expected to exist in a fresh clone. Connector
publication can change commit identity while preserving the complete Git tree.
It records existing debt, not permission to add more. Its two wildcard-import
locations are historical compatibility facades; rewriting their export behavior
is a separate compatibility task. Other recorded debt is intentionally left in
place to avoid whole-project beautification.

A check never rewrites `tools/quality/baseline.json`. Do not increase budgets or
regenerate it just to turn a failure green. First inspect the failing change. If
an exception is justified, review the exact symbol/rule, the behavior change,
the focused negative test, and the reason alongside that small baseline diff.
When cleanup reduces debt, lower the relevant budget after tests; do not replace
unrelated entries. Removing a function retires that function's budget, so review
its replacements and the full report rather than claiming a deleted metric
proves the design improved.

## Reproducing the initial source measurements offline

Ordinary quality/API checks use committed evidence files and the checked-out
source. They do not run Git, require old history, download a baseline, or rewrite
one. Reconstructing the initial architecture report additionally needs either
the published commit already present locally or a separately supplied snapshot
whose measured source digest matches. A shallow clone may lack the old commit;
stop with that explicit missing-history result rather than substituting `HEAD`.

From the refactored checkout, this POSIX command verifies the complete original
Git tree before exporting only the measured `rm_client` source into a new
temporary directory. It does not check out or mutate the current branch:

```sh
(
  set -eu
  revision=d64f1528e2e1864ef369537c026b6d3f7127f590
  git cat-file -e "$revision^{commit}" || {
    echo "Baseline commit is absent locally; obtain the published history or a verified source snapshot separately. No automatic fetch." >&2
    exit 2
  }
  test "$(git rev-parse "$revision^{tree}")" = 46333e99db86988f503b4ff31cd651d0e07eed86
  baseline=$(mktemp -d)
  git archive --format=tar --output="$baseline/source.tar" "$revision" rm_client
  tar -xf "$baseline/source.tar" -C "$baseline"
  python tools/quality_gate.py --root "$baseline" \
    --expect-source-sha256 cd432aed2f9188765812905cab9e76a61aac6c36980d6f8bec3100d91138aca5 \
    --json tmp_quality/architecture-before-reproduced.json \
    --markdown tmp_quality/architecture-before-reproduced.md
)
```

The 64-character measured digest is the report's sorted `rm_client` path/content
digest, not the 40-character Git tree identifier. It binds the measured source text
independently of commit ancestry and also works with a plain source directory
that has no `.git`. The measurement reads UTF-8 with BOM removal and normalized
newlines; the Git tree check additionally fixes the original byte-level tree.
A mismatch fails even when the ordinary debt checks pass.
Keep the original `reports/clean_code/architecture_before.md` as historical
evidence; reproduction outputs belong in a separate path. A later downward
ratchet may correctly reject the old source's larger hotspot counts; the content
digest and reproduced measurements remain meaningful independently of that
current-budget result.

## How to interpret the measurements

The full JSON preserves per-function direct `self` names, state reads/writes,
call targets, parameters, source span, executable statements, decision points,
and control depth. Markdown shows the top executable-statement hotspots and
module fan-in/fan-out. Compare full reports at known revisions when reviewing
extractions.

- Multiline shader strings, documentation, and declarative data can occupy many
  physical lines without adding many executable statements. Neither a small
  AST count nor a large data literal proves that a function is easy to change.
- Nested function bodies are measured independently, not charged twice to the
  outer function. An `elif` chain adds decisions, not artificial nesting.
- Decision counts include `and`/`or`, comprehensions, and exception branches.
  They are explicitly defined review signals, not a claim of exact McCabe
  complexity or a measure of how many responsibilities a person would identify.
- Static imports, direct `self` attributes and literal `state.field` paths can
  be measured. Dynamic imports, reflective dispatch, aliases, indirect field
  mutations and effects inside collaborators require code review and behavior
  tests. Deferred-cycle components remain visible as coupling debt.

## Test design and source guidance

The checks follow the supplied book's emphasis on readable, independent,
repeatable tests, one concept per test, consistent team formatting, and placing
responsibility with the object that owns it:

- [Chapter 9: Unit Tests](https://aeryzhao.github.io/cleancode-book/ch9.html)
- [Chapter 17: Smells and Heuristics](https://aeryzhao.github.io/cleancode-book/ch17.html)
- [Chapter 5: Formatting](https://aeryzhao.github.io/cleancode-book/ch5.html)

Use build/operate/check fixtures and observable behavior for new service tests.
Keep hardware-safe defaults and test error, interruption and retry paths. A
private attribute may be a deliberately retained legacy test seam, but a broad
snapshot of every newly added private field is not a substitute for a public
compatibility contract or a behavioral assertion.

## Window compatibility contract and legacy audit

```sh
python tests/api_freeze_test.py
python tests/api_freeze_test.py --mutation-selftest
python tests/api_freeze_test.py --legacy-snapshot
python tools/verify_all.py --test api_freeze_test --test test_api_freeze_platform_regressions --test test_window_api_contract --json tmp_quality/api-contract-tests.json
```

Default compatibility mode protects the 1,036 public entries in the existing
snapshot plus 78 explicitly retained private caller seams. The manifest
`tests/window_api_contract.json` records up to three independently observed
caller locations for every private seam, and 540 actual Python call signatures.
It preserves names, parameter order/kinds/defaults, callable kinds, stable
attribute types/default values, the narrow Qt-version/path portability rules,
and the module-level exports. Type annotations and a method's implementing
Mixin/class name do not change which calls it accepts, so they are not compared
as calling conventions. Added public methods do not break existing callers.

Private storage without recorded external callers and new implementation fields
are not implicitly made permanent APIs. Existing behavioral tests remain in
place. A contract of 1,114 members is evidence about that named surface, not a
claim that every possible external caller or runtime behavior was verified.
For example, static caller discovery does not resolve dynamically constructed
attribute names; inspect relevant consumers when moving a private seam.

On 2026-10-16, two storage aliases (`_field_frame_times_ms` and
`_last_field_frame_log_at`) retired from the mandatory private-seam set after
migration to `FrameTelemetry`. Their former callers in GPU warmup/UI-state
harnesses now use that owner. Remaining uses characterize the owner or the alias
itself, so they are not independent caller evidence. The manifest's
`retired_private_members` retains the original locations, implementation stage,
replacement fields, rationale and behavior-test paths. Those records cannot
exempt a public member or a still-active private seam. The runtime aliases stay
in place: `test_window_construction.py` checks assignment and reading in both
directions on an actual window, while `test_frame_telemetry_contract.py` checks
the owner's reporting behavior. Removing either alias from a synthetic snapshot
still fails the historical audit.

`_last_present_time` remains protected because `overlay_live_capture.py` and
`refactor_golden.py` reset it; its pre-existing volatile classification protects
presence without asserting its changing value/type. `_ui_tick_stages` remains
protected, with normal-frame and tick-behavior test consumers and stable-type
negative controls. The caller-evidence check itself is unchanged.

The original 1,161-entry `tests/api_freeze_baseline.json` is unchanged. Its digest
is pinned by the compatibility manifest, so refreshing the historic snapshot
cannot silently approve changed defaults. `--legacy-snapshot` still checks the
full original `dir(window)` surface and method qualnames, reports real drift,
and returns failure when it differs. It is deliberately an opt-in historical
audit after compatible implementation extractions, not a synthetic passing
result. Any intentional drift should be explained with the affected name and
its behavior tests.

`--update` is restricted to `--legacy-snapshot` with a separate `--baseline`
destination for review. It never updates the compatibility manifest or overwrites
the committed historical evidence by default. Do not copy a fresh dump over the
baseline to mask a failing contract.

Negative tests prove that public callable removal, changed arguments/defaults,
missing signature evidence, protected private removal/type drift, module export
loss and attempted volatility exemptions are caught. They also prove that a
provider-only move or added private collaborator can pass the compatibility
contract while the legacy full audit still reports the difference. The real
window mutation command deletes `draw_watermark`, checks that removal fails,
restores it, and checks that the original contract passes again.

### Asynchronous workflow behavior is a separate contract

Preserved names and call signatures do not prove preserved completion timing.
The asynchronous lifecycle intentionally makes the first `window.close()` return
before retained native owners finish; that close is ignored until completion is
confirmed and the event loop accepts a later close. Likewise, returning from
`apply_config_data()` is not proof of committed runtime configuration. Callers
must respect `configuration_change_pending` and the owner-thread completion
path. A passing API snapshot is not synchronous-behavior compatibility evidence
for these operations.

Keep these focused behavioral suites in the verification inventory:

- `test_backend_async_lifecycle`: public request/poll/shutdown/retry operations,
  latest-request ownership, locked output, failure visibility and stopped
  recovery. Private worker storage is not added to the window snapshot.
- `test_window_async_lifecycle`: a real offscreen window's first rejected close,
  responsive event loop, owner-thread final save, no normal runtime work while
  closing, and scenario completion routing.
- `test_window_normal_frame`: settled normal ticks execute the production
  presentation, control and backend stages. Their counters must increase on
  each normal frame, pause during a controlled pending transition, and resume
  after real owner-thread completion; launcher/HUD/ClickGUI paints enter
  the actual `paintGL()` compatibility path. Only its final `QPainter(window)`
  target is redirected to a real `QImage`; nested paint devices remain intact.
  This checks real CPU drawing and nonempty output, not native GL presentation
  or visual pixel equivalence. Both uncaught frame errors and recorded
  `_run_tick_stage` errors fail the smoke. Python network audit events are
  rejected; native Qt audio probes remain outside that audit's scope.
- `test_configuration_transactions` and `test_async_configuration`: accepted
  settings/profile changes, pending-versus-committed status, exact
  values/options/binds rollback, failure reporting, and no implicit re-arm.

The API harness uses `settle_window_lifecycle` before stable sampling and
`close_window_safely` in every teardown path. These helpers pump the real window
lifecycle and Qt events; they do not substitute synchronous backend shutdown.
Pending timeout or unconfirmed native cleanup must fail the test, including
snapshot-update mode. Do not delete the window immediately after the first
close or suppress a cleanup failure to make the contract check green.

The general `smoke_test` window fixtures and `student_frontend_test` also wait
for startup profile settlement before exercising normal interactions, then
confirm asynchronous close completion. Directly editing a registry value while
an initial profile transaction is pending can be reverted by that transaction;
it is not a valid setup for a language-toggle or hotbar-edit assertion. Their
existing interaction assertions remain intact. Tests specifically about input
during a pending transition must instead assert that transition's explicit
contract, without pre-settling it.

Run the short normal-frame smoke directly with
`python tests/test_window_normal_frame.py`, or select it through
`python tools/verify_all.py --test test_window_normal_frame`. It must report three
executed unittest methods, covering multiple frame/surface cases, rather than a
zero-test success. Its standalone entry sets the same optional-import telemetry
and capture/media guards as the runner.

## Historical implementation evidence

`tools/module_backend_map.py` keeps the retained Tk prototype in a separate
historical-evidence appendix. It excludes that implementation from current Qt
module-name coverage only when `rm_client.app.main` is a direct forwarder to the
canonical `rm_client.ui.polar_app.main` and there is no other statically visible
production consumer of the historical client. A filename alone is insufficient.
The post-extraction facade must reference the moved implementation only through
its explicit lazy compatibility export. Unverified sources stay in the search.

The AST report keeps historical functions in its full statement/decision totals
and marks their source role. Moving the same class from `app.py` to
`legacy_tk_app.py` does not count as reducing its complexity or wiring a setting.
The settings-orphan audit remains a separate command with its existing scope.
No classification changes its baseline automatically. The module map retains
its manually reviewed section and now describes missing grep evidence honestly:
absence of a nearby name does not prove there is no generic/dynamic consumer.

## Per-suite environment isolation

Every owned verifier child receives a fresh application-data directory and XDG
cache/config/data roots beneath it. `POLAR_APP_ROOT` is removed because it forces
portable mode in the real `RuntimePaths` resolver and overrides the isolated
application-data directory. Explicit portable-path tests supply their own local
mapping; the source runner must not inherit another installed application's root.

The runner additionally forces `POLAR_AUTOSTART=0` and removes the standalone
startup suite's `POLAR_STARTUP_USER_DATA_ROOT` debugging override. An inherited
shell setting must not activate production transports or bypass the new data
root. A test of explicit activation or a fixed-path edge case must arrange it
inside its own controlled fixture. Release-selected FFmpeg and explicit media
fixtures remain available; this is not a blanket environment reset.

Without `--firmware`, the child also removes an inherited
`POLAR_REFEREE_REFERENCE`; with that option, both firmware selectors point to the
same explicitly selected checkout. Otherwise a shell's old reference could
silently satisfy tests outside the report's firmware identity check.

`test_test_registry` covers both original counterexamples: the production
`RuntimePaths` resolver must stay in source mode with the requested owned data
root, and consecutive real subprocesses must have separate writable cache and
configuration directories. The invoking environment is unchanged. These are
fixture-isolation checks, not an OS security sandbox or a network prohibition.

## Optional dependency telemetry in local verification

Every `verify_all.py` child gets `CI=true` before importing test/application
code, in addition to `POLAR_DISABLE_LOCAL_CAPTURE=1` and
`POLAR_DISABLE_MEDIA_PRELOAD=1`. This applies to offline, native-desktop and
release profiles and does not mutate the invoking process's environment or
start any hosted CI job.

The installed OpenVINO 2026.4.1 wrapper initializes its optional telemetry with
`disable_in_ci=True`. In openvino-telemetry 2025.2.0,
`OptInChecker._run_in_ci()` recognizes `CI=true` and `check()` returns declined
before reading a consent file. This behavior was verified from the installed
official package's source by executing only those isolated AST methods with
file access prohibited. No OpenVINO import, consent-file modification,
telemetry initialization, or network request was needed for that check.

The release freezer applies the same opt-out separately: `build_release.ps1`
sets `CI=true` for its PyInstaller invocation and restores the prior process
value (including absence) in `finally`. `POLAR-RM.spec` also sets it before hook
imports, so direct spec invocation is covered. The spec runs in the freezer;
this does not inject a telemetry preference into the packaged application.
`tests/test_build_telemetry_environment.py` checks the PowerShell source contract
and executes the real spec with inert PyInstaller hooks in isolated Python
children. No PyInstaller installation, real freeze, Windows packaging, or actual
SDK import was performed for that check. A freezing-time external request was a
review risk, not an observed reproduction in this environment.

For standalone Qt/inference tests outside the runner, set the same environment
before imports. On a POSIX shell:

```sh
CI=true POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1 python tests/api_freeze_test.py
```

This is an opt-out for the observed official dependency path, not a claim of a
universal network sandbox. Keep denied external requests denied, inspect other
optional dependencies before importing them, and continue to distinguish local
fixtures from hardware/network qualification. Environment-contract regressions
exercise both all profile combinations and a real inert child process; they
never import the telemetry package.

## Second-pass responsibility ratchet (2026-10-16)

The fixed integrated source `c4d3bcf97ef43056bf316bbec593802652120878`, Git tree
`7133ab90ca589f2b9afaf6e141186b42f5b6d6c1`, measures source SHA-256
`2112b3f1c0c583adaf7f5e26daf90061c619697bd398b84b4d0b64d30999b191`.
Three existing budgets are lowered to those measured values. No baseline source
identity, lint allowance, other function limit, public API snapshot, behavioral
trace or pixel fixture is refreshed by this change.

| Symbol | Prior allowance (statements / decisions / depth) | Actual starting `71ebb0f` | New measured cap |
| --- | --- | --- | --- |
| `EconomyMixin.build_gpu_economy_workspace` | 218 / 80 / 3 | 198 / 77 / 3 | 131 / 45 / 3 |
| `EconomyMixin.draw_economy_overlay_compat` | 199 / 61 / 3 | 179 / 58 / 3 | 123 / 33 / 3 |
| `PolarRMWindow._paint_gpu_frame_cached` | 167 / 69 / 5 | 167 / 69 / 5 | 97 / 46 / 3 |

The older allowances predate geometry work already present at `71ebb0f`; that
prior reduction is not credited to this pass. Accepted named-port/domain tests
and preserved behavior justify these boundaries, rather than wrapper size alone.
Responsibility moved into new collaborators remains visible: all functions in
`rm_client.ui.economy_content` total 131 statements / 53 decisions, and
`rm_client.ui.polar.overlay_layers` total 187 statements / 89 decisions.

Whole-tree totals **increase** from `71ebb0f` to the fixed integrated source:
190 to 204 modules, 3211 to 3342 functions, 36668 to 36957 executable statements,
and 12901 to 12999 decisions. These include retained historical UI source.
This is a bounded reduction of selected orchestration responsibilities, not a
claim that total project complexity decreased or all large adapters disappeared.

`tests/test_quality_ratchet_baseline.py` measures the real repository and then
independently sets every reviewed symbol/metric to its limit plus one. Each must
produce exactly its matching responsibility-growth failure. The original
behavior, rendering and API suites remain separate acceptance evidence.

### Full-import cycle guard

At the same fixed integrated source, both initialization and deferred-inclusive
import graphs have zero cycles. Baseline schema 2 adds a required, explicit
`all_import_cycles: []` allowance and fails closed if it is absent or malformed.
The original baseline source identity and initialization-cycle allowance remain
unchanged. This is a new enforcement boundary; deferred cycles had previously
been reported without a failing guard.

The quality suite mutates an acyclic fixture with a real function-local import
to create a cycle while keeping initialization cycles empty. The CLI must fail
for the full graph. Replacing that edge with a `TYPE_CHECKING` import passes,
preserving the existing non-runtime classification. Dynamic imports remain
outside this static graph's evidence.
