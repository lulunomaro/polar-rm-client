# Windows native acceptance handoff

**Status: NOT EXECUTED HERE.** Prepared 2026-10-17 UTC for the next Windows
acceptance session. Linux real-Qt **offscreen** tests verify logic, event routing
and selected raster output; they do not certify Windows DWM, GPU presentation,
physical audio, first-use latency or clean-machine packaging. No native defect is
claimed fixed by this checklist. Match readiness remains **NOT READY FOR REAL MATCH**.

## Identity and prerequisites

- Public source snapshot: `007a0a24504579bdfafc2ba95bb82f57c7be3ce5`, branch
  `refactor/client-quality-20261016` in `lulunomaro/polar-rm-client`. Its tree
  `eff914a1f7d933ef70920040836eea74457b04ff` matches local verification source
  `5cf2ab3c06deceef6e2febd7bc310cda652c3d8f`. Use the public SHA for Git checkout
  and the build GitRef. If testing a later revision, record its
  exact commit instead; do not attribute that run to this snapshot.
- Record Windows version, GPU/driver, monitor resolution/refresh/DPI scaling,
  audio device, Python/Qt versions, renderer selection, application configuration
  and optional resource-pack identity. Test the user's original configuration as
  well as a fresh isolated profile, without overwriting their saved settings.
- Native desktop/OpenGL and a working audio output are required. Do not run the
  visual session with `QT_QPA_PLATFORM=offscreen` or infer native success from a
  VM/software-only substitute. Record environment overrides, especially
  `POLAR_APP_ROOT`, `POLAR_USER_DATA_ROOT` and `POLAR_FFMPEG`.
- Use MOCK/locked operation with robot, serial and external control transports
  disconnected. Use built-in local previews; no live purchase, match arming or
  physical control is part of this handoff.
- Build prerequisites are separate: Windows PowerShell 5.1, Git, Python 3.10+
  (README example uses 3.12), complete `requirements.txt` and
  `requirements-build.txt` in the selected interpreter, usable desktop/OpenGL,
  and the exact manifest-matched FFmpeg and two wallpaper files. Core runtime
  dependencies alone do not prepare a release builder. Missing assets or tools
  mean BLOCKED, not permission to substitute bytes or weaken checks.

## Existing commands (repository root)

These are the supported README entry points, not a new installation/CI service.
Run dependency preparation only on the authorized Windows build machine.
For an already prepared source environment:

```powershell
.\.venv\Scripts\python.exe main.py
```

The source `启动.bat` may provision `.venv-polar`; that behavior is different
from a clean portable-runtime test. Existing offline verification command:

```text
python tools/verify_all.py --profile offline --json tmp_quality/verification.json --log-dir tmp_quality/logs
```

Here `python` must resolve to the prepared interpreter. Keep its full verdict,
including FAIL/INCOMPLETE; this command is not native visual acceptance.
The existing release recipe is:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\tools\build_release.ps1 `
  -PythonPath '.\.venv\Scripts\python.exe' `
  -GitRef <explicit-commit> `
  -FfmpegPath '<local-ffmpeg.exe>' `
  -TexasWallpaperPath '<local-texas.mp4>' `
  -VendingWallpaperPath '<local-vending-machine.mp4>'
```

Replace placeholders with the recorded immutable commit and existing local
inputs. Omit `-UseWorkingTree` for acceptance. The builder installs nothing,
uses the selected interpreter, checks input hashes, runs its registered gates,
requires desktop GL, freezes and runs portable preflight. Preserve failures;
do not remove gates to obtain a ZIP. Do not add a private CS2 pack to public
artifacts without redistribution rights.

## Native observation matrix

Record each case as PASS, FAIL or BLOCKED, with reproduction steps and evidence.
Repeat cold starts and interrupted transitions; one successful click is not a pass.

1. **GPU/fullscreen presentation:** from startup, HUD, Escape menu and settings,
   toggle F11 repeatedly, resize, minimize/restore and switch focus away/back.
   Capture a screen recording showing transitions and steady-state frames.
   Pass: no sustained black/white flash, stale scene, missing overlay, clipped
   controls or lost input; resolution/fullscreen state agrees with the displayed
   setting. Record exact trigger/frame and renderer diagnostics for any flicker.
   Do not claim a frame-rate target from draw duration alone.
