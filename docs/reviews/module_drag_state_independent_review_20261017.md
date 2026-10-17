# Independent module-drag state review, 2026-10-17

Reviewed exact `ec80a3a91a0ff75f2fc1359c7fdfbd3384142be0` in a separate
detached worktree, against characterization stage `431645d`. The integrating
owner's frozen source was not modified. No blocking finding.

## Boundary and behavior

`PointerInteraction.track_module_drag` owns capture progress but preserves live
`self.drag` reads/writes. A point-store callback may replace the capture; the
subsequent moved flag targets that successor, matching the frozen route.
Threshold remains Euclidean and inclusive at six, and moved remains latched.
Window invalidation/scheduling effects stay in the adapter.

`take_module_drag` consumes before interpreting module name and moved payload,
then returns a frozen value detached from the legacy dictionary. Name conversion
precedes moved truth testing. The adapter's initiating-left-button guard remains
ahead of consumption. Existing partial-effect failure and successor retirement
semantics remain unchanged. The new methods assume an already-admitted module
capture; they are not generic APIs for arbitrary absent drag storage.

## Independent execution

Linux, pinned CPython 3.12.14 environment. With `PYTHON` pointing to that
interpreter, from the detached checkout:

```sh
export PYTHONPATH=. QT_QPA_PLATFORM=offscreen CI=true
"$PYTHON" tests/test_drag_movement_characterization.py
"$PYTHON" tests/test_module_drag_state.py
"$PYTHON" tests/test_drag_button_ownership.py
"$PYTHON" tests/test_drag_release_commit.py
"$PYTHON" tests/test_pointer_routing_characterization.py
"$PYTHON" tests/test_pointer_interaction.py
git diff --check
```

All exited 0: 4 + 4 + 4 + 7 + 2 + 9 = **30 tests**, no skips. This includes the
stale-capture mutation negative control and the prior positive/negative button
ownership paths. Detached status remained clean.

Two additional independent adapter probes used custom payload objects:

1. Module `__str__` installs a distinct slider successor and then raises.
2. Module `__str__` installs that successor and returns a name; moved `__bool__`
   then raises.

Both propagated the expected error, preserved the exact successor object, and
issued zero toggle or slot-assignment commands. They do not restore the consumed
original capture. No original fixture or public baseline was rewritten.

Acceptance is for this state/adapter boundary under controlled callbacks, not
native mouse/graphics qualification or a full integrated profile. No aggregate
verifier was started and no lock was bypassed.
