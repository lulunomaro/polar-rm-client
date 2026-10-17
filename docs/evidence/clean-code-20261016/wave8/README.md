# Stage-eight integrated checkpoint

Completed 2026-10-16 07:10 UTC. **199 PASS, 12 SKIP, 4 INCOMPLETE, 1 FAIL**. Exact tested clean source `702f340` and environment are embedded in results.json. This aggregate is not accepted.

The failure is the source-evidence check for four protected private window seams after telemetry ownership moved: `_field_frame_times_ms`, `_last_field_frame_log_at`, `_last_present_time`, `_ui_tick_stages`. Existing production compatibility aliases still exist. Independent analysis must distinguish obsolete caller evidence from intentionally retained contract entries; do not fabricate consumers, regenerate the historical API snapshot, or lower the general evidence requirement just to turn the suite green.

This checkpoint includes the earlier backend video ownership and thread-admission fixes; it excludes later source-snapshot/HUD retry, specialized GL ownership, and local-vision nonblocking changes. Firmware/reference/native Windows GPU/device limitations remain. Reproduce with the same offline guards and shared `tools/verify_all.py --profile offline` command as prior stages.
