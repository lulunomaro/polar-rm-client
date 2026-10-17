# MQTT live endpoint admission

## Frozen defect and scope

Source before this correction: `c4d3bcf97ef43056bf316bbec593802652120878`.
`tests/test_mqtt_endpoint_admission.py` was introduced before production changes
and fails against that source. Its initial run executes 12 tests and exits 1.

The independent review and the new regression test reproduce these direct
mutable-registry/API writes without starting MQTT:

| Raw Port | Previous result | Backend port | Raw setting afterward |
| --- | --- | --- | --- |
| `"not-a-port"` | `ValueError` during final writeback conversion | 3333 | unchanged |
| `"3333.0"` | `ValueError` during final writeback conversion | 3333 | unchanged |
| `3333.5` | no warning; truncated for the backend | 3333 | 3333.5 |
| positive infinity | uncaught `OverflowError` | 3333 | unchanged |

These are raw setting values, not a screenshot-based claim about displayed text.
The numeric display formatter itself rounds step-1 settings; it does not repair
the stored value. Retrying the original strings does not remove the error.

The original `sync_mqtt_link` itself checks 1000..9999, independently of the
catalog's UI metadata. That live-setting range is preserved, not newly imposed
on other entry points. Scenario validation separately accepts TCP ports
1..65535; `RmMqttBackend.set_address` is unchanged. Regression cases explicitly
retain 18830 and 65535 through both APIs, then prove that rejecting a bad live
setting keeps that current endpoint and its existing client. A subsequent live
sync may still report its historical range warning, but never substitutes a
3333 default for the API-owned endpoint.

The production catalog declares Port as 1000..9999, step 1. The slider delegates
to `Setting.set_from_ratio`, which rounds to that step and clamps to the range.
The regression test characterizes ordinary finite pointer ratios; it does not
claim that the slider produces malformed strings, fractions, or infinity.
The profile loader separately accepts finite numeric values and converts them
to floats, without step rounding. Thus an integral profile value legitimately
arrives as a float, and a finite fractional profile value reaches the live
endpoint boundary. Profile validation rejects string and nonfinite numeric
values. Direct registry/API assignment bypasses that profile validation.

The correction is limited to `BackendManager.sync_mqtt_link` and a pure value
admission helper. Scenario request validation, direct low-level transport APIs,
protocol definitions, broker ownership, and lifecycle retry policy remain
separate responsibilities.

## Reproduction

```sh
python tests/test_mqtt_endpoint_admission.py
python tools/test_mqtt_link_settings.py
python tests/test_mqtt_lifecycle_nonblocking.py
python tests/test_mqtt_cleanup_ownership.py
```

The first suite uses an actual manager and backend with a fake Paho boundary.
It never opens a network connection. Live-owner tests exercise production
start/stop/restart bookkeeping and controlled cleanup completion; they do not
claim successful real broker or physical robot qualification.

## Corrected admission and ownership

`mqtt_endpoint.admit_mqtt_endpoint` accepts a detached pair of raw values and
the backend's current accepted endpoint. It has no manager, registry, resolver,
socket, thread, or device dependency. It returns a frozen canonical value and
validation note. The manager remains the sole side-effect adapter.

- Port accepts plain integer values, finite integral floats, and integer text
  accepted by Python's decimal `int` conversion, within the existing live
  1000..9999 range. Integral profile floats and integer strings are canonicalized
  to actual integers in the setting. Fractional floats are rejected rather than
  truncated. Decimal-looking text such as `"3333.0"` is invalid, as before; its
  rejection now completes safely rather than raising during writeback.
- Boolean, container, complex, byte-string, nonfinite, and out-of-range ports
  are rejected. Arbitrary conversion methods on other objects are not invoked.
- Host accepts trimmed text without interior whitespace. Empty, nontext, and
  whitespace-containing values retain the current host. Hostnames and IPv6
  strings remain supported; this is not hostname resolution or connection
  validation. Interior tab/newline/Unicode whitespace now follows the same
  rejection rule as an ordinary space.
- Invalid fields fall back independently to the current endpoint. No global
  default replaces a known endpoint, and an unchanged result does not restart
  an owner or advance its receive generation. A legitimate new endpoint still
  uses the existing transport's lock/revoke/retire/restart behavior.
