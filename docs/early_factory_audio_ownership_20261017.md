# Early factory acquisition audit and audio callback ownership

## Bounded acquisition ledger

This audit concerns the constructor before its fully assembled activation guard.
It does not grant partially constructed windows the full shutdown contract.

| Acquisition | Owner and admission | Failure/retirement observation |
| --- | --- | --- |
| Warmup QTimer | Qt child of the window; constructed inert | No worker admission in constructor |
| Camera, local vision, Tongji receiver/bridge | Explicit worker objects; start/configuration separate | Constructors initialize locks/events/state, not capture/socket workers |
| BackendManager transports/decoder/log | Backend owns inert transports and lazy logger | Configuration uses `start=False`; lifecycle activation is later |
| Feedback assets | Plain manager owns parentless audio-pool QObject; pool owns effects/players | Concrete native QObject retention defect found below |
| Wallpaper | Manager owns player, decoder and two-thread executor | Executor allocates lazily; no thumbnail jobs submitted by its constructor |
| Presentation assets/fonts | Presentation service bundle and window aliases | Local loading may fail; no worker-loop ownership identified in inspected constructors |
| GPU services | Window owns inert renderer objects | Allocation/shader admission remains initializeGL, not the assembly factory |

Audio is the only additional retained native owner confirmed by this bounded
investigation. This is not a proof that every possible earlier exception is safe.

## Reproduction and controls

Checkpoint `53e527e` has three failing lifetime tests. After feedback ownership is
lost, `ReliableAudioPool` and its QSoundEffect children remain valid native Qt
objects after ten GC, event processing and DeferredDelete rounds. The pool has no
Qt parent. The effect's native signal retained a lambda capturing both the pool
and effect strongly. The application audio registry is a WeakSet.

Controls exclude a preserved exception traceback or intended parent ownership:

- A successful factory result is dropped without raising; pool remains valid.
- A later window factory raises; the exception is caught in a helper that returns
  before collection, without storing the exception or traceback.
- A successfully constructed window is closed and explicitly deleted before GC;
  its feedback pool still remained valid at the baseline.
- A minimal standalone probe creates an 80-frame temporary PCM WAV; no bundled
  sound or physical output is required. Baseline: UI pool retained, kill pool
  collected, two QSoundEffects retained. Corrected: both pools collected.

The evidence is valid native QObject retention. It is **not** evidence of a live
physical sound-driver handle, a hardware leak, or an audible-output result.

## Narrow correction

The three status/error signal connection sites now use weak pool and voice
references. While alive, callbacks still resolve the current owner handler and
forward the original voice/status/message. There is no new exception swallowing.
After Python ownership ends or native wrappers are deleted, a retained callback
becomes a no-op. Qt child ownership and explicit disconnection remain unchanged.
This fixes both ordinary owner loss and later-factory failure without invoking
full-window shutdown on partially initialized services or changing playback policy.

The offline replay test AST loader includes the two production listener helpers,
with a native-validity stand-in for its existing non-Qt FakeObjects. Original
replay, cue selection and error assertions are unchanged; real Qt validity and
connection sites are independently tested in the new native QObject suites.

## Verification

Python 3.12.14 / PySide6 6.8.2; offscreen / CI; 80 focused unittest tests pass:

- `test_audio_pool_lifetime.py`: 5, including all three production connection
  sites, failed later factory and successful window close
- `test_audio_signal_listeners.py`: 5, live handler/status/error forwarding,
  explicit disconnect, native deletion and weak Python ownership
- `test_audio_replay_regressions.py`: 17
- `test_kill_audio_regressions.py`: 22
- `test_audio_configuration_transactions.py`: 9
- `test_audio_write_admission.py`: 11
- `test_window_activation_failure.py`: 5
- `test_window_construction.py`: 6
- `tools/diagnostics/probe_audio_factory_retention.py`: exit 0 after correction,
  exit 1 on the original strong-lambda implementation
- `git diff --check`: pass

The preloaded/fallback media lifetime tests use real QMediaPlayer/QAudioOutput
parent and signal ownership but replace source loading/play calls, so no codec or
physical playback is inferred. Integration owns the final aggregate run. No
visual/API baseline, playback behavior or safety gate was weakened.

Remaining scope: native codec/audio-device teardown on target platforms and
exhaustive service-factory failure coverage are unverified. A targeted native
playback/close validation stage is more informative than a blanket constructor
rollback; allow roughly one focused session per target audio backend.
