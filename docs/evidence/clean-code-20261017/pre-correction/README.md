# Preserved rejected integration candidate

Exact tested local commit: `dcd523cd5460b42d26aa9291452416eb6667f75e`.
Tree: `428122bbdc342ca1651afe44c185fd5b598c3878`.

The complete offline profile finished with **298 PASS, 12 SKIP, 3 INCOMPLETE,
1 FAIL**. Declared 641 input files had identical start/end SHA256
`a855249d90766cb30a72b8006a2dcd95add76c73735eea538a65e375bb0f1abe`.
This is a rejected, pre-correction candidate, not current acceptance.

`test_ui_state_regressions` failed because its synthetic lifecycle stand-in
lacked the extracted finalization owner. The later fixture correction uses the
real owner and preserves the original assertions. Independent review also found
callback lookup and live hover read ordering differences not covered by this
profile's then-current cases. Those have separately preserved red tests and
corrections; passing suites here cannot certify the corrected code.

Command, with prepared pinned CPython 3.12 verification environment and fixed
CRC reference (`f46cce6382bbbae2056b81538c1de5c06a93c2199b4d07784d9bc551509ed2f5`):

```sh
CI=true QT_QPA_PLATFORM=offscreen POLAR_TEAM_CRC_REFERENCE="$CRC_REFERENCE" \
  "$VERIFICATION_PYTHON" tools/verify_all.py --profile offline --timeout 300 \
  --json "$OUTPUT/results.json" --log-dir "$OUTPUT/logs"
```

`raw-records.tar.gz` preserves the original runner, result JSON, and per-suite
stdout/stderr without normalization. `raw-manifest.json` lists every member's
size and SHA256; `SHA256SUMS` covers published artifacts. Only generated local
verification output is included. Native Windows/GPU/device checks were not run.
