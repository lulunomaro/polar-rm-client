# Event extension validation — 2026-10-17

## Scope and checkpoint

The repair rejects malformed numeric/over-nested supported event extensions at
the parsing boundary so they cannot abort consumption of later raw messages.
It is not comprehensive event authentication, identity validation, or a protocol
rewrite.

- Local checkpoint: `2f95679285b5b16eb38c9c16dfcf5b5967c7d7d3`.
- Public counterpart supplied by the parent publication verification:
  [`e1fed5dc11e6c4d3b547178f36e0af93e5fa9dac`](https://github.com/lulunomaro/polar-rm-client/commit/e1fed5dc11e6c4d3b547178f36e0af93e5fa9dac).
- Reviewed `rm_client/combat_events.py` SHA-256:
  `25da52402f6a66ce51569e747c37e9f2f61b02943ac9350c80d2f3c50689aaa2`.
- Reviewed `tests/test_official_event_numeric_boundary.py` SHA-256:
  `c31e524af28acd6ec8f57df37fef6ed38dfd42056b7e1af0c08c757b468cf59a`.

This document was added during the subsequent frozen full regression. It does
not report that run's outcome and does not change its production/test inputs.

## Verified failure boundary

`RmMqttBackend._on_message` validates the selected protobuf contract before
queueing. `Event.param` is a UTF-8 string; `CustomByteBlock.data` is bytes (the
current codec additionally caps the block at 300 bytes). These envelope checks
do not validate the embedded JSON extension's numeric values.

Supported existing formats are Event JSON dictionaries or comma-separated
`victim_id,killer_id`, and CustomByteBlock JSON with `protocol="polar.kill.v1"`
or `POLAR_KILL/1|kind|killer|victim|detail`.

Examples admitted by the protobuf codec include an Event JSON dictionary with
`victim_id: null`, a nonnumeric string/container in a consumed numeric field,
and a numeric distance too large for a finite float. Before repair, conversion
could raise `TypeError`, `ValueError`, or `OverflowError`; nonfinite distances
could also pass into a constructed event. Oversized integer tokens and deeply
nested valid JSON could fail earlier inside `json.loads`.

`OfficialMqttBridge.drain_received_messages` copies and clears the raw queue
before the UI iterates it. An exception in `consume_official_messages` therefore
abandons the remaining raw messages in that drained batch. The outer
`official.consume` tick-stage boundary contains the exception and later tick
stages continue. **This is not evidence that all decoded state is lost:** the
backend's decoded-state queue and projection are separate.

## Narrow repair and compatibility

In [`combat_events.py`](../../rm_client/combat_events.py):

- `_event_numbers` converts only the numeric fields consumed by the event
  branch. Expected conversion failures reject that event; explicitly bad values
  are not replaced with zero. Distances must be finite.
- Missing fields retain existing defaults. Finite numeric strings retain the
  existing `int`/`float` conversion behavior. Objective events do not start
  validating unused elimination-only extensions.
- `_parse_event_param` retains the existing `JSONDecodeError` CSV/detail
  fallback. A distinct parser-only catch rejects plain `ValueError` and
  `RecursionError`, rather than interpreting those failures as default events.
- Custom-block parsing rejects syntax/numeric-token/nesting parser failures;
  its existing malformed-syntax behavior was already rejection.
- No broad catch surrounds UI callbacks, no interpreter limit changes were
  made, and valid CSV, pipe, JSON, defaults, and deduplication remain supported.

The code deliberately does not redefine missing Event identities, accepted
kinds, confirmation semantics, or referee alive-state enums. Those are separate
contracts; passing these tests does not certify every event's meaning.

## Reproduction and verification

The new regression contains **six test methods**, using actual selected-codec
validation, real MQTT queues, backend decoded application, and an actual
offscreen window's tick-stage and combat-counter handlers. Backend starts are
inert; these are not physical-device or native-GPU tests. Valid messages before
and after a bad event are checked, including resulting window counters.

Counts refer to distinct stages of verification, not interchangeable totals:

1. On baseline `c110fa0dc9f8fceda1d777416f989b9c67c979da`, the initial three
   methods produced **50 failing subcases**.
2. The expanded final-five-method suite before adding deep nesting produced
   **72 failures** in the independent review's full baseline-module comparison.
3. The oversized-integer queue test separately failed the initial candidate
   before its parser-limit correction, then passed.
4. For deep nesting, an independent full-module negative control removing only
   the parser `RecursionError` catches produced **one failure with the actual
   parser exception**. The final candidate retained the following valid event.
5. The author's final focused group passed **30 methods**; the independent
   review's broader focused group passed **35 methods**, including all six new
   methods and existing authority/characterization coverage.

The malformed-value matrix covers both Event and custom JSON, five consumed
numeric fields, and seven bad values per field (70 combinations). Positive
controls include numeric strings, CSV, pipe format, absent defaults, and
objective events with unused extension fields. Existing API-freeze and official
economy smoke checks also passed during this repair; API baselines were not
relaxed.

Runtime-specific evidence is explicit: the verified interpreter was Python
3.12.14. Its recursion setting was 1,000, but its JSON parser still accepted
1,020 levels, so the final fixture uses a bounded 10,000-level Event payload
(about 20 KiB). No recursion setting was changed. The integer-token test reads
`sys.get_int_max_str_digits()` and explicitly skips when that limit is disabled
or unavailable; it never changes the limit or claims identical behavior on
other runtimes. The oversized Event is envelope-valid; an equally oversized
current CustomByteBlock would hit its 300-byte codec cap first and is not claimed
as an admitted-message reproduction.

Source references: [`mqtt_backend.py`](../../rm_client/backend/mqtt_backend.py)
`_on_message`; [`transports.py`](../../rm_client/transports.py)
`OfficialMqttBridge.drain_received_messages`;
[`video_backend_mixin.py`](../../rm_client/ui/polar/mixins/video_backend_mixin.py)
`consume_official_messages`; [`polar_app.py`](../../rm_client/ui/polar_app.py)
`official.consume` stage; and the complete
[focused regression](../../tests/test_official_event_numeric_boundary.py).
No raw logs or raw measurement artifacts are published with this report.
