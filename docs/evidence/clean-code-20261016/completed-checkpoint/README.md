# Completed integrated checkpoint

Exact tested source: 2d3a5a47f6c75def410aca7144f53e0522bb9f6b.
292 PASS / 12 SKIP / 3 INCOMPLETE / 0 FAIL. The 630 declared inputs retained SHA256 d082a63ab4e38e5b7a25bb784ae9e6a5f1cc00fe4aebed25a03e3c3fe04054d3 before/after. Every result is tied to its own source and scope; subsequent changes are documentation/evidence only.

The preceding ade481f production run reports 291 PASS / 12 SKIP / 3 INCOMPLETE / 0 FAIL. It still used a historical vacuous bridge cleanup check and must not be treated as corrected cleanup acceptance. Both original results remain intact. The corrected script requires server retirement and fake-board closure; no-op-close and live-worker negative probes fail. Ten focused normal runs each report 15 assertions. No verifier/classifier/baseline weakening was used.

raw-runs.tar.gz retains all generated UTF-8 records from both run directories, and raw-manifest.json records every member hash and length. Separate results files include provenance, dependencies, individual checks and source stability. Archive contents are only synthetic local test records, not user inputs, assets or physical-device recordings. Bounded content scanning is not an absolute secrecy guarantee.

Reproduction: use Python3.12 and requirements-verification-linux-cp312.txt; retrieve the CRC reference described in ../continued-integration/README.md. With CI=true, QT_QPA_PLATFORM=offscreen and POLAR_TEAM_CRC_REFERENCE set, run python tools/verify_all.py --profile offline --timeout 300 --json <outside-checkout>/results.json --log-dir <outside-checkout>/logs on the stated source. Local staged commit history is in the recovery bundle; public snapshot has identical code/tests plus evidence.

The selected profile remains NO-GO: missing firmware/reference/official dump prerequisites remain12SKIP/3INCOMPLETE. Native Windows/GPU/serial/audio/camera and robot acceptance are additional unexecuted gates. No FPS, crash-atomicity, whole-project Clean Code, hardware or release-readiness claim. PASS counts are registry checks, not a sum of repeated individual assertions.
