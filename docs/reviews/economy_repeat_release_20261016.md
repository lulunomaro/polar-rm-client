# Economy confirmation: physical release ownership

## Reproduced defect

Before this change, `InputDispatchMixin.keyReleaseEvent` always called
`finish_economy_hold` for Return/Enter while the economy workspace was visible.
Qt autorepeat key-up events can occur while the physical key remains down. A
synthetic release therefore cancelled a short hold, or submitted a completed
hold before physical release. The corresponding press route already ignored
repeat activation. This is a concrete ownership mismatch, not grounds to split
the large router into helpers accepting the entire window.

Tests use real `QKeyEvent` objects, production `begin_economy_hold` /
`finish_economy_hold`, a controlled clock and an inert submission recorder.
No live command, transaction or purchase is made. The frozen old route still
reproduces short-hold cancellation and long-hold submission. The pre-fix red run
has seven failures across repeat timing/key variants and the held sequence.

## Narrow correction

Only non-autorepeat Return/Enter releases complete economy confirmation. Repeat
releases remain consumed by the existing economy branch and cannot reach control.
Physical release retains the old behavior: short holds cancel with feedback,
valid holds go through the existing submission adapter, and duplicate releases
cannot submit twice. No authorization, availability, transport or time threshold
is loosened. No press route, E-stop route, modal priority or focus code changed.

The historical routing fixture and its integrity hash remain untouched. The
existing differential suite uses one named, explicit oracle correction:
`correct_economy_repeat_release` replaces only the frozen hold-completion effect
for repeat Return/Enter release events. It retains every earlier branch, effect
and observation and does not normalize unrelated traces. The separate frozen
regression demonstrates exactly why this exception exists.

## Residual routing assessment

The press path's E-stop handling precedes lifecycle gating; release of E-stop is
still blocked during lifecycle transitions. Lifecycle, menu-owned key-up,
student panel, binding/search, radial wheel and chat retain their priority before
economy release. Press and release routes are intentionally asymmetric: an
opened overlay must own its eventual release and some presses toggle local
modules as well as control bits. A blanket routing-table replacement would need
stronger ownership/event-loop evidence and is not justified by method length.

Focus-loss early-exception safety is a separate worker's scope. Native OS repeat
behavior, focus delivery, hardware outputs and end-to-end command acceptance
remain acceptance work; these tests establish the synthetic-event contract only.

## Reproduce

```
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest test_economy_keyboard_release test_keyboard_routing_characterization test_economy_interaction test_economy_interaction_characterization test_economy_confirmation test_fullscreen_input_extended
python tools/quality_gate.py
QT_QPA_PLATFORM=offscreen python tests/api_freeze_test.py
```

Logs: `docs/evidence/clean-code-20261016/economy-repeat-release/`.

Candidate results: 58 test methods PASS, including six focused hold-release
methods; API freeze and static quality gates PASS. Full-range diff check passes.

The red log has trailing whitespace stripped for the repository diff gate;
assertion text and failure results are unchanged.
