# Independent audio ownership review

Reviewed exact source: `9dd5a806739ed315ddfcba5fc7d2fe2feabdb99f` in a detached
review worktree. Baseline reproduction: `53e527edf87393e62c72c4aa824304607c49c090`
in a separate detached worktree. No production changes made during review.

Environment: Python 3.12.14 from `/workspace/shared/client-clean-venv/bin/python`,
`QT_QPA_PLATFORM=offscreen CI=true PYTHONPATH=.`; PySide6 installed in that pinned
verification environment. Run each command from the respective checkout root.

## Commands and observed results

- Corrected source: `python -m unittest discover -s tests -p 'test_audio*py'`
  returned 0: **47 tests passed** in 2.417 seconds. Complete output:
  `audio-tests.log`.
- Baseline: `python tools/diagnostics/probe_audio_factory_retention.py` returned
  **1**: `ui_pool_retained=True`, `kill_pool_retained=False`, `ui_effects=2`.
  Complete output: `red-probe.log`.
- Corrected source: the same standalone probe returned **0**:
  `ui_pool_retained=False`, `kill_pool_retained=False`, `ui_effects=0`.
  Complete output: `green-probe.log`.
- Additional direct control created a `ReliableAudioPool` with a temporary root
  and asserted `pool.parent() is None`. Source inspection confirmed the global
  audio registry uses `weakref.WeakSet`.

## Independent assessment

No blocking findings. The successful factory-drop test needs no exception and
therefore excludes an exception traceback as the retention explanation. The
failed-factory helper returns before collection, and the successful-window path
closes/deletes the window before collection. Parentless pools and the weak global
registry rule out intended Qt/application parent retention.

All three production signal connection sites now use weak pool and voice
references. The tests preserve current-handler lookup, original voice/status/error
forwarding while alive, explicit disconnection, and no-op callbacks after native
wrapper deletion or Python ownership loss. Actual preloaded-media and fallback
connection sites use real Qt parent/signal objects; source/playback operations are
mocked. Test mock call history is explicitly cleared before collection so it
cannot manufacture an unrelated strong reference to a player.

The replay AST loader includes the two production listener helpers and retains
existing playback assertions. There is no new exception swallowing, forced
full-window shutdown on partial assembly, or playback policy change in the fix.

These results establish native QObject lifetime behavior in offscreen tests.
Expected fontconfig, unavailable-audio-device and offscreen warnings appear in
logs. They do **not** qualify physical sound-device cleanup, audible output,
platform codec teardown or native visual behavior. Aggregate verification remains
owned by the integration stage.
