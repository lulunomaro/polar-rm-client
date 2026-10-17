# Deterministic video-source retry ordering

Integration source 666c040; failing aggregate frozen source 93aa839.
Original log: clean-20261017-stage2/logs/tests__test_backend_video_source_nonblocking.stderr.log.

The failing test waited for the native decoder closer, but not the independent
UDP/HEVC closer, before requesting retry. Whether retry succeeded depended on
thread scheduling. The production rule correctly rejects retry while any closer
is in flight. Changing it would weaken native-owner semantics.

Red commit 539509a makes the original ordering deterministic by holding socket
close behind an event. It reproduces the identical `Video-source cleanup is
still in flight` error; red.log records the exact run.

The corrected test keeps every existing assertion and adds proof that:

- the native failure and UDP in-flight states are simultaneously visible;
- retry is unavailable and explicitly rejected during that overlap;
- rejected retry changes neither native/UDP worker identity nor request generation;
- UDP completes only after the barrier is released;
- native failure remains retained and retryable after that independent drain;
- the explicit retry still clears the native failure without refresh replay.

No production code, safety gate, timeout, skip or quality baseline changed.

## Verification

Shared hash-pinned Python; `PYTHONPATH=. QT_QPA_PLATFORM=offscreen CI=true`.

- Corrected case repeated 50 times: 50 pass.
- Backend video-source nonblocking: 14 pass.
- Video-source retry admission: 2 pass.
- Video-source snapshot: 5 pass; snapshot evidence: 3 pass.
- Video close ownership: 8 pass; UDP stop nonblocking: 3 pass.
- `git diff --check`: pass.

These are Python lifecycle tests with mocked native owners. Actual codecs,
physical streams and hardware remain outside this evidence. Full aggregate and
independent review belong to integration.
