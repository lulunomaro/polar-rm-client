# Scroll target policy checkpoint

## What changed

`scroll_target_at` independently chooses the last matching live GUI viewport
and returns a semantic scroll family, key, maximum and (for legacy panels) a
whitelisted owner attribute. It neither sees a window nor changes models.

The Qt adapter still owns every actual scroll effect and the combat-input gate.
The four legacy panel routes now share their owner lookup/write path; the three
Jello routes share the exact 35-pixel gesture conversion, with the original
second live angle read retained. Successful viewport effects share a single
frame-force/event-accept tail. Retained Polar paths keep their 28-pixel step.
Unknown viewport modes keep the existing unified-inertia fallback and do not
fall through to combat output.

This is not a wholesale relocation of wheelEvent. Its modal and PolarPanel
special paths remain in place, and the pure selection policy can be tested
without importing Qt or constructing a window.

## Evidence

Before changing production behavior, `7d522a2` froze the original wheel entry
point from `8dba917` and ran the differential characterization. The immutable
fixture is checked by digest. Current coverage comprises 375 scenarios and
5,356 injected observable failure prefixes, checking both traces and final
scroll state. It includes pixel/angle gestures, changing event angle reads,
live viewport metadata mutation, every known family plus unknown fallback,
existing/new settings inertia, modal owners, and combat fallthrough. A deliberately
wrong-family policy is rejected by comparison with frozen emitted effects.

Executed with shared hash-pinned Linux Python/PySide6, offscreen, PYTHONPATH=.:

- test_wheel_routing_characterization.py: 3 passed
- test_scroll_routing.py: 6 passed
- test_pointer_routing_characterization.py: 2 passed
- test_economy_keyboard_release.py: 6 passed
- test_economy_interaction.py: 8 passed
- api_freeze_test.py: compatibility baseline unchanged

The first direct economy keyboard invocation omitted PYTHONPATH and failed
during import; rerunning with the repository root on PYTHONPATH passed.

## Scope and remaining debt

The wheel fixture intentionally shares production InertialScroll and economy
layout helpers, so it qualifies route/effect equivalence, not an independent
implementation of inertia or grid layout. Its economy branch exercises the
legacy layout, not CS2 grid-enabled layout. This checkpoint changes neither
helper nor economy branch. Desktop GPU, physical wheel devices and robot
hardware were not tested.

Remaining meaningful input work is drag ownership: movement/commit logic still
uses string-tagged dictionaries and reaches through the whole window. A next
stage should freeze slider/Jello/hotbar/panel drag sequences (including focus
loss and exceptions) before introducing typed drag intents and narrow effects.
Estimate one or two focused stages; large keyboard/action dispatch also remains.
