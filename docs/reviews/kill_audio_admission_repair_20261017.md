# Kill-audio decode admission repair — 2026-10-17

## Result and scope

The focused repair prevents executor-construction/submission exceptions from
leaving a cue permanently marked as converting. It selects per-request Qt media
fallback while retaining ownership of decoder work whose submission outcome is
uncertain. Reentrant stop/close cannot replay retired requests.

This establishes a reproducible admission-failure mechanism, **not the physical
root cause of a reported “only the first cue plays” symptom**. Playback in these
tests is an owner-thread recorder; neither audible output nor a physical audio
device was validated. The subsequent full offline regression completed with
345 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL. Its overall result remains **NO-GO**
(`passed=false`); zero failures do not turn skipped/incomplete checks into acceptance.

## Frozen checkpoint

- Local commit: `bd7115ffdcab11c5771fac362aabe97f960b22bd`.
- Public commit supplied by parent verification:
  [`d8a90c52d3a52b390c1a014750191dc5b760a3b7`](https://github.com/lulunomaro/polar-rm-client/commit/d8a90c52d3a52b390c1a014750191dc5b760a3b7).
- Git tree, independently checked locally:
  `963e1daf39b0dfad7811184c8f09e69bb521ef16`.
- Parent commit: `15e472b4fe57910d397eedf655822ed4f1ea7865`.

Reviewed file SHA-256 values:

| File | SHA-256 |
|---|---|
| `rm_client/kill_audio.py` | `a52c5fd3c4ae53d25d2f2ad1d7eafebab4880967e78978634de011382ef63292` |
| `tests/test_kill_audio_executor_admission.py` | `0e4205df3a3344a8a7eb082523e9e202c3025c05e581a31eca562328a7a625ee` |
| `tests/test_kill_audio_submission_fallback.py` | `db25ebf4883dd5c225679ebc0cbc119f93dcdf32fa21cfc9a6c9e28546b5eea2` |

## Failure mechanism and correction

In [`KillSoundPool._queue_decode`](../../rm_client/kill_audio.py), a cue enters
`_converting` before executor submission. Previously, a submit exception could
escape without clearing that marker. Later requests then joined a conversion
with no returned Future/callback to finish it, instead of reaching fallback.

The correction handles construction/submission failure through the existing
`_converted(name, None, error)` path: clear the converting marker, mark that cue
as failed, report the error, and drain only fresh queued requests through media
playback. Later requests for that failed cue use media rather than retrying its
decode. Other cues can still use the retained executor.

A submission exception is not proof that work never started. Tests exercise
real executor queues and threads where `Thread.start` fails before starting,
after starting, or after decode completion. The pool retains its executor and
independent active-decode ownership; an unreturned Future's result does not
become a second playback or install a decoded result for the failed cue.
`close` seals admission and cancels unstarted work; `wait_closed` separately
waits for executor/activity retirement. Fallback success does not claim that
uncertain work has already retired.

Two additional boundaries are necessary:

- `_decode_admitting` prevents a different cue reentering during executor
  construction/submission from replacing the owner or nesting a submit while
  its non-reentrant lock may be held. That request can use media fallback.
  Same-cue reentry can join the pending request list.
- `_playback_epoch` changes on stop. `play` checks the epoch after admission;
  `_converted` checks it between queued fallback requests. A reentrant stop or
  close therefore cancels the remaining old requests. Close during constructor
  return also seals the newly returned idle executor before any submission.

Existing bounded pending age/count, mute/output-device checks, and the media
backend's asynchronous-error reporting remain relevant. A successful recorder
call does not prove the platform media backend will emit sound.

## Completed full offline regression

The completed results were read directly from the stage's `results.json`:
run `ab7ab426bcb748dbad8963dbcd4a197b`, started 2026-10-17 20:19:36 UTC,
`run_state=completed`, `passed=false`, **345 PASS / 12 SKIP / 4 INCOMPLETE /
0 FAIL**. This remains NO-GO for complete acceptance and provides no physical
audio or robot qualification.

Provenance records the local commit and tree above. The declared verification
inputs have identical start/end fingerprints: **694 files**, SHA-256
`26a50e55eb79e0993e7bbca35cab4dd525fe8ec82958e28d351be55f4e684498`.
The source-stability check reports PASS. Its declared scope covers selected
source/configuration files under `rm_client`, `tests`, `tools`, `configs`, plus
root entry/requirements files; it excludes documentation, assets, external
fixtures and installed packages. Start/end equality does not detect transient
edits restored between captures. Documentation added during the run therefore
does not alter this fingerprint's defined scope.

## Verification evidence and limitations

The independent focused log was inspected: **56 tests passed**. The parent also
reported the independent baseline comparison as **2 failures / 7 errors**, and
a guard-only mutation as **2 failures**. These are separate verification runs,
not counts of physical playback failures or new test methods.

The new [submission-fallback suite](../../tests/test_kill_audio_submission_fallback.py)
contains nine methods covering constructor rejection, same/different-cue
reentry, submit rejection, fresh versus expired pending requests, real queued
and running work after late start errors, and reentrant close/stop. Its cache is
inert and playback is replaced with a recorder that asserts owner-thread use.
The existing [executor-admission test](../../tests/test_kill_audio_executor_admission.py)
now expects bounded fallback rather than an escaping submit error, while
retaining its worker-lifetime assertions. Focused coverage also includes
existing audio replay/cue-selection behavior.

This report adds documentation only. No source/test changes or duplicate full
run were made while the completed full regression was active. No raw logs or
private audio assets are published with this report. Native audio-device
availability, actual repeated audible playback, and the original user's
physical symptom still require their own authorized acceptance evidence.
