# Failed integration checkpoint

2026-10-16 05:33 UTC. This failed checkpoint is preserved intentionally; it must not be reported as a passing release.

- Tested local revision: `8c2ddc9`
- Exact Git tree: `af1f410cd070e92b6037b16a377b5c760d166c27`
- Report SHA-256: `13fe790a5e84a8fafbc0ff40b2e879fbc37d7dacf1f618e10362393040c8f3af`
- Result: **146 PASS, 12 SKIP, 4 INCOMPLETE, 7 FAIL**.
- Environment and reproduction command: same as the preceding wave3 checkpoint (Linux, Qt offscreen, no firmware selected).

Failed suites: polar_startup_test, smoke_test, student_frontend_test, test_binding_restore_quality, test_device_manager_nonblocking, test_lazy_settings_state, test_ui_state_regressions.

At recording time, diagnosis is ongoing. Some tests still model synchronous lifecycle or the former audio save call; these must be updated only where the changed public workflow is justified and equivalent production behavior is tested. Key/mouse and settings failures require actual settled-window interaction checks. This report predates subsequent fixes and cannot validate them.

The seven failures are separate from skipped firmware/cross-reference requirements. Correcting the tests does not waive hardware qualification or the independent review's open failure cases.
