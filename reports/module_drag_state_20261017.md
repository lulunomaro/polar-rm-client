# Module drag state and release value checkpoint

## Characterization first

`431645d` extended the digest-checked original pointer-route fixture with drag
movement effects before production changes. It covers 82 scenarios and 900
injected effect/read failure prefixes for sliders, Jello module gestures and
old/Polar panels: threshold boundaries, invalid/missing starts, replaced
captures, cancellation and partial model writes.

One important preserved behavior is live panel offsets: if writing x replaces
the capture, y reads the successor offset. For module motion, storing the point
can replace the capture, and the moved flag is then written to the successor.
The new negative-control test rejects a deliberately snapshotted module capture.
No panel/slider geometry was prematurely converted into a snapshot command.

## State boundary

PointerInteraction now owns module-drag position/threshold accumulation and
exports an immutable ModuleDragRelease (name, moved) when consuming the gesture.
The input adapter keeps routing, rendering notifications and actual module or
hotbar effects; it no longer interprets a mutable module payload after transfer.
Payload normalization occurs after consumption, in the original module-name
then moved-flag order. A payload error cannot resurrect the gesture, and a
successor installed during payload reads remains untouched.

The legacy dictionary/field surface remains compatible with rendering and
extensions. Slider/panel effects deliberately remain in the adapter: replacing
their live writes with eager typed data would change behavior rather than reduce
responsibility safely. This is a limited module-state boundary, not a claim
that all drag ownership is now fully typed.

## Executed checks

Shared hash-pinned Python/PySide6, QT_QPA_PLATFORM=offscreen and PYTHONPATH=.:

- test_drag_movement_characterization.py: 4 passed
- test_module_drag_state.py: 4 passed
- test_drag_release_commit.py: 7 passed
- test_drag_button_ownership.py: 4 passed
- test_pointer_routing_characterization.py: 2 passed
- test_pointer_interaction.py: 9 passed
- test_tactical_map_input_ownership.py: 13 passed
- api_freeze_test.py: unchanged compatibility baseline

No physical input devices, desktop GPU, transport or robot behavior was
qualified. Parent integration/aggregate verification remains separate.

## Remaining effort

The largest remaining input responsibilities are action-handler window access,
keyboard priorities and slider/panel movement effects. Panel extraction needs a
live state port rather than snapshotting geometry; expect another focused stage
plus real-window UI regression before broadening that boundary.
