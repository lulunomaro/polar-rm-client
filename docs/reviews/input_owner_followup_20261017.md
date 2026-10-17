# Input ownership and shutdown admission follow-up

## Corrected complete result

Local `f78a489fe3e606daf6cb8b331ec6247eabc38d18` equals public source
`45628b1ce17618a8c55ee5f9cf1f60e99be50039`, tree
`aabd31bbbc40f74160d590ee3241a9643df03858`.
Complete selected offline profile: **341 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL**.
Aggregate remains **NO-GO** for external prerequisites. All 690 declared inputs
retained SHA-256
`0a96b8199a1f02538495ff9e33cb441c68199aacf9683d4991157deabd8c122f`.
Native Windows/GPU/audio/device/portable qualification and historical timing
causation remain open. Report-only follow-ups change no tested source.

## Rejected integrated candidate

Local `5cf2ab3c06deceef6e2febd7bc310cda652c3d8f` equals public
`007a0a24504579bdfafc2ba95bb82f57c7be3ce5`, tree
`eff914a1f7d933ef70920040836eea74457b04ff`.
Full selected offline profile: **340 PASS / 12 SKIP / 4 INCOMPLETE / 1 FAIL**.
690 declared inputs retained start/end SHA-256
`b5d3d9a3413a591df282fa149bd7c896753eb28c74794505f6242da5eb696a6f`.

The failed `test_ui_state_regressions` suite had 30 methods and one error:
its extracted lock-cleanup function lacked the real newly introduced
`_cancel_economy_hold_input` helper in its execution globals. The correction
imports the production helper and logging, preserves the original last-command
before-writer and qualification assertions, and verifies that pending economy
key/owner/rectangle are already retired inside reset_inputs. All 30 methods pass
independently after correction. A no-op-helper negative control fails the new
hold-state assertion; no cancellation behavior is stubbed away.

## Production scope already integrated

- Cleanup-thread constructor rejection is reported as a retryable resource
  failure rather than leaving phantom pending shutdown work. Independent real
  thread probes preserve live/finished admission and real close outcomes.
- Economy holds belong to the initiating mouse button or Return/Enter key.
  Unrelated releases do not confirm them. Modal/focus/lock changes retire holds
  before fallible callbacks, including failed-reset and reentrant-release cases.
  Real mock transaction checks retain eligibility and pending-purchase gates.
- Closed ClickGUI geometry cannot consume wheel input before repaint. Visible
  scroll and higher-priority chat/economy handling remain intact; fallback still
  uses the existing combat permission gate.
- Bounded early-constructor tests observe actual Qt object retirement and idle
  field-log thread exit. They are not universal resource-cleanup certification.

The fixture correction changes no production code. A new complete regression is
required for its final source identity; the rejected result above remains valid
historical evidence. Raw logs remain local. Native Windows/GPU/audio/device and
portable acceptance are not performed here; see the Windows handoff document.
