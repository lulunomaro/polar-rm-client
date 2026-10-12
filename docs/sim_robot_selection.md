# Explicit SIM vehicle selection

Use the original Jello RobotProfile settings, **SIM Vehicle**. This row is added
only to the session-local SIM registry; it does not change the device registry,
user profiles or style. It is unlocked only by the explicit selection capability.
Selecting a model vehicle changes the controlled vehicle and camera, not the
match. The old vehicle, old image and held inputs cannot silently continue.

The window releases keyboard/mouse/fire and clears manual spin, source-aim hold
and hit-spin policy state before queuing one asynchronous selection. Other
selection requests are rejected while that transaction is pending. The transport
invalidates old in-flight responses and attempts an old-identity neutral control
before the server's atomic switch. A successful response does not arm the new
vehicle. Its session, telemetry, camera and neutral acknowledgement must arrive,
then the operator explicitly presses F2. F2 first synchronizes the current
capabilities and camera-dependent request geometry, including a frame that
arrived between Qt ticks; it never bypasses freshness or lease gates.

Unknown or failed requests also release control. A timeout is an uncertain
result: the client reconciles with GET /session and never automatically retries
the selection operation. A lost acknowledgement cannot replay the switch.

## Protocol extension

The existing `polar.sim.operator.v1` session advertises:

- `capabilities.robot_selection=true`, `robot_selection_version=1`
- `endpoints.select_robot="/select_robot"`
- `last_selection_id`, initially -1
- `robot_profiles`, each containing robot_id, team, role and per-robot capabilities

Only model-backed red/blue hero, infantry and sentry identities are accepted.
The client sends POST /select_robot with exactly session_id, epoch, robot_id,
selection_id, requested_robot_id and sent_at_ms. The independent selection ID
is a monotonically increasing safe integer and does not replace control seq.
Its timestamp uses the session's server offset. The loopback endpoint is fixed;
server-provided arbitrary URLs or redirects are not followed.

Success returns accepted, the same selection_id, previous_robot_id/previous_epoch,
the new robot_id and incremented epoch, last_selection_id, requires_rearm=true
and match_reset=false. A failed request returns 409 and a reason with the current
identity. Only success advances last_selection_id. The client gets the full new
session rather than assuming old capabilities apply. The server separately
requires a new-epoch neutral control before accepting active input.

Damage is rebased for the new observation identity, not erased from the judge's
match ledger. Cumulative mouse angles, old source inference and hit-spin state
must not cross the identity boundary. The client ignores old-identity telemetry
and video; tests retain the independent transport lease and no-hardware gates.

## Capability boundary

Vehicle selection grants basic modeled movement/gimbal/manual-fire paths, not
all abilities associated with a role name. Source aiming is still limited to the
qualified stationary red sentry. Other selected roles expose source aiming as
unavailable. Returning to the red sentry restores eligibility, with aiming and
all control still off until an explicit request and rearm. No automatic firing,
hero-lob deployment, world-stabilized gimbal or power/buffer model is added.

## Bounded validation and reproduction

The original Qt Jello setting dispatch was exercised against a real simulation
service and selected-vehicle camera. The bounded scene first established actual
source aiming, then cycled all six vehicles and returned to the original robot.
The match identity remained stable, epochs advanced, simulation time did not
rewind, stale telemetry was rejected and every selected vehicle required explicit
F2. Input, mode state and camera were cleared at each switch. Fire remained off.
The final screenshot was taken after F12 safe stop; it is not an aim-active frame.
Screenshots are actual CPU paints of the original window, not desktop/GPU
qualification. Real records and fixture paths remain private.

Synthetic contract/window tests are available without a running simulator:

```sh
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest \
  tests.test_sim_robot_selection tests.test_sim_operator_boundaries \
  tests.test_sim_operator_modes tests.test_sim_operator_mouse_angle
```

For the bounded actual-source scenario, separately provide an installed helper
and its native source dependencies. Its ready record must include the full SIM
session and loaded source identities, start at red_sentry, and provide the
previously qualified visible-target setup for the explicit one-time 12-pixel
manual camera alignment. It must preserve normal physics/judge behavior and
leave other policies passive. Do not substitute event/HP injection or images for
the native source/camera path. A local JSON file supplies `argv`, `cwd` and optional
`env` for that helper:

```sh
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tools/verify_sim_robot_selection.py \
  --launch <local-launch.json> --out <new-private-output-directory>
```

Run from this client checkout with a Qt-capable interpreter. Output directories
must be fresh and Python optimization is refused so acceptance assertions cannot
be disabled. Keep failed runs, including startup dependency errors. The entry
hashes client sources before/after import and execution; the helper must similarly
bind its loaded core/fixture identity. This bounded scenario does not qualify
all match situations, all role abilities or desktop interaction/performance.