2. **First Escape and warmup:** compare first Esc immediately after a fresh launch,
   first Esc after atlas warmup, later Esc, and first/settings-tab opens. Repeat
   with a hide/restore during warmup. Pass: input remains responsive, requested
   menus appear correctly and warmup resumes without stale/duplicate work.
   Preserve `ui_tick_timing`, `frame_timing`, wakeup-gap and warmup maxima, and
   compositor dynamic/blur timings separately. Report observed latency; the
   3 ms warmup scheduling target is not a hard per-upload or native acceptance SLA.
3. **Repeated kill sound:** in `EliminationFX`, enable sound at a comfortable
   recorded volume; use `Preview FX` with `Normal 1-6`, `All Kill Types` and
   `Single`, including Card and another preset. Repeat after the first decode,
   change preset, mute/unmute and close/reopen the panel. Pass: the appropriate
   available event sound is audible repeatedly, without duplicate playback or
   delayed stale bursts; mute stays silent. Preserve decoder/cache/audio-error
   logs and a recording that actually captures the chosen audio output. A GUI
   animation or non-silent decoded WAV alone does not establish speaker playback.
4. **Scrolling, crosshair and input ownership:** scroll long preset lists to the
   last entry by wheel and touchpad, then close immediately and scroll over the
   old panel location. Reopen: no hidden scroll change; visible scrolling stays
   bounded and does not reach underlying settings/control. Test crosshair default
   and large scales at 720p and the target monitor resolution/DPI, editor preview
   zoom and fullscreen: no unintended disappearance/clipping; intentionally zero
   size/transparent profiles remain valid. Preserve screenshots and profile values.
   For MOCK economy holds, try Return vs keypad Enter and left/middle/right
   buttons: a different source release must not confirm; the initiating release
   after the hold threshold confirms once. Repeat after Escape open/close, focus
   loss and control lock: the old hold cannot resume; a fresh gesture is required.
   Test repeated Cancel/switch between settings tabs and GUI reopening: drafts and
   hit targets agree with the visible controls. Record mock transaction history;
   no physical purchase or control output is evidence required here.

## Portable clean-machine check

Use a separate clean Windows machine/account without Python, venv, pip or PATH
FFmpeg, and with no inherited development root overrides. Extract the complete
one-folder ZIP to a writable location, including a path with spaces. Launch
**`START-POLAR-RM.bat`**, the generated portable launcher (not the source launcher).
No runtime download/install should be needed. Preserve
`user_data/logs/startup-preflight.json`; all preflight checks must pass before GUI
entry. Repeat the native matrix on the portable build, close and relaunch, and
verify user settings persist while `defaults/` and packaged resources stay intact.

Keep the ZIP hash, `BUILD_MANIFEST.json`/`.sha256`, its `git_commit`, dependencies,
critical-file hashes, build logs and portable preflight stdout/stderr/report.
Manifest `tests.clean_windows = FIELD VALIDATION REQUIRED` is a pending gate,
not evidence of a clean-machine run. Report build preflight and independent clean
machine acceptance separately. Any missing DLL/plugin/codec, startup failure,
wrong runtime root or dependency on the developer machine is FAIL/BLOCKED.

## Evidence return

For every row provide machine/commit/package identity, configuration, cold/warm
state, exact sequence, expected/observed result, recording/screenshot timestamps
and logs. Keep unrun rows explicitly BLOCKED/NOT RUN. Native UI acceptance does
not replace the firmware, official-link, physical emergency-stop, soak and dual
sign-off requirements in `FIELD_CANDIDATE.md`.

Sources: `README.md` (launch/verification/release), `tools/build_release.ps1`
(actual generated launcher and manifest), `FIELD_CANDIDATE.md`,
`docs/build_interpreter_20261017.md`, `docs/render_performance_hardening.md`,
`docs/settings_cold_start.md`, `docs/settings_crosshair_review.md`,
`docs/kill_effect_reliability.md`, and the ownership/wheel regression tests.
