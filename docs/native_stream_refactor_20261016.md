# Native custom-block continuity boundary

## Scope and ownership

`native_viewer_telemetry.py` already owns strict, Qt-free decoding of PDL1
telemetry and the fixed 300-byte 0x0310 video packet. This stage leaves those
parsers, their wire values, and the Annex-B codec recovery implementation intact.
Adding a second parser would not improve the boundary.

The new `backend/native_stream_policy.py` owns two pure continuity decisions:

- Whether a producer sequence restart or an explicit localization-reset status
  invalidates the operator's existing localization trust.
- Whether a decoded video sequence starts a new epoch, continues it, represents
  a forward gap, or is a duplicate/late chunk. The result is an immutable value
  containing only sequence, reset, missing-count, and payload-admission values.

`BackendManager._apply_custom_byte_block` keeps byte conversion, receipt metadata,
strict parsing, and dispatch. Its two adapters publish telemetry or apply video
continuity effects. They retain ownership of robot state, recovery, and decoder
calls. No service receives a manager or a general-purpose mutable state bag.

The video adapter intentionally remains explicit. It checks decoder availability
at the original live decision points, rather than treating a concurrently changing
process as one immutable snapshot. It retains the existing reset-before-feed,
recovery-before-write, pending-restart suppression, bounded write-retry window,
and diagnostics-after-effects order. The source/deploy gate still short-circuits
invalid deploy values when an explicitly selected native source is sufficient.

Telemetry publication retains the existing sequence: native fields, localization
revocation, then diagnostics. Valid telemetry never re-arms a revoked operator
state. Natural uint32 wrap remains distinct from a short backward restart.
Malformed records do not refresh telemetry; the separate freshness/readiness
checks continue to govern stale telemetry. This stage adds no actuation path.

## Frozen evidence before production changes

`tests/fixtures/custom_byte_block_baseline.json` was recorded before extraction.
It pins the exact pre-extraction manager method, parser module, and recovery
class by SHA-256. Explicit `--record-baseline` refuses mismatched sources; ordinary
tests only read the fixture. The fixture is not regenerated after extraction.

Its 96 cases include valid and malformed packets, all six reset markers, natural
wrap and backward jumps, already-revoked trust, selected/unselected sources,
deploy conversion failures, byte-sequence wrap/gaps/duplicates, split Annex-B
recovery, decoder availability changes, failed starts, pending restarts, bounded
write retries, and injected exceptions. Exact packet bytes, ordered state writes,
ordered recovery/decoder calls, and partial state after aborts are compared.
Shared initial values and explicit per-case deltas avoid duplicating unchanged
state while retaining complete observations. These are fake I/O tests, not decoder
or hardware qualification.

The pure policy tests additionally check every one of the 65,536 possible previous
and current byte-sequence pairs against an independently expressed forward-advance
reference. They cover the half-ring boundary, reset/first-packet behavior, existing
normalization, immutable decisions, and imports with Qt/state/decoder/parser layers
blocked.

## Focused verification

From the repository root, with an isolated user-data directory:

```sh
export QT_QPA_PLATFORM=offscreen
export POLAR_USER_DATA_ROOT=/tmp/client-native-stream-tests
export PYTHONPATH="$PWD:$PWD/tests"
python tests/test_custom_byte_block_characterization.py -v
python tests/test_native_stream_policy.py -v
python tests/test_serial_codec_regressions.py -v
python tests/test_decoder_lifecycle_quality.py -v
python - <<'PY'
import backend_protocol_test as protocol
import smoke_test as smoke
protocol.test_decoded_video_timestamp_and_source_selection()
smoke.test_native_viewer_telemetry_and_video_0310()
smoke.test_hero_lob_solver_and_fail_closed_readiness()
PY
python tools/quality_gate.py
```

Observed locally: 5 characterization methods / 96 frozen cases, 7 pure-policy
methods, 11 codec regression methods, 14 decoder lifecycle methods, all three
selected existing protocol/smoke functions, and the static quality gate pass.
The manager dispatch now contains 9 statements / 2 decisions; the telemetry
adapter 20 / 1; the video effect adapter 59 / 24. These measurements describe the
seams, not a line-count acceptance target. The important change is independently
testable continuity policy with unchanged ordered effects.
