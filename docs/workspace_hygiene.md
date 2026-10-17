# Workspace hygiene

This policy separates maintained source and evidence from disposable local output.

## What belongs in Git

Keep application source, build/test entry points, default configuration, shipped assets,
test fixtures and visual baselines. Retain reviewed reports and their raw evidence
under `docs/` and `reports/`; a text file is not disposable merely because it is a log.
The three `tools/firmware/armcc_check/uvision_build_log_*.txt` files are explicitly
referenced historical build evidence and remain tracked.

## Where new scratch output belongs

Use a root `tmp/` directory for disposable experiments. Runtime logs belong in
`logs/`; build products in `build/` or `dist/`. These locations are ignored.
Root `tmp*/` directories also cover automatically generated names such as
`tmpe_5m1knd/`. Root `tmp_*` and `_tmp_*` files and `*.tmp` / `*.temp` files
are ignored. Intentional fixtures and baseline directories are not blanket-ignored.

For GPU smoke captures, set `POLAR_GPU_SMOKE_ARTIFACT` to a path in an existing
scratch directory, for example `tmp/gpu-smoke.png`. The smoke script already accepts
this optional destination. The obsolete tracked `tests/_gpu_renderer_runtime.png`
is removed; `tests/baselines/` remains unchanged.

## Inspect a cluttered local checkout before removing anything

Run these read-only commands from the repository root:

```sh
git branch --show-current
git status --short
git ls-files
git ls-files --others --exclude-standard
git ls-files --others --ignored --exclude-standard
```

Ignored does not mean safe to delete. This repository intentionally ignores local
calibrations, imported wallpapers/audio, virtual environments, user configuration and
field evidence. Do not run blanket `git clean -fdx` or `git clean -fdX`.

Move only individually reviewed disposable files to an archive outside the repository
first. Check that the client, required configuration and tests still work before
discarding that archive. Preserve uncommitted source and unique real-machine evidence.
Changing `.gitignore` does not erase existing local files or untrack previously
committed files.

## Scope of the 2026-10-16 cleanup

Reviewed main `9599a1c5fd8742fd5dd53c1b57fec1effd4827bd`: 929 tree entries,
untruncated. No tracked root tmp directory, ordinary .log files or Python caches were
found. The single generated screenshot is removed and ignore gaps are closed.
Historical reports, firmware logs, models, shipped media, fixtures and configuration
are preserved. No history rewrite or branch merge is performed.

A separate read-only branch scan found
`tmpe_5m1knd/polar-field.jsonl` on `perf/modular-rust`
(`ba88e426358ed31edc1269773b61547b81bfa655`).
That branch and log are not changed by this main-targeted PR. A user's local checkout
may contain many additional untracked or ignored files; it was not inspected or cleaned.

Validation: remote tree inspection and a read-only reference scan of 455 text files
found no reference to the removed screenshot. The GPU smoke producer writes only an
optional capture destination. Existing ignore rules and evidence documentation were reviewed. No local Git ignore execution or application
test run is claimed because the execution environment was unavailable.
