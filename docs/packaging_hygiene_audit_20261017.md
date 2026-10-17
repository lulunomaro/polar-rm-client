# Packaging/output audit, 2026-10-17

Audited immutable source `dcd523cd5460b42d26aa9291452416eb6667f75e` in a
separate detached worktree; later integration edits require their own check.
Linux x86_64, CPython 3.12.14, hash-pinned verification environment.

## Tracked output inventory

`git ls-files -z` lists 3,908 files. `git ls-files -ci --exclude-standard -z`
finds 2,341 already-tracked files matching ignore rules, all under
`docs/evidence/`. These are retained historical verification logs; matching the
broad `*.log` ignore rule does not make them disposable. `CONTRIBUTING.md`
explicitly distinguishes retained evidence, fixtures and baselines from scratch.
This audit did not individually authenticate every historical log.

No tracked path matched these temporary-directory component patterns:
`tmp`, `tmp_*`, `temp`, `.tmp`, `_tmp_*`. No further candidate outside
`docs/evidence/`, `reports/`, `tests/fixtures/` or `tests/baselines/` had a
`.log`, `.tmp`, `.temp` or `.pyc` suffix, or a `logs`, `user_data`,
`field_evidence`, `__pycache__`, `.venv`, `build` or `dist` component.
This is a finite filename inventory, not proof that arbitrary filenames cannot
contain accidental output, private data or obsolete evidence. Nothing was
deleted, untracked or rewritten. Root `.venv-verification` ignore coverage is
addressed separately in `verification_output_hygiene_20261017.md`.

## Safe source-entrypoint preflight

README's normal `main.py` launch enters the GUI and may activate selected
services. Instead this audit uses that entrypoint's existing `--preflight` branch:
no GUI main window, selftest, codec test, serial connection or motion flags.
It only checks a UDP bind/release, protocol data, temporary directory writes,
FFmpeg availability and a Qt offscreen GL context. It does not transmit UDP.

From the detached checkout, set `PYTHON` to the prepared pinned interpreter and
`AUDIT_ROOT` to a new absolute directory outside all source/user-data trees:

```sh
mkdir -p "$AUDIT_ROOT"/{cache,config,data}
env -u POLAR_APP_ROOT -u POLAR_STARTUP_USER_DATA_ROOT -u POLAR_FFMPEG \
  QT_QPA_PLATFORM=offscreen CI=true POLAR_AUTOSTART=0 \
  POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1 \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
  POLAR_USER_DATA_ROOT="$AUDIT_ROOT/runtime" \
  XDG_CACHE_HOME="$AUDIT_ROOT/cache" XDG_CONFIG_HOME="$AUDIT_ROOT/config" \
  XDG_DATA_HOME="$AUDIT_ROOT/data" \
  "$PYTHON" main.py --preflight --preflight-output "$AUDIT_ROOT/preflight.json" \
  >"$AUDIT_ROOT/preflight.stdout.log" 2>"$AUDIT_ROOT/preflight.stderr.log"
```

Observed exit **2**, with eight checks passing and two failing:

- FFmpeg: checkout `bin/ffmpeg.exe` absent, no explicit source override supplied.
- Qt OpenGL: context could not be made current; reported requested OpenGL 3.3
  CoreProfile and a valid offscreen surface. Surface validity is not GL success.

Protocol specification, key-use JSON, all four writable destinations, source
manifest allowance and UDP port availability passed. With the owned XDG cache,
stderr was empty. An earlier run without the owned cache also emitted Fontconfig
cache warnings; both raw attempts were retained separately outside the checkout.

Tracked file SHA-256 inventories before/after the first run were identical;
Git status remained clean after both. The runtime tree contained no files, so
no default or developer config was seeded/overwritten. No production or test
assertions were changed to make this preflight green.

## Remaining scope

This clean source checkout does not provide the external FFmpeg binary, and
this host does not establish a usable native desktop GL context. Supplying a
manifest-matched Windows FFmpeg and native desktop remains a separate release
procedure. The source launcher can start without a video decoder; this stricter
preflight failure does not prove ordinary source UI launch fails. Actual Windows
PowerShell path handling, full freezing, clean-machine portable startup, native
framebuffer evidence and devices remain unverified here.
