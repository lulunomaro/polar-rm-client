# Independent review: mouse button admission

Reviewed exact 2b968a520a09c4daab76f04d4ea70f8a6eafa3f9 in detached worktree.
No blocking findings. The recognized table is Left=1, Middle=2, Right=3.
Unsupported and compound values map to zero. Both press and release guards
follow lifecycle ownership and precede capture/modal mutation. Existing
recognized routing is unchanged; unknown release cannot consume held left or
pending background-release ownership.

Executed with pinned Python, QT_QPA_PLATFORM=offscreen CI=true PYTHONPATH=.:
- mouse_button_admission: 8
- pointer_routing_characterization: 2
- drag_button_ownership: 4
- clickgui_background_dismiss: 14
- tactical_map_input_ownership: 13
All 41 unittest methods pass; outputs attached. API freeze script also passed
1114 established members / 540 signatures / 78 private seams unchanged.

Inspected positive controls: all three supported button press/release pairs
reach actual KeyboardControlBridge with real OfficialSafety armed in MATCH.
The inert sink still calls can_publish and mark_published; locked negative
control attempts output but admits no frames. Explicit fallback mutant
produces accepted left=True, demonstrating sensitivity to the original bug.
Unknown values preserve held left, drag object identity and background capture;
real left release still consumes them. Lifecycle precedence is independently
asserted using a translator that raises if reached.

No physical input, serial/network transport, receiver, GPU or audio output
capability is established by these tests. API script emitted expected offscreen
OpenGL and unavailable audio-device warnings; its compatibility assertion passed.