- The manager captures the raw values once before admission. After transport
  application, it repairs only settings that still hold those exact objects.
  It does not reparse unsafe raw input for writeback. Object identity here is
  request provenance, not numeric equality: a callback can replace `4444.0`
  with another equally valued object and that is still a later request.
- `set_address` can synchronously deliver an input-clear observer. If that
  observer edits a setting, the new request survives until its next sync. If
  it reenters sync, the nested call's newer endpoint and validation diagnostic
  survive. The outer call returns the note for its own captured request and
  does not overwrite the nested note after observing replaced raw values.
  A callback that only edits settings (without nested sync) also supersedes
  the old request: the outer note remains its return value, while shared
  `last_error` retains the previous diagnostic until the later sync. This
  conservative ownership rule deliberately does not claim that every return
  note is also published into `last_error`.

The lifecycle blocker still returns before reading/admitting endpoint settings.
Failed cleanup retains its original owner; endpoint repair and repeated sync do
not turn into implicit cleanup retries. A repaired valid request still coalesces
behind an old owner, and explicit stop/start retains the existing retry path.

## Verification

After the correction, all original 12 regression methods pass unchanged.
Ten additional tests cover equal-valued replacement provenance, a later
malformed callback edit, request-specific diagnostic publication, failed
input-clear observer recovery, unsupported profile precedence, absent settings,
current-owner fallback, foreign conversion objects, and unchanged wider TCP
API/scenario ports. All 22 methods pass.
Together they cover repeated invalid input, later valid recovery, nested and
non-nested callback edits, ordinary profile/slider producers, and delayed or
failed cleanup ownership. The static quality gate passes without any baseline
or threshold edit. Existing MQTT lifecycle (21 tests), cleanup ownership (8 tests), and
broker-setting command-line assertions also pass separately. These runs use
the prepared Python 3.12 verification environment; no aggregate verifier lock,
network broker, or hardware is involved.

### Independent review and expanded regression

Independent review accepted code/test snapshot
`28a281d94f2665e957d644d79eff9138964ebddf` on 2026-10-16. The reviewer:

- Reproduced the original 12-method red run at
  `15e9252cee55d3c99655627640164d4c81f70964` independently.
- Compared all 26 original qualified test/helper AST definitions and found them
  unchanged in the fixed candidate, so passing did not come from weakening the
  red assertions.
- Ran the 22 admission, 21 MQTT lifecycle, and 8 cleanup ownership tests
  independently: all 51 passed. The settings CLI, actual static quality gate,
  and full-base-range whitespace/diff checks also passed.
- Confirmed unchanged wider TCP/scenario endpoint handling, retained cleanup
  ownership, per-field fallback semantics, and the explicit diagnostic policy
  for superseded requests. No production blocker remained in this scope.

The implementation worker also ran the following independent test entry points
at that snapshot, using the prepared verification interpreter and no shared
aggregate-verifier lock:

```sh
for name in \
  test_mqtt_endpoint_admission test_mqtt_lifecycle_nonblocking \
  test_mqtt_cleanup_ownership test_backend_async_lifecycle \
  test_backend_input_owner test_backend_lifecycle_input_failure \
  test_backend_policy_boundaries test_backend_policy_characterization \
  test_transport_safety_regressions polar_link_test polar_bridge_test \
  polar_integration_test; do
  QT_QPA_PLATFORM=offscreen PYTHONPATH=. python "tests/$name.py" || exit 1
done
python tools/test_mqtt_link_settings.py
python tools/quality_gate.py
```

The nine unittest entry points total 135 passing methods. All three POLAR CLI
suites and the broker-settings CLI also exit zero. The static gate reports 205
production Python modules, 3344 functions, and zero initialization cycles at the
pinned candidate. These counts describe this isolated review snapshot; parallel
integration changes may legitimately produce different counts.

Production changes remain one import and one narrow method in the manager,
plus the 54-line pure admission module. No transport implementation, protocol,
public API fixture, or quality baseline was modified. All code/tests were
committed before this final evidence-only documentation update. This is offline
behavioral evidence, not native desktop, real MQTT broker, or robot acceptance.
