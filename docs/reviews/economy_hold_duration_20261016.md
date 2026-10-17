# One authoritative economy hold duration

Base: combined `66deab1d34ea56e0e9ce07812ef6b565491b704f`, in a fresh worktree.
Pre-edit grid characterization: `3cfb31b` (same artifact as `7948b1c`).
Altered-duration negative-control commit: `abd6af7`.

Production changes are only importing `HOLD_SECONDS` from the existing interaction
owner and replacing the CS2 grid's duplicated `.6` denominator. No generic
presenter, configurable variant, field-read changes, new authority check, or
render geometry/hotspot/command ordering change was introduced. Existing text
literals remain unchanged; this is not runtime-configurable hold-duration UI.

The test changes the authoritative duration before importing dependent modules in
three separate subprocesses (.4, .8, 1.2 seconds). The actual grid must show 75%
at three-quarter duration, matching the shared presentation value; completion
must remain TOO_SHORT then become SUBMIT exactly at the same configured constant.
The baseline fails all three cases; the candidate passes. Output is inert and no
transaction is submitted. This guards against a future source constant update
leaving a renderer's progress denominator behind.

All 320 pre-existing grid output/field-observation cases remain unchanged; the
AST execution scope merely supplies the new imported constant. Distinct grid and
shared-presenter precedence and lazy observation rules are deliberately retained.

Results: 45 focused methods PASS, including the three subprocess controls in one
new method, plus all 320 projection/read-order cases and static quality gate.
Logs: `docs/evidence/clean-code-20261016/economy-hold-duration/`. Trailing log
whitespace is normalized only. No native hardware, OS input or GPU qualification.

```
PYTHONPATH=. python docs/evidence/clean-code-20261016/economy-grid-audit/review.py
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest test_economy_hold_duration_alignment test_cs2_buy_matrix test_economy_content test_economy_presentation test_economy_selection test_economy_interaction test_economy_confirmation
python tools/quality_gate.py
```
