# Pointer hit-selection policy checkpoint

## Scope

The existing mouse press, hover and owned-left-release routes now share one
`topmost_hotspot` policy. It knows only target precedence, optional button
eligibility and geometry, and returns the original target identity. It does not
own a window, mutate control samples or authorize commands. Modal guards,
capture state, map protection, dispatch and control admission remain in their
original event order. This removes three independent implementations of the
same last-painted-target policy rather than relocating an event handler.

Hover deliberately does not read target buttons. Press/release reject a button
before reading geometry. Selection remains lazy over the live reversible target
collection: no snapshot, eager filtering, read-ahead or swallowed exception.

## Characterization before production changes

Commit `1502317` freezes the three original mouse entry points from `1b2f580`
and adds ordered differential effect/read tests. Both old and new routes run
against the same inert host; the fixture is digest-checked. Cases cover left,
middle and right buttons, empty/overlapping targets, dynamic target eligibility,
GUI exit animation, lifecycle/escape/student/radial/binding ownership,
background press and retained releases. Every observed failure prefix is also
compared, including already-applied pointer state. This is behavior evidence,
not proof of desktop GPU or physical hardware behavior.

## Executed verification

Shared hash-pinned Linux Python, PySide6 6.8.2, QT_QPA_PLATFORM=offscreen:

- test_pointer_routing_characterization.py: 2 tests passed
- test_hit_testing.py: 5 tests passed, including live list replacement and lazy reads
- test_pointer_interaction.py: 9 tests passed
- test_focus_loss_failures.py: 7 tests passed (pre-refactor safety characterization)
- test_tactical_map_input_ownership.py: 13 tests passed, real offscreen window
- test_window_focus_loss.py: 2 tests passed, real offscreen window
- test_keyboard_routing_characterization.py: 17 tests passed
- api_freeze_test.py: compatibility baseline unchanged

Qt emitted expected headless audio warnings and existing painter saved-state
warnings in map tests; no failures or skips were reported. No robot output,
real display, user Codex task or paid API was exercised.

## Remaining debt

This is a deliberately bounded policy extraction. The input adapter still owns
large heterogeneous key/scroll and drag-effect routes; action handlers retain
whole-window access. A next meaningful stage is an explicit scroll-target
policy with characterization of live viewport reads and failure prefixes,
followed by a narrow scroll effect adapter. Estimate: one focused stage plus
cross-modal regression, not a claim that input routing is fully decomposed.

## Independent review correction

Independent review exposed a hover-only read-order regression: the initial
extraction captured `hover_point` once, while the original reads the live field
for each candidate after resolving that candidate's geometry/contains method.
Commit `bc27814` adds the dynamic coordinate regression before the correction;
it produced 84 differential failures against the first extraction. Hover now
supplies a lazy coordinate callback; press/release retain their local points.
The corrected differential suite passes; pure hit policy now has 6 passing
tests, including geometry-before-coordinate read order. Map ownership's 13 tests
and the API freeze were rerun after correction. No safety guard was changed.
