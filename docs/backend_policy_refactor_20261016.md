# Backend control-policy extraction

## Scope

This stage separates the policy decisions in `BackendManager` from mutable
registry and transport objects. It does not add control features, change
protocol definitions, optimize autoaim, or qualify physical hardware.
The frozen pre-change source is commit
`da91fc54b97caffd8f76b1aba86f88105d3c0379`.

The design follows the [function](https://aeryzhao.github.io/cleancode-book/ch3.html),
[data-boundary](https://aeryzhao.github.io/cleancode-book/ch6.html), and
[class-responsibility](https://aeryzhao.github.io/cleancode-book/ch10.html)
chapters of the requested Clean Code reference. The meaningful separation is
between value-based decisions and side-effect adapters, rather than simply
relocating the manager into another file.

## Boundaries

- `robot_link_settings.py` is the registry-name adapter. It captures drive,
  gimbal, spin and transport requests into frozen, explicit data structures.
  Policies receive scalar settings rather than UI modules or mutable settings.
  Raw persisted values remain unconverted until their original validation point,
  so an early transport-retirement return does not unexpectedly parse later
  settings. Default endpoint sentinels have one definition.
- `polar_control_policy.py` resolves robot role limits, checks distinct control
  gates, selects the sole gimbal egress, and advances the bounded spin state.
  It does not mutate state, read a registry, open a device or send a command.
- `radio_km_feedback.py` interprets readback into a value result. Frame receipt,
  firmware participation and fresh key evidence remain distinct. It returns
  the next counter explicitly instead of retaining a manager reference.
- `BackendManager` applies those decisions through narrow telemetry,
  configuration, authority, motion and submission adapters. The ready tick now
  presents the execution order in 56 lines rather than 505. The manager remains
  a compatibility facade; it still has unrelated responsibilities outside this
  stage, so this is not a claim of complete architectural decoupling.

No extracted object receives the manager, a shared `self` context, or a service
locator. The public manager APIs and existing spin fields remain available to
callers and lifecycle code.

## Preserved safety and compatibility details

- Reported robot identity controls chassis limits; selected identity remains
  the existing gimbal-role gate. A mismatch stays visible. This stage does not
  silently unify these historically different rules.
- Chassis drive requires module enable, explicit authorization, chassis request,
  fresh POLAR telemetry, a permitted role and no E-stop. Radio keyboard/mouse
  intentionally does not require USB telemetry.
- Radio authorization is published before diagnostic parsing, so a diagnostic
  exception cannot retain authority from the previous tick. Nonfinite values
  injected outside the normal bridge contract still preserve the old abort
  behavior; broader malformed-telemetry hardening is a separate change.
- Spin ownership is independent of chassis drive. It continuously asserts ON,
  asserts OFF only after owning spin, and relinquishes OFF on confirmed stop or
  the existing bounded fallback. SHIFT remains excluded from virtual RC.
- The firmware gimbal-host request retains its existing independent gates.
  Spin and gimbal-host decisions recheck live-link freshness at their own later
  points in the tick rather than retaining an earlier authority snapshot.
- Auto/115200 settings do not override scenario endpoints. Explicit port, baud,
  bridge and mock-mode changes retain the existing retirement order. An old
  real writer cannot be relabeled mock before its cleanup completes.
- Zero percentages retain the historical 100% fallback. Incomplete or malformed
  scale settings abort before constructing a replacement drive intent.
- Fire/friction defaults, transport masks, role limits, protocol bytes, and
  transport neutralization logic are unchanged. The refactor never starts
  transports in its tests.

## Reproduction

Run the ordinary tests with the repository's supported environment:

```sh
QT_QPA_PLATFORM=offscreen python tools/verify_all.py \
  --test test_backend_policy_characterization \
  --test test_backend_policy_boundaries \
  --test test_polar_control_policy \
  --test polar_bridge_test --test polar_integration_test \
  --test test_device_manager_nonblocking \
  --test test_controller_angle_integration \
  --test test_transport_safety_regressions \
  --test robot_roles_test --test test_settings_are_read
```

`tests/fixtures/backend_policy_baseline.json` contains 97 named observations
recorded before production edits. Its source commit and exact manager SHA-256
are embedded. Ordinary tests only read it. Explicit regeneration is refused
unless the manager bytes exactly match the original reviewed source.
Observations cover decision values, HUD/control state, ordered adapter effects,
configuration precedence and complete encoded 0x01/0x03/0x04 frames.
The fake delegates send-frequency clamping to the production implementation.

For a larger seeded differential replay without modifying the fixture:

```sh
QT_QPA_PLATFORM=offscreen python tools/compare_polar_policy.py \
  --cases 10000 --seed 20261016 --json /tmp/polar-policy-differential.json
```

This loads only the pinned manager source from local Git history and runs both
versions against the same recording adapters and current codec. Its report
records source hashes, seed, executed/aborted cases, mismatch details and an
observation digest. Malformed-input cases compare partial control/state/config
and event history as well as the exception type; diagnostic wording is excluded.
Failures outside the observed tick are marked incomplete, not equivalent passes.
The fault matrix preserves revocation-before-diagnostics and clearing a previous
gimbal plan before a later identity-validation error. Separate protocol suites
remain necessary because both
managers intentionally use the same codec in this comparison.

These checks are offline assertions. Missing firmware references, native
Windows/GL acceptance and actual robot qualification remain separate gates.
