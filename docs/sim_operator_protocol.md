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
