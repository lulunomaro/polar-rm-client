# polar.sim.operator.v1

Simulation-only HTTP JSON + JPEG, bound to 127.0.0.1. No serial, DT7, MQTT,
official referee packet or remote transport is part of this protocol. All JSON
is an object and uses schema `polar.sim.operator.v1`; nonfinite numbers are invalid.

## Session: GET /session

Required: session_id (nonempty string), epoch (nonnegative integer), robot_id
(`red|blue` + `_hero|_infantry|_sentry`), server_time_ms (Unix milliseconds),
ttl_ms (250), simulation_only=true, hardware_transport=false,
coordinate_frame=`chassis_body`, first_person_camera=`<robot_id>__aim`, last_seq.
A process restart has a new session_id; reset/selection changes epoch. An HTTP
round-trip midpoint estimates server wall-clock offset. Reconnection uses at
least server last_seq+1 and always requires explicit local re-arming.

## Control: POST /control

Required: schema, session_id, epoch, robot_id, seq, sent_at_ms, focus, deadman,
vx_mps, vy_mps, yaw_rate_rps, pitch_rate_rps, fire. Booleans are booleans; seq is a
strictly increasing JSON-safe integer, with gaps allowed. Body ≤4096 bytes.

- vx forward, vy left in chassis body coordinates; speed magnitude ≤1 m/s.
- yaw positive turns gimbal left; pitch positive raises gimbal; each ≤2 rad/s.
- sent_at_ms is adjusted to server wall clock; maximum age 250 ms, future skew
  ≤50 ms. Server lease deducts transit age and uses monotonic wall time thereafter.
- focus=false, deadman=false, invalid identity/order/time/value, terminal match,
  heartbeat loss or client disconnect all cause neutral/no-fire. The server
  validates independently of physics and rendering speed.

HTTP 200 accepted / HTTP 409 rejected acknowledgements contain schema, accepted,
reason, session_id, epoch, robot_id, seq, last_seq, server_time_ms. A rejection
latches client release; it does not preserve previous drive/fire intent.

## Telemetry: GET /telemetry

Required envelope: schema/session_id/epoch/robot_id/server_time_ms, sim_time,
phase (`idle`, `countdown`, `running`, `finished`), remaining_seconds, vp{red,blue},
own, robots, simulation_only=true, hardware_transport=false,
map_source=`simulator_truth_display_only`. Also available: countdown (nullable),
control_owner, terminal_reason, recent events, control gate status and camera
metadata. A gate's applied_operator_command is diagnostic evidence, not a command
request from the client.

Each robot: robot_id, team, role, hp, max_hp, ammo, heat, heat_max, xy[2],
chassis_yaw, gimbal_yaw, pitch, alive. Angles are radians, positions meters.
Selected own status requires finite observed HP/ammo/heat/pose and valid limits;
missing own observations fail closed. Other roster observations may be unknown.
The centered reference arena spans [-6,6] × [-4,4] meters. Map data is never an
algorithm input. Stale or decreasing telemetry simulation time is not applied.

## Video: GET /frame.jpg

503 until available, otherwise image/jpeg plus:

- X-Sim-Session-Id, X-Sim-Epoch, X-Robot-Id
- X-Frame-Seq (strictly increasing new frames; cached duplicates allowed)
- X-Sim-Time (physics simulation timestamp for this picture)
- X-Camera-Name=`<robot_id>__aim`, X-Camera-Overlays=false
- X-Frame-Width, X-Frame-Height

The client validates identity, camera name, overlay flag, dimensions, sequence,
and time before using a picture. JPEG response cap is 4 MB; JSON cap 2 MB. A
wrong-identity/bad picture releases controls even if a good picture arrives before
the next UI tick. Raw images may include actual simulated projectile geometry;
extra trajectory/target overlays must not be baked into this source.

The supplied session/control/ack fixtures are independently synthetic examples,
not extracted simulation logs or official judge wire packets.

## Optional ModList contracts

Capabilities gate each extension. `operator_modes_version: 1`, `spin_mode: true`
and `chassis_follow_modes: ["free"]` admit the complete `mode_request` object:
`spin_enabled` boolean, `spin_direction` auto/cw/ccw, `spin_speed_dps` 120–720,
`chassis_follow` free. The response separates requested/effective values,
request/effective sequence, measured chassis yaw rate and torque, and saturation.
Only a fresh effective response matching the current request is displayed active.

Source aiming requires `aim_request_version: 1`, `automatic_targeting: true` and
selected robot membership in `autoaim_robots`. The complete `aim_request` object
has booleans `enabled` and `hold`, `fov_degrees` 10–180 and positive
`circle_radius_x_norm`/`circle_radius_y_norm` no larger than 2. Normalized values
are radii over the fitted image dimensions, not diameters or whole-window ratios.
The admission rule is the intersection of source-image ellipse and angular FOV.

`autoaim` feedback supplies requested/held/available/active, authority/reason,
source frame/time, selected/in_fov/fov_applied and the actual applied FOV values.
An active response must identify source authority, a fresh valid source frame,
selection inside the admitted range and manual-only firing authority. The
client also requires a control sequence after its current arm/right-hold edge.
Frame indices may skip; they need not increment by one. The display frame index
is not silently treated as the source detector index. No map coordinates are
accepted by the client aiming request path.

For source-authority confirmation, `autoaim.algorithm` must equal the session's
`autoaim_source_identity`, and `autoaim.input_revision` must equal the current
`control.safety_revision`. An active response with mismatched identity/revision
is rejected, even if its numeric frame index appears recent. Native-camera
headers identify source capture sequence separately from display frame sequence;
preview images without a capture index are not claimed as detector observations.
