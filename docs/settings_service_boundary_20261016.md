# Settings / keyboard service dependency boundary

## Scope and result

Baseline: `d3c03213ef213daa9ab3b64c4779c8454d9010bf`.
Implementation checkpoint: `2ef40aa36159237d24b2030463b1db9ec3cdb332`.
Initial regression checkpoint: `9cc587ada2543e047357fbffde1f03343b3a75f0`.
Final tested code/test checkpoint: `febac3a09fee0ec3ef8b99f466f73fb8685e1599`
(the following report-only commit does not change the tested source).

The previous dependency was a real deferred-import cycle:

- `cs2_settings_center.BindingControl.capture()` opens `jello_keybinds`.
- `BindingSession._validate()` and `.apply()` imported validation/application
  from `cs2_settings_center`.
- `build_keyboard_manager()` imported its F12 installer from that same page.

The settings page still opens the keyboard UI. The reverse service dependency
is removed, including all three function-local imports:

- `ui/settings_application.py` owns the shared `PAGES` capability allowlist,
  live-registry `settings_specs()` / `preference_capabilities()`, and the
  existing `validate_draft()` / `apply_registry_draft()` entry points.
- `ui/emergency_shortcut.py` owns the dialog-lifetime Qt event-filter adapter.
  It delegates emergency sequencing to the existing `emergency_actions` service.
- Both dialogs import those shared adapters; neither adapter imports either
  dialog. The keyboard UI can build, validate, commit and handle F12 with the
  settings page explicitly unavailable to the Python import system.

This is a dependency-direction repair, not complete host decoupling.
Validation can run without Qt or either page. Application still requires the
host's registry, control bridge, backend lock, runtime snapshot/restoration,
configuration-loading flag, failure recorder, cache invalidation, frame scheduler
and save hooks. Audio application remains a prepared transaction participant.
The F12 adapter explicitly depends on Qt and the host's backend/control/toast.
No surrogate window facade or second transaction implementation was introduced.

## Preserved behavior and compatibility limits

`PAGES`, `settings_specs`, `preference_capabilities`, `validate_draft`,
`apply_registry_draft` and `install_estop_shortcut` remain importable from
`rm_client.ui.cs2_settings_center`, with the same call signatures. They are
aliases of the shared objects. In-place edits to the existing `PAGES` dictionary
are therefore still observed by both paths. The ordered page entries, allowed
setting names and toggle rights match the pre-extraction source exactly.

The five moved functions and `PAGES` have identical ASTs to the baseline. Two
semicolon-separated lines in the new Qt adapter were expanded to satisfy the
unchanged static-quality baseline; no transaction sequence, audio participant sequence, validation branch, rollback
operation, auto-save order or F12 event/filter cleanup branch was rewritten.
Original window API/behavior tests and frozen baselines were not changed.

An imported function now has its implementation module as `__module__` and its
globals come from that module. Rebinding names such as
`cs2_settings_center.validate_draft`, `prepare_preference_draft` or
`request_emergency_stop` is not a cross-module monkeypatch hook. Repository
search found no caller relying on those rebindings. Ordinary calls/imports,
object identity of compatibility aliases, signatures and shared `PAGES`
mutation are tested; arbitrary module-global rebinding is not claimed as a
preserved extension API.

Settings-page keyboard acceptance still changes only that page's draft widgets.
Standalone keyboard acceptance uses the real preference transaction. A failed
apply retains the settings draft and reports the existing error prefix, restores
values/bindings, leaves control stopped and does not auto-save. Both actual
window-backed dialogs retain F12 in nested modal dialogs, suppress auto-repeat,
report independent cleanup failures and remove their filter on completion.
When keyboard and settings dialogs coexist, each physical F12 is dispatched
once; after the child finishes, the parent remains responsive to F12.

## Verification

Environment: Linux x86_64, Python 3.12.14, PySide6 6.8.2. All commands used the
existing project virtual environment, with:

```sh
export CI=true PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen
export POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1
```

Before and immediately after extraction, the same six suites passed all 68
methods: settings center, Jello keyboard, configuration transactions, emergency
failures, binding restore quality and asynchronous configuration.

The expanded focused run passes 110 methods, with no skips:

```sh
python -m unittest \
  test_settings_service_boundaries test_settings_dialog_service_wiring \
  test_cs2_settings_center test_jello_settings_keyboard \
  test_configuration_transactions test_emergency_action_failures \
  test_binding_restore_quality test_async_configuration \
  test_audio_configuration_transactions test_lazy_settings_state -q
```

The two new suites contribute 12 methods. They are discovered automatically by
`tools.test_registry.discover()`; no aggregate-runner or registry bypass was used.
Three negative controls separately restored each old deferred page import.
For each mutation, both the fresh-process keyboard test and all-scope AST
boundary test failed as intended. The runtime error specifically identified the
forbidden settings-page import; the production file was restored exactly after
the controls.

The default API compatibility check also passed against a fresh temporary user
data directory, covering 1,114 established members, 540 Python signatures and
78 explicit private caller seams:

```sh
POLAR_USER_DATA_ROOT="$(mktemp -d)" python tests/api_freeze_test.py
```

The six-symbol AST comparison can be reproduced without importing either page:

```python
import ast
from pathlib import Path
import subprocess


def symbols(tree):
    result = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            result[node.name] = node
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            result[node.targets[0].id] = node
    return result


before = symbols(ast.parse(subprocess.check_output([
    "git", "show", "d3c0321:rm_client/ui/cs2_settings_center.py"
], text=True)))
for filename, names in (
    ("settings_application.py", ("PAGES", "settings_specs", "preference_capabilities",
                                 "validate_draft", "apply_registry_draft")),
    ("emergency_shortcut.py", ("install_estop_shortcut",)),
):
    after = symbols(ast.parse((Path("rm_client/ui") / filename).read_text()))
    for name in names:
        assert ast.dump(before[name]) == ast.dump(after[name]), name
```

`git diff --check` and `python tools/quality_gate.py` passed without changing
lint/cycle budgets. The static report has 190 modules, 3,211 functions, zero
initialization cycles and two retained deferred-import cycles (model/referee and
app-entry/window); the settings/keyboard cycle is absent. Integration-wide
aggregate verification is separate and was not run from this worktree. These
are focused offscreen, synthetic-failure and import-boundary results. They are not native
OpenGL, audio-device, physical controller or real-match qualification. Existing
model/referee coupling, window input routing and host configuration dependencies
remain outside this change.
