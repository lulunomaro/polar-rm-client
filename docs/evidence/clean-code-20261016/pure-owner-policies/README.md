# Root-specific pure-owner policies

Test-first checkpoint: `31463b2`. Original two root policies and all numeric,
lint and cycle allowances are unchanged.

`red.log.gz` preserves the 50-method test-first output, including 348 failing
mutation assertions. `green.log` records these three commands, each exit zero:

```sh
/workspace/shared/client-clean-venv/bin/python tests/test_quality_gates.py
/workspace/shared/client-clean-venv/bin/python tests/test_quality_ratchet_baseline.py
/workspace/shared/client-clean-venv/bin/python tools/quality_gate.py
```

`candidates.log` identifies each separately measured candidate revision before
running this candidate tool with `--root` pointed at that checkout. No measured
production source is imported. These checks are not an integrated suite.
