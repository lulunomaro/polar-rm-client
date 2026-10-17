# Reproduce focused final audit

Use the hash-pinned verification environment at source `ade481f`, with
`QT_QPA_PLATFORM=offscreen` for API and runtime tests:

```sh
python tools/quality_gate.py
python tests/api_freeze_test.py --mutation-selftest
python tests/test_quality_gates.py
python tests/test_quality_ratchet_baseline.py
```

The nine-suite selection (49 methods) is:

- test_audio_write_admission
- test_configuration_quiescence
- test_economy_hold_duration_alignment
- test_mjpeg_frame_delivery
- test_polar_health
- test_polar_health_characterization
- test_preference_file_descriptor
- test_referee_frontend_projection_contract
- test_window_configuration_quiescence

Load these through unittest with `tests` on sys.path. Discovery uses
`tools.test_registry.discover(Path.cwd())`, compared against added files from
`git diff --diff-filter=A --name-only <base> HEAD -- tests tools`.
`discovery.log.gz` lists suites, fixture exclusions, and unchanged file hashes.
`summary.json` is a selected subset of the real static report, not new metrics.
All raw logs are gzip-compressed, byte-preserved; all commands exited zero.
No global aggregate runner or lock was used.
