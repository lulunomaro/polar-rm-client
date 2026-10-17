# Independent Jello release review, 2026-10-17

Reviewed exact `d30ea097b391cb877aff14e119d20816f68c0501` in a detached
worktree. No blocking finding. The change intentionally fixes replay after
partial failure/reentry; it is not claimed to preserve those defective results.

`take_drag()` detaches the Jello command before lookup/toggle/assignment and
fallible notification/save/cache work. A nested release therefore cannot claim
the same pending command. The local original is used for remaining effects.
The panel branch is mutually exclusive, so a successor installed by a callback
is not saved as part of the old release. Final retirement uses identity, leaving
a distinct successor intact after ordinary completion. Exceptions also leave
that successor intact because the original was already detached.

Inspected positive click/drop, epoch cancellation, lifecycle/modal precedence,
partial toggle mutation followed by exception, later toast/save/cache failures,
assignment failure, recursive release and slider/panel successor cases. The
existing right-button release of a left-started drag is separately disclosed in
`reports/input_drag_commit_20261017.md`; this fix does not change button routing.
No broader transaction rollback or physical-input qualification is established.

## Independent validation

Linux, CPython 3.12.14, prepared hash-pinned environment. With `PYTHON` naming
that interpreter in the detached checkout:

```sh
export PYTHONPATH=. QT_QPA_PLATFORM=offscreen CI=true POLAR_AUTOSTART=0
export POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1
"$PYTHON" tests/test_drag_release_commit.py
"$PYTHON" tests/test_pointer_routing_characterization.py
"$PYTHON" tests/test_pointer_interaction.py
"$PYTHON" tests/test_tactical_map_input_ownership.py
```

All exited 0: 7 + 2 + 9 + 13 = 31 tests, no skips. Tactical-map construction
emitted headless GL/font/audio warnings and two painter saved-state warnings;
these are not evidence of native graphics or audio qualification. Git status
remained clean.

For an independent negative control, read the exact pre-fix input mixin from
`6d337c0` with `git show`, AST-extracted only `mouseReleaseEvent`, compiled it
against the candidate module's globals, and temporarily patched that method
while executing the final `DragReleaseCommitTests`. Its seven methods produced
nine assertion/subtest failures and zero errors. Restoring the candidate method
returns the passing behavior above. This confirms the regression is rejected
without editing frozen fixtures, source, thresholds or the repository baseline.
No aggregate verifier or shared-lock bypass was used.

## Subsequent initiating-button correction

Independently reviewed exact `8df72aaef938010dca4ec314a72d6be1158925f4` in
another detached worktree. The earlier cross-button limitation above is historical
for `d30ea09`: this follow-up now resolves it. Production
`_on_action_jello_module_press` already accepts only button 1 (left). The added
release guard accepts non-left events without committing or clearing that Jello
capture; a later left release still commits. The guard is confined to Jello
captures, after existing lifecycle/modal gates and before taking the command.
No blocking finding or unrelated button-routing change found.

Using the same interpreter/environment as above, independently ran:

```sh
"$PYTHON" tests/test_drag_button_ownership.py
"$PYTHON" tests/test_drag_release_commit.py
"$PYTHON" tests/test_pointer_routing_characterization.py
"$PYTHON" tests/test_pointer_interaction.py
```

All exited 0: 4 + 7 + 2 + 9 = 22 tests. Checks include left-only production
admission, right/middle interruption of click/drop, matching release, canceled
focus epoch/fresh capture, and recursive release. As a negative control,
AST-extracted the pre-fix release from `94354fb` and temporarily substituted it
while running the final button-ownership suite: four methods produced five
assertion/subtest failures, zero errors. The detached worktree remained clean.
Physical mouse/device qualification and full integrated verification are still
separate; no aggregate lock was bypassed.
