# Indirect pure-domain guard evidence

Production base: `fa2c2b8397295d27ada5d105854b751b7dd0940d`.
Test-first commit: `afbdb95` (46 methods, 38 deliberately failing assertions).
The final candidate adds one CLI negative-control method (47 methods).

Red command (system Python, test-first source and original gate):

```sh
python -m unittest discover -s tests -p test_quality_gates.py
```

Green commands (parent-provisioned verification environment):

```sh
QT_QPA_PLATFORM=offscreen /workspace/shared/client-clean-venv/bin/python tests/test_quality_gates.py
QT_QPA_PLATFORM=offscreen /workspace/shared/client-clean-venv/bin/python tests/test_quality_ratchet_baseline.py
/workspace/shared/client-clean-venv/bin/python tools/quality_gate.py
```

Each green command exited zero. These are focused static tooling checks, not
an aggregate, runtime purity proof, hardware qualification or packaging check.
No production source or quality baseline was edited by this lane.
