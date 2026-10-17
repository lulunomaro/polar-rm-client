# Reject unsupported mouse-button intents

## Reproduced behavior defect

The Qt button adapter returned left=1 for every value except right and middle.
Back, forward, extra, no-button and compound synthetic button values therefore
became left presses/releases. In a real KeyboardControlBridge with OfficialSafety
armed and fighting, a BackButton press produced an accepted left=True snapshot
at an inert egress recorder. An unknown release could also release a held left
command; GUI hotspots and existing captures were affected by the same widening.

Red commit `233e443` precedes production correction and reported 14 assertion
failures. This is an intentional semantic safety correction, not unchanged
behavior relative to the frozen original routing fixture.

## Correction and preserved scope

A single translation table recognizes only left, middle and right. Unsupported
values map to zero. Press/release routes reject them after lifecycle ownership
and before focus, modal callbacks, hotspots or capture mutation. They cannot
synthesize a supported button or consume a pending supported release.

The existing qt_button API/signature remains; its formerly unsafe unknown-value
fallback intentionally changes. Recognized-button routing order and downstream
control/authority checks remain. Physical left/middle/right press and release
continue to produce their intended states when eligible. Locked authority still
rejects recognized output. No backend permission, fire mask or transport was
changed.

## Executed evidence

Pinned Python/PySide6 with PYTHONPATH=. and QT_QPA_PLATFORM=offscreen:

- test_mouse_button_admission.py: 8 tests passed; real QMouseEvent,
  KeyboardControlBridge and OfficialSafety, with inert output recorder
- test_pointer_routing_characterization.py: 2 tests passed
- test_drag_button_ownership.py: 4 tests passed
- test_clickgui_background_dismiss.py: 14 tests passed
- test_tactical_map_input_ownership.py: 13 tests passed
- api_freeze_test.py: unchanged compatibility baseline

Positive assertions exercise all three supported buttons and matching releases.
Negative cases include Back/Forward/Task/ExtraButton24/NoButton/compound values,
button-agnostic hotspots, retained captures, cancelled epochs, and locked control.
A mutant restoring the left fallback produces accepted left=True output and is
detected explicitly. No serial/network writer, actual robot or physical mouse
was used; accepted snapshots do not prove firmware or hardware behavior.
