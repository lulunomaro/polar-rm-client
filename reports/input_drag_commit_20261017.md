# At-most-once Jello drag release

## Reproduced problem

Jello release previously retained its drag dictionary until after module toggle
or hotbar assignment, notification, save and cache refresh. If a later step
raised, a second release could replay the command. A nested release from the
toggle callback toggled the same module twice. Unconditional final clearing
also erased a distinct successor capture installed by a callback.

Red regression commits `61e1cf8` and `6d337c0` preceded production changes.
The first run reported five failures (including a double toggle); adding the
successor case produced six failures. This checkpoint intentionally fixes that
failure/reentry behavior rather than pretending it is unchanged refactoring.

## Responsibility change

PointerInteraction now transfers the captured drag with `take_drag()` before
Jello command callbacks. The release adapter executes that one command and
clears only an unchanged original capture; a successor is neither autosaved as
a panel nor erased. The dictionary compatibility surface remains intact. This
is an ownership/lifetime correction, not a cosmetic typed-dictionary rewrite.

Lifecycle, owned-background release, escape/student/radial gates and economy
hold ordering remain ahead of drag processing. Focus/epoch cancellation still
prevents a commit. No transport, robot permission or fire gate changed.

## Verification

Shared hash-pinned Python/PySide6 with QT_QPA_PLATFORM=offscreen, PYTHONPATH=.:

- test_drag_release_commit.py: 7 tests passed, including partial toggle failure,
  toast/save/cache failure, failed drop, nested release, successful click/drop,
  successor captures through callback failures and authoritative owner gates
- test_pointer_routing_characterization.py: 2 passed
- test_pointer_interaction.py: 9 passed
- test_tactical_map_input_ownership.py: 13 passed
- test_window_focus_loss.py: 2 passed
- api_freeze_test.py: compatibility baseline unchanged

Additional scroll-stage compatibility checks at the preceding policy code:
Jello description/scroll usability 8 passed, kill-selector regression 3 passed,
escape-menu 13 passed. Headless font/audio warnings were emitted, with no skips.

## Remaining issues

Movement still uses string-tagged drag data, and slider/panel effects still
reach through the window. A separate observed legacy behavior remains: a
right-button release can commit a left-started Jello drag. This stage does not
change button routing. A follow-up needs explicit initiating-button ownership
and multi-button interruption/release characterization before changing it.
Neither physical mouse devices nor desktop GPU presentation was qualified.

## Matching-button follow-up

The previously recorded cross-button problem was verified against production
acquisition: `_on_action_jello_module_press` rejects every button except left.
Commit `94354fb` adds four ownership tests before the correction; the uncorrected
release route produced five failures. The correction now accepts and consumes a
middle/right release without committing or discarding the active Jello capture.
Only the matching left release commits. Other drag kinds retain their existing
button rules (slider acquisition can intentionally use other buttons).

All 4 new tests pass, including production action admission, click/drop,
foreign-button interruption, epoch cancellation, fresh capture after cancellation
and nested matching release. Existing 7 command-commit, 2 pointer differential,
9 capture-state, 2 real-window focus-loss tests and API freeze were rerun and
passed. Focus/escape/lifecycle ownership still precedes this check.
