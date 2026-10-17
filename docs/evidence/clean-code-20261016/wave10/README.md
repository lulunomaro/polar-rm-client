# Stage-ten integrated checkpoint

Completed 2026-10-16 07:51 UTC: **213 PASS, 12 SKIP, 4 INCOMPLETE, 1 FAIL**. Exact clean source `6bd95ba07adc95ac192bc235e0b21a9a3ebfd443` and environment are embedded in results.json. This checkpoint is not accepted.

This includes CameraCaptureWorker nonblocking stop and actual UI caller wiring, plus native ownership review. The single failure is `tests/smoke_test.py::test_hero_native_video_isolation_and_authoritative_lock`: the NativeOnlyBackend test double lacks `field_log`, now required by the real typed camera stage used by draw_video. Verify the real backend contract and correct the double while retaining video isolation assertions; do not add a silent production fallback to mask an incomplete test double.

The later independent camera Qt-heartbeat tests and recovery-button mouse-anchor correction are not in this tested source. Firmware/reference/native Windows GPU/device qualification gaps remain. Repeat with the same offline guards and verifier command documented in wave9.
