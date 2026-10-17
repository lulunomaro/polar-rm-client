# Independent live slider/panel edit review

Reviewed exact `b7ee352a200a8c15f13a5d0b4c4fd97408523c34` in a separate
detached worktree, against prior characterization `4d439a3`. No blocking finding.

The boundary earns a modest amount of additional code: press and movement now
share one slider ratio policy, while local model edits can be tested without Qt
or a whole window. The functions receive a selected model, point and live
capture reader; they cannot select modules, schedule frames or publish controls.
Admission, collection/category lookup and notifications remain in the adapter.
This is not a full typed-drag conversion or removal of all window coupling.

Compared source evaluation order: slider setting/rectangle are resolved through
separate live reads; ratio application returns the original setting even if it
cancels capture, preserving the subsequent adapter callback target. Panel x/y
use live offsets after preceding writes, including the optional dragged flag.
Exceptions preserve the already-applied prefix; no hidden retry or normalization
was introduced. Existing original-capture ownership/button fixes remain ahead
of release effects.

With the prepared pinned CPython 3.12.14 interpreter as `PYTHON`, Linux,
`PYTHONPATH=. QT_QPA_PLATFORM=offscreen CI=true`, independently ran:

```sh
"$PYTHON" tests/test_live_drag_edit_contract.py
"$PYTHON" tests/test_drag_editing.py
"$PYTHON" tests/test_slider_press_contract.py
"$PYTHON" tests/test_drag_movement_characterization.py
"$PYTHON" tests/test_drag_release_commit.py
"$PYTHON" tests/test_drag_button_ownership.py
"$PYTHON" tests/test_pointer_routing_characterization.py
```

All exited 0: 4 + 6 + 3 + 4 + 7 + 4 + 2 = **30 tests**, no skips. The suites
include live capture replacement, failure prefixes and frozen-route comparisons.
Detached worktree remained clean. Native mouse/Qt desktop presentation and the
full integrated profile are separate evidence. No aggregate run or lock bypass.
