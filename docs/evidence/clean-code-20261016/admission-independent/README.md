# Independent admission ownership review

**PASS for the two scoped fixes; no blocking finding.** This reviewer did not
write the production change. Review source is fixed at
`70c410c65aa2fc781bae758b945e62e99bc6d27b`, tree
`b5b8ce281e45ca230e453167528e68105c2cf643`. Baseline is
`71ebb0f570441c12eb0d15b0a992c9cc3e805aeb`; red fixtures are
`8ff426f27f1200ccefeb2ac8082dba2cb4ebb1b5` (production identical to baseline).
This evidence commit adds only tests and review evidence. Serial discovery
fixture `706b136` is outside this review.

## Independently executed evidence

- Eight focused/adjacent suites: **80 PASS**, 0 skips.
- Six additional lifecycle suites: **76 PASS**, 0 skips.
- Five reviewer-authored probes: **5 PASS**, 0 skips. Registering them as
  `tests/test_admission_ownership_independent_review.py` preserved all function
  ASTs; the registered file was then rerun: **5 PASS**, 0 skips. This is a repeat,
  not five additional distinct tests.
- Original eight admission tests on red: **3 PASS, 4 FAIL, 1 ERROR**, exit 1.
- Independent probes on red: **2 PASS, 2 FAIL, 1 ERROR**, exit 1.
- Static quality gate: **PASS**, no violations. Production source SHA-256:
  `91b2716555d144a3bad2f18cde8e34747d1bf29334e24213477479af90d86d0c`.
- `git diff 71ebb0f..70c410c --check`: PASS. Read-only registry discovery found
  the two original admission suites as offline checks.
- Environment: Linux x86_64/glibc 2.41, Python 3.12.14, pre-existing shared venv.
  All test commands used the four environment flags below.

Logs are adjacent; `manifest.json` records their SHA-256, results and commands.
These separate direct runs are **not aggregate or native qualification**.
No aggregate runner/lock, remote writes, main merge, user Codex control, paid
service call, physical serial/device test, or native GPU test was used.

## Source and adversarial review

`ControllerAngleRelay.configure` constructs the candidate before publishing its
ownership. A rejected start releases only never-admitted state, sets the candidate
stop event and rethrows the identical RuntimeError. A genuinely started worker
retains its reservation through late start-wrapper failure, reconfiguration and
blocked native open/close. Strict close and same-configuration retry work after
outright rejection. Permission, freshness and wire behavior are unchanged.

`RotatingJsonlLogger` handles admission errors under its condition lock. It always
preserves a sanitized cumulative diagnostic and clears queued work only when the
failing never-admitted candidate still owns it. Real in-flight, finished and
successor writers therefore retain their correct accounting. Constructor
RuntimeError now returns False with accounted loss, matching start rejection.

Independent probes also check concurrent queued producers; close drain versus
cancel while admission is rejected; cancellation with a genuinely in-flight
writer and late start error; repeated constructor failure followed by successful
recovery; same-angle-configuration retry; and old angle reservation retained when
configuration changes during a blocked native open. No negative pending counts,
double-counted losses, false drain, forgotten failure, or overlapping owner was
observed. Error object identity remains visible for angle failures; logger
messages remain sanitized and losses/errors survive subsequent success.

## Reproduce

In an isolated checkout of the review source, with the additional test file from
this evidence commit, set `PY` to the existing Python interpreter and run:

```sh
export CI=true QT_QPA_PLATFORM=offscreen
export POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1
PYTHONPATH=tests:. "$PY" -m unittest -v \
  test_controller_angle_admission test_field_log_admission \
  test_field_log_lifecycle_quality test_backend_angle_lifecycle \
  test_angle_writer_handoff test_dt7_angle_ownership \
  test_controller_angle_safety_review test_controller_angle_integration
PYTHONPATH=tests:. "$PY" -m unittest -v \
  test_backend_async_lifecycle test_backend_lifecycle_controller \
  test_backend_lifecycle_callback_failures test_backend_lifecycle_input_failure \
  test_lifecycle_coordinator test_mqtt_lifecycle_nonblocking
"$PY" tests/test_admission_ownership_independent_review.py
"$PY" tools/quality_gate.py
git diff 71ebb0f..70c410c --check
```

Repeat the two original admission suites in an isolated `8ff426f` checkout to
reproduce the red result. For the extra red probes, copy only the independent test
file into that checkout's tests directory; do not change production files. The
recorded red run used an external harness selecting that checkout explicitly;
its function ASTs are identical to the committed independent tests.
