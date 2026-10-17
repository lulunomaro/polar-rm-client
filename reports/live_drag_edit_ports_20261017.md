# Live slider and panel edit ports

## Scope and responsibility

The Qt input adapter still chooses whether a gesture is admitted, selects the
old/Polar panel collection and category, and delivers setting-change/rendering
notifications. Local slider/panel model writes now use a small edit module with
an explicit capture-reader callable. It has no window, registry, backend,
renderer or event object.

This removes duplicated panel-coordinate mutation and shares the same slider
ratio policy between initial press and subsequent movement. Slider edits return
the original setting for the adapter's existing notification. Panel edits use
an already-selected mapping and resolve each offset through the live reader.
There is no broad window-service wrapper or relocation of an event handler.

## Frozen live-order contract

Characterization commit `4d439a3` precedes production edits. Its 20 scenarios
and 549 injected read/effect prefixes observe host property reads, collection
lookup, capture fields, geometry-method lookup, ratio mutation and each axis
write against the original frozen mouseMoveEvent fixture. Existing movement
coverage adds 82 scenarios and 900 prefixes.

Key preserved boundaries:

- Slider target and bounds read the current capture separately.
- set_from_ratio is resolved before geometry evaluation.
- Cancellation during ratio mutation still notifies the original setting.
- Panel collection/category lookup stays in the adapter and retains read order.
- A panel write or lookup can replace capture; later offsets observe the new one.
- Exceptions propagate without retries, rollback, extra notifications or frames.
- Initial slider press still mutates value before installing capture and notifying.
- Existing slider button admission remains unchanged.

A deliberately cached-capture implementation is rejected by the frozen trace.
Pure port tests independently cover live offsets, cancellation, geometry bounds
and failure after the first panel-axis write.

## Executed verification

Shared pinned Python/PySide6, `PYTHONPATH=.`, `QT_QPA_PLATFORM=offscreen`, `CI=true`:

- test_live_drag_edit_contract.py: 4 tests passed
- test_drag_editing.py: 6 tests passed without importing Qt/window
- test_slider_press_contract.py: 3 tests passed
- test_drag_movement_characterization.py: 4 tests passed
- test_drag_button_ownership.py: 4 tests passed
- test_drag_release_commit.py: 7 tests passed
- test_pointer_routing_characterization.py: 2 tests passed
- test_clickgui_background_dismiss.py: 14 tests passed
- test_window_focus_loss.py: 2 tests passed
- api_freeze_test.py: unchanged compatibility baseline

These checks qualify local edits and event-order compatibility, not physical
mouse/robot behavior or desktop GPU presentation. Parent integration and its
aggregate verification remain separate.

## Remaining input work

The event adapter still has heterogeneous modal priorities and numerous action
handlers accessing the window. Those need independently characterized capability
boundaries. The new port does not claim all legacy dictionaries are typed or
that arbitrary callback-driven mutations are transactionally atomic.
