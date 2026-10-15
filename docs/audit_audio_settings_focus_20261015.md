# Audio, settings, focus and fullscreen interaction audit

## Newly reproduced defects

On the PR13–PR16 composed tree (`fdc665272d205edf26785e454760027a61c70d3c`), cold kill requests can survive a mute interval and play after unmuting:

1. A kill requested with master volume zero still enters the asynchronous decode queue. Unmuting before completion releases the old cue.
2. A normal cold kill, followed by settings Apply with master zero and then Apply with master 100, survives both changes.
3. A kill requested while `mute_unfocused` applies also enters that queue. Restoring focus before decode completion releases the old cue.
4. An active cold kill survives focus loss/restore despite the background-mute setting.

Each case produced one real Qt/FFmpeg playback and 220,614 nonzero decoded PCM bytes on the composed baseline. The native CS2 route gates its request immediately and does not have this difference in mute behavior.

A related lifetime defect is independently covered: `KillSoundPool.close()` only cleared its worker queue. It did not clear the inherited WAV-ready queue or stop running players, and later `play()` could fall back into playback even after close.

## Fix

- Check master/focus policy before a cold request enters the decode queue. Preserve the unscaled request volume so master gain is applied exactly once at playback.
- Centralize stop/cancel behavior in `ReliableAudioPool.stop()` and extend it in `KillSoundPool` to clear the cold-request queue as well.
- Focus mute and preference Apply stop all paths and invalidate queued cues. In-flight cache preparation may finish without reviving canceled requests.
- Closing a pool stops playback, clears both queue layers and rejects later requests.
- Focus changes with background mute disabled preserve legitimate queued playback.

## Verification

- Before fix: 5 failures in 22 deterministic Qt queue/policy/lifetime checks.
- After fix: all 22 checks pass.
- Audio replay: 17 pass.
- Settings center: 12 pass.
- Cold settings build: 13 pass.
- Startup/settings/fullscreen: 19 pass.
- Total focused regression coverage: 83 tests pass; compile and whitespace checks pass.
- Real decoder after fix: all four stale-cue cases produce zero play calls and zero PCM.
- Native CS2 control: four normal unmuted cues decode at output gain 0.33; muted requests remain silent.
- Production preview → accept-combat-event → renderer → audio pool: two six-kill sequences deliver 12/12 primary cues while settings closes and F11 switches fullscreen/windowed twice. The real Qt fullscreen states are `[true, false, true, false]`.
- Closing a warm pool stops its real QMediaPlayer and rejects a later request.

Machine-readable before/after reports and focused test logs are under `docs/evidence/audio-policy-20261015/`.

## Reproduction

Install the project's supported Qt dependency and provide the original bundled audio assets. The audit script accepts explicit fixtures because the investigation checkout is partial; missing fixture assets are not treated as repository defects.

```bash
QT_QPA_PLATFORM=offscreen python tests/test_kill_audio_regressions.py
QT_QPA_PLATFORM=offscreen python tools/probe_audio_policy.py \
  --root . \
  --kill-cue assets/kill_fx/gd656/sounds/killsound_df.ogg \
  --cs2-pack assets/cs2_ui_pack \
  --output /tmp/polar-audio-policy/after.json
```

For the unmodified composed baseline, use the same probe with `--expect-stale`; it expects and records the known stale-cue reproductions. The reports include source and fixture hashes.

## Limits

This is Linux Qt 6.8.2 offscreen and decoded-PCM evidence, not physical speaker, Windows audio-device routing or OpenGL/compositor evidence. `QMediaDevices.audioOutputs()` is empty in this environment. The probe substitutes only the kill pool's no-device early-return guard so the real Qt decoder can run; Qt itself still sees no output device. Focus-policy cases control the reported application state and emit the real Qt state-change signal, rather than claiming OS-level focus coverage. A worker event deliberately holds genuine FFmpeg cache completion to reproduce the race before the existing two-second request expiry. Repeated-preview coverage disables the optional locked-target bonus because only the unchanged primary cue fixture is supplied. It does not assert complete client rendering or external combat-event ingestion.
