# Independent scroll-policy review, 2026-10-17

Reviewed immutable `1e02fbbb491fabd58f4d67d9fe79787fa7db1346` in a separate
detached worktree. Compared selector/adapter changes against the prior committed
wheel fixture stage `7d522a2`; no fixture bytes were changed by implementation.

## Findings

No blocking discrepancy found. The policy selects the existing owner and keeps
reverse live iteration, hit-only metadata reads and mode/key/maximum lookup
order. Unknown modes retain unified inertia, not control-output fallthrough.
The adapter still owns effects, the second live Jello angle read, bounds/clamp,
mark-scroll, frame refresh and acceptance. The common tail preserves their
previous order. Known legacy owner attributes are explicitly enumerated.

The new wrong-family mutation changes candidate effects while the frozen route
remains unchanged. Literal pure-policy expectations cover every mode, unknown
unhashable fallback, live-list replacement, lookup order and invalid maxima.
The differential additionally exercises creation of a missing settings motion.

Coverage remains deliberately scoped: baseline and candidate share production
inertia/grid helpers; economy grid-enabled rendering is not characterized.
The new-motion observation uses a default zero-state object when the map key is
absent, so it does not separately assert key existence after an injected failure.
This is a possible further test strengthening, not an observed behavioral defect.
No native GUI/input device or physical control qualification is established.

## Independent execution

Linux, CPython 3.12.14, prepared hash-pinned verification environment. From the
review checkout, with `PYTHON` pointing to that interpreter:

```sh
export PYTHONPATH=. QT_QPA_PLATFORM=offscreen CI=true POLAR_AUTOSTART=0
export POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1
"$PYTHON" tests/test_wheel_routing_characterization.py
"$PYTHON" tests/test_scroll_routing.py
"$PYTHON" tests/test_pointer_routing_characterization.py
"$PYTHON" tests/test_economy_keyboard_release.py
"$PYTHON" tests/test_economy_interaction.py
"$PYTHON" tests/api_freeze_test.py
"$PYTHON" tools/quality_gate.py
git diff --check
```

All exited 0. The five unittest scripts ran 3 + 6 + 2 + 6 + 8 = 25 cases.
API freeze matched the existing baseline; static gate reported 217 Python
modules, 3,406 functions and zero initialization cycles. The API check emitted
expected offscreen/OpenGL, font cache and missing-audio-device warnings; it is
API evidence, not native rendering/audio evidence. Checkout status remained
clean. No aggregate verifier was started or its lock bypassed.
