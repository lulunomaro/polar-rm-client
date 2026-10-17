# Economy interaction and content boundaries, 2026-10-16

## Scope and fixed checkpoints

Original source: `71ebb0f570441c12eb0d15b0a992c9cc3e805aeb`.
This is focused offline evidence, not an aggregate release result or native
OpenGL, broker, referee, robot, or physical-control qualification.

| Checkpoint | Change |
| --- | --- |
| `e64d6e6` | Characterize original selection, confirmation, send, and qualification effects before refactoring; add actual-window command tests |
| `8c4a9d9` | Separate selection query from explicit reconciliation, preserving the old compatibility entry point |
| `b70f71e` | Extract value-driven economy content drawing with narrow drawing/locale/navigation capabilities |
| `b97b2ce` | Extract immutable activation/release decisions and share the hold duration |
| `ba0a4b8` | Add confirmation-budget negative controls, migrated-caller guard, and actual-window one-shot qualification failure test |
| `dafe885` | Preserve lazy clock/key/deadline observation order for legacy hosts |

The standalone source digest at `dafe885` is
`f2282d67f2af9f65fbbaa25fdab8635f71fe22d412b239071bbdd1e0e8b185c0`
using `tools/quality_gate.py`. Integration with other lanes must be measured and
tested again; their changes are not included in this checkpoint.

## Ownership and compatibility

- `economy_selection.selected_offer` is a value-only query. It returns the
  matching offer, the first category offer, or `None`, without writes.
- `selected_economy_offer` is the window's read-only query.
  `reconcile_economy_selection` explicitly commits a missing-key fallback and
  resets its index, while preserving a valid selection's existing index.
- `economy_selected_offer` remains the historical read-and-normalize wrapper.
  All seven existing production call sites now name reconciliation explicitly:
  three keyboard branches, economy open and two scene builders, and CS2 grid
  construction. No public signature or historical API fixture was changed.
  An AST guard rejects new direct production calls to the ambiguous wrapper;
  dynamic external callers continue to receive its original behavior.
- `economy_interaction` returns immutable activation/release decisions. It
  owns UI eligibility and confirmation timing rules, not transport authority.
  Named clock/key/deadline queries preserve rejection and short-circuit
  observation order. There is no generic host/state proxy.
- The window still owns arm/hold/selection writes, notifications, clock supply,
  latest-offer refresh, transaction admission, sender calls, and qualification
  consumption/relocking. A successful hold decision still goes through
  `submit_economy_offer`, and the existing bridge remains authoritative.
- `economy_content` draws from explicit context/offer/confirmation/transaction
  values and geometry. It receives font/locale and drawing capabilities, plus
  narrow category animation/hotspot callbacks where required. It receives no
  window, registry, mutable match-state owner, or transaction bridge object.
- The existing scene adapters retain UI state reads/writes, selection
  reconciliation, scroll bounds, animation, clipping/unwind ownership, hotspot
  registration, student-reload details, CS2 feedback, and compositor submission.
  Rendering is therefore **not globally pure**: selection normalization remains
  at the original render stage to preserve behavior and interrupted prefixes.
- Locale queries stay lazy at their original drawing points. Compatibility
  pending-cost reads also remain at their original point after balance drawing;
  they were not eagerly collected into a broad scene snapshot.

At this standalone checkpoint, content/selection/interaction/presentation still
import immutable values or label dictionaries from the historical `economy`
module. They do not receive its bridge object, but this remains a module-level
transaction-owner dependency pending alignment with the separate domain-values
lane. No claim of complete domain import independence is made here.

## Behavioral evidence

1. The command reference copies eight original methods before refactoring.
   Its frozen file SHA-256 is
   `8f6bf8a7de0bf1240df5e6672a25f4d2c09a275f0a9d4784f01fdc9457d942b6`.
   There are 297 paired scenarios and 447 paired external-call failure prefixes,
   comparing ordered writes/effects, final state, result, and exception.
   Another 32 activation cases and 48 failure prefixes observe legacy scalar
   reads explicitly. This is compatibility-order hardening, not evidence of a
   real-window scalar-field defect.
2. The new scene reference copies both original scene methods. Its SHA-256 is
   `31b798cb502ad718020c5c17aca5a01b27bb408739b6035bd6fdebbd6681b99f`.
   There are 72 paired complete scenarios and 2,344 paired injected drawing,
   font, locale, animation, and hotspot failure prefixes. These compare command
   order, selection/scroll, clips, painter stack/opacity, glass, and exceptions.
   The independent reviewer also compared both reference method ASTs against
   the fixed original Git source, not just against the new fixture hash.
