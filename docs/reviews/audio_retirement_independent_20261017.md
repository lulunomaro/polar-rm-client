# Independent kill-cue retirement review

Accepted source: `cc33702784662d0d10fbc06834ac98d3b289c8f4` plus
`f3677296f8f8e01294702be54a611d6da576c3ca`. Review checkout was detached at the latter.

The first candidate passed 69 tests but failed reviewer red `bf85c5f`: an actual
executor worker admitted by a late-raising Thread.start was absent from its
executor join set. Retirement returned while the inert decoder remained active.
The original red is retained, including its failing log; the author carried it
forward as `2aa1764`. No native codec or physical audio was used.

The correction seals decoder entry and counts active decodes under the same
condition. Thus an already admitted decode delays retirement, while a delayed
unregistered task cannot start decoder work after sealing. Counting drains in a
finally block even on decode failure. Shutdown failure also preserves the drain.
Qt playback remains owner-thread work; worker retirement performs no Qt calls.
An unregistered idle Python thread can finish asynchronously: this acceptance
certifies decoder activity, not an exhaustive executor thread registry.

Independent shared pinned Python, offscreen Qt, `PYTHONPATH=.:tests`:

- 71 tests passed across test_kill_audio_executor_admission,
  test_window_audio_retirement, test_kill_audio_regressions,
  test_window_activation_failure, test_window_async_lifecycle,
  test_window_constructor_failure, test_window_construction and
  test_audio_pool_lifetime.
- Additional probe injected executor shutdown failure while an active decoder
  was latched, then released it into a decoder exception. Retirement waited,
  preserved the shutdown exception, cleared the activity count, and allowed a
  sealed retry without new decoder entry.

Raw evidence is in `../evidence/clean-code-20261017/audio-retirement-independent`.
This fixes the reproduced reused-window audio owner omission and late admission
counterexample. It does not explain the historical 30-second activation timeout,
qualify audio devices/codecs, or replace the root's aggregate evidence.
