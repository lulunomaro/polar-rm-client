# Stage-ten targeted recheck

Completed 2026-10-16 07:53 UTC, exact clean source `66cbe93` (full identity in results.json): **3 PASS, 0 SKIP, 0 INCOMPLETE, 0 FAIL**.

The actual smoke fixture now carries the original manager's field_log into NativeOnlyBackend; production error handling and all video-isolation assertions remain. The complete smoke script passes (71 explicit assertions). Two independent real-window/camera-worker heartbeat tests pass, with cv2 native boundaries controlled; nine actual-window recovery tests pass, including owned UI drag not replaying as combat motion after release.

This is selected-suite evidence only, not a full aggregate or native hardware qualification. It corrects the stage-ten smoke failure; retain its failed original report. Run the wave9 verifier command with `--test smoke_test --test test_camera_window_heartbeat --test test_window_video_recovery` to select the same checks.