3. The existing **84 frozen render digests** are unchanged. Layout, locale,
   CS2 confirmation/hotspots, and keyboard ordering suites remain active.
4. Eight actual `PolarRMWindow` tests use isolated runtime files and inert
   device starts. They cover repeated keys, latest availability on hold release,
   outside release, pending configuration and transaction guards, selection
   compatibility/query purity, and real backend qualification consumption and
   relocking after a publisher exception. No local gold mutation or duplicate
   pending purchase is allowed.
5. One actual-window test compares original/current GPU command lists,
   hotspots, and exact raster `QImage` bytes at 1280×720 and 1920×1080 with
   hold/double-confirm states. A bridge stub rejects any attempted purchase
   during drawing. These are CPU raster/command checks, not native GL pixels.
6. Independent fragment/selection/intent tests construct no window or bridge.
   Import subprocesses guard against model/backend/window dependencies; the
   selection and interaction modules also import no Qt.
7. Negative controls change hold duration from 0.60 to 0.59 seconds and double
   confirmation duration from 2.8 to 3.8 seconds. Both are detected against the
   untouched original command reference.

Independent review passed `8c4a9d9` (75 focused methods), `b70f71e` (53), and
`b97b2ce` (35), and `dafe885` (36, including added read-order and negative
controls). These are overlapping scoped runs, not additive totals.

## Structural measurements

Measurements use the same current static analyzer on exported original source
and this lane's source; no ratchet budget was raised or baseline refreshed.

| Method | Statements, before → after | Decisions, before → after | Direct self names, before → after |
| --- | --- | --- | --- |
| `build_gpu_economy_workspace` | 198 → 131 | 77 → 45 | 25 → 25 |
| `draw_economy_overlay_compat` | 179 → 123 | 58 → 33 | 25 → 25 |
| `activate_economy_offer` | 14 → 11 | 6 → 2 | 5 → 5 |
| `finish_economy_hold` | 13 → 14 | 8 → 6 | 7 → 7 |

The largest new content function has 27 statements. The economy mixin shrank
from 1,422 to 1,105 source lines, but this is not the acceptance criterion:
new fragments are tested with explicit values/capabilities independently of the
window. Direct window coupling in the remaining adapters is unchanged.

Measured `rm_client` tree totals are 190 → 193 Python modules, 3,211 → 3,230
functions, 36,668 → 36,695 statements, and 12,901 → 12,899 decisions. Added
interfaces make total statements/functions larger. This is not a claim of
whole-tree complexity or performance reduction. Both trees have zero runtime
initialization cycles under the existing gate; deferred domain edges are a
separate lane.

## Reproduction

Validation environment: Python 3.12.14, PySide6 6.8.2, numpy 2.5.4,
OpenCV 5.0.0.93, OpenVINO 2026.4.1, Paho MQTT 2.1.0, pyserial 3.5.
Interpreter used: `/workspace/shared/client-quality-venv/bin/python`.

Final focused run on `dafe885`: all 14 listed scripts PASS, consisting of
140 unittest methods plus the actual API-freeze script. Zero failed or skipped
methods. The static quality gate and `git diff --check` also PASS. Historical
API capture/contract files, the 84-render trace file, and quality budgets are
unchanged. This remains separate from the parent's aggregate runner.

```sh
export CI=true QT_QPA_PLATFORM=offscreen
export POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1 PYTHONPATH=.
PYTHON=/workspace/shared/client-quality-venv/bin/python
for suite in \
  test_economy_selection test_economy_interaction \
  test_economy_interaction_characterization test_economy_content \
  test_economy_scene_failures test_economy_confirmation \
  test_economy_presentation test_economy_layout test_economy_display_locale \
  test_cs2_buy_matrix test_keyboard_routing_characterization \
  test_window_economy_commands test_window_api_contract api_freeze_test
do
  "$PYTHON" "tests/$suite.py" || exit "$?"
done
"$PYTHON" tools/quality_gate.py --json tmp_quality/economy/current.json
git diff --check
```

These direct scripts do not acquire or bypass the aggregate runner's global
lock. The authoritative test registry discovers every new `test_*.py` suite as
`offline`; aggregate execution and final integration evidence belong to the
parent task. The module/backend-map generator was run and its diff inspected;
the shared document is left to the integrated-tree regeneration owner.

Expected offscreen warnings about unsupported `QOpenGLWidget` and unavailable
audio devices do not constitute native presentation/audio evidence. Test
assertions and process completion determine the focused result.
