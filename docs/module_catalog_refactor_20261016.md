# Module settings and catalog boundary

## Why this boundary

The original `model.py` combined robot runtime state, mutable settings and
registry notifications, a 1,178-line declarative catalog, serial/profile
discovery, extension modules, and derived function-bar choices. Most of the
catalog's length is legitimate data. It remains one declaration instead of
being split into arbitrary small files.

The separation follows the requested Clean Code chapters on
[functions](https://aeryzhao.github.io/cleancode-book/ch3.html),
[data boundaries](https://aeryzhao.github.io/cleancode-book/ch6.html), and
[class responsibilities](https://aeryzhao.github.io/cleancode-book/ch10.html):

- `module_settings.py` owns `Setting`, `Module`, `ModuleRegistry`, the existing
  setting helpers, and stable setting-option identifiers. It imports neither
  Qt nor runtime state at execution time. The only state reference is a
  type-checking-only annotation for the existing module tick interface.
- `module_catalog.py` receives explicit `CatalogChoices` snapshots, declares
  default module prototypes, copies prototypes into registry-owned values,
  appends caller-supplied extensions, and derives the complete function-bar
  choices. It does not discover ports/protocols, import a UI, or start workers.
- `model.build_default_registry()` remains the compatibility/environment
  adapter. It captures candidates, obtains the existing student extensions,
  and calls the deterministic assembler. Existing imports from `model` are
  aliases of the extracted types and helpers.

`RobotState` and every existing class/helper implementation are AST-identical
to the pre-extraction source. This stage does not claim that all remaining
state/simulation responsibilities have been separated.

## Preserved behavior and deliberate ownership change

The frozen fixture records every default dataclass field and ordering for
74 modules and 470 settings, with exact source-file digests. Setting names,
module IDs, defaults, options, descriptions, keybindings, enabled values, and
function-bar order match. Duplicate candidates remain ordered duplicates.
The settings helpers retain their existing direct-call mutation semantics.

Discovery remains exactly one profile-list call, two ordinary controller-port
calls, then nonblocking RobotLink/DT7/CC calls, followed by student extensions.
The two existing blocking controller-port calls are deliberately unchanged;
this extraction does not introduce additional discovery or an asynchronous
timing change.

The intentional change is ownership: the baseline shared 21 option lists
between fresh registries. The assembler now gives each setting its own options
and copies mutable values. Extension prototypes keep their original registry
hooks and are not rebound or mutated. Editing one registry cannot change a
different registry or the next default construction. The fixture retains the
legacy alias list as evidence; its default observations were not regenerated.

Source-location-specific audits now inspect the declaration file and exclude
it from consumer evidence. Public option tests use compatibility imports
rather than executing sliced assignments from a particular file.

## Reproduction

```sh
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tests/test_default_catalog_characterization.py
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tests/test_module_catalog_boundaries.py
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tests/test_configuration_boundaries.py
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tests/test_quick_action_configuration.py
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tests/test_controller_angle_integration.py
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tests/test_binding_restore_quality.py
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tests/test_cs2_settings_center.py
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tests/test_ui_state_regressions.py
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tests/api_freeze_test.py
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tools/test_settings_are_read.py
python tools/quality_gate.py
```

Use an isolated `POLAR_USER_DATA_ROOT` for UI tests. The standalone-catalog
regression actively blocks runtime-state, backend, serial, UI, and Qt imports
while constructing all 74 modules. These are offline tests and offscreen API
checks, not native desktop, firmware, device, or robot qualification.
