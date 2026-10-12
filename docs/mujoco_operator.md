# MuJoCo operator mode in the original POLAR window

This optional mode runs the existing `PolarRMWindow`, input dispatcher, keyboard
bindings, Minecraft combat HUD, CS match strip, Jello module list and tactical
map renderer against a **loopback-only virtual simulator**. It is not a separate
spectator player. The normal desktop entry point is unchanged unless a simulation
switch is supplied.

## Start

Install the client's normal dependencies in its own Python environment. This mode
uses PySide6, OpenCV and NumPy; it does not load the local vision model. A compatible
simulator bridge must already be running on the same machine. The bridge/runtime
is a separate project; no simulator source, actual match recording, model weights
or user configuration is included in this change.

```sh
python main.py --mujoco-operator --sim-endpoint http://127.0.0.1:8768
```

Only an explicit nonprivileged `127.0.0.1` HTTP port is accepted. DNS names,
remote addresses, credentials, proxy settings and redirects are rejected. A normal
remote desktop connection may be used to operate a client and bridge together on
one machine; do not expose the bridge port publicly.

Wait for current state and the selected vehicle's first-person picture, then press
**F2** to enable virtual controls. Default bridge selection is red infantry:

- W/S: forward/backward in chassis coordinates; A/D: left/right.
- Mouse: gimbal yaw/pitch. Right moves aim right; up raises aim.
- Left mouse: virtual fire, subject to simulator ammo/heat/referee rules.
- F12: virtual emergency stop. F2 is required again after stopping.
- Opening a menu, losing focus, stale/missing state or video, malformed camera
  identity, a connection/session change, or a UI heartbeat gap releases movement
  and fire. Restoring focus/connection does not resume held controls; release
  inputs and press F2 again.

This phase does not implement in-client vehicle selection/reset, chassis yaw,
automatic targeting, purchasing buttons, or physical vehicle control. Other module
buttons retain their original presentation but have no additional simulator
command consumer. In the reference bridge, legal initial supply is handled by
its referee/runtime, not by fabricated client ammo. Stop means zero applied drive
force and no fire; a physics body can still coast.

## Data and visual boundaries

The main picture is the selected vehicle's **raw** first-person JPEG. The existing
crosshair and HUD are client presentation only; no rendered overlay is sent back
as algorithm input. No synthetic scene is substituted if video fails.

`own` health, ammunition, heat, pose, robot identity, phase/time and team VP feed
the original `RobotState` and match-strip models. No official referee wire bytes
or fake official-link status are generated. Unknown base/outpost/shield/economy,
power and cooling-rate observations are not asserted as real data. The simulation
MC sidebar shows supported state; missing own state suppresses health/thermal
bars instead of presenting a robot as dead. The match strip displays unknowns.

The small map is explicitly **simulator truth, display only**. Contract v1's
reference arena is 12 × 8 m with x in [-6, 6], y in [-4, 4]. The adapter maps
(x + 6, y + 4) into the original map renderer. It draws a coordinate grid and robot
positions, not an official terrain image or inferred obstacle layout. Unknown
roster health remains unknown on the match strip and is omitted from map bars.
No map truth enters movement, aiming or vision algorithms.

Simulation options are isolated in a temporary user-data directory. Original
saved configurations and recordings are not overwritten. Hardware backend,
camera/vision workers and serial discovery are not constructed. Physical
qualification/preflight and serial-rescan actions are unavailable in this mode.

## Safety and protocol

See [sim_operator_protocol.md](sim_operator_protocol.md). A dedicated control
thread emits current intent at approximately 50 Hz; telemetry and camera requests
use separate threads. There is no FIFO of old movement commands. Relative mouse
motion has a small bounded accumulator so faster UI ticks cannot erase a movement
before transmission; release/timeout clears it. The UI watchdog is 150 ms, and the
server independently enforces its 250 ms wall-clock lease. Telemetry freshness is
500 ms; camera freshness is 1.5 s to accommodate the reference 2 fps camera. A fresh
frame requires a strictly increasing sequence. Duplicate cached pictures do not
refresh their age.

The 2 fps / 320×240 reference camera is enough for plumbing verification, **not a
finished operator-performance target**. Telemetry/video run independently: the
frame's simulation time and sequence remain available for timestamp comparison.
No 60 fps, zero-latency or full desktop OpenGL qualification is claimed here.

## Reproduce checks

```sh
QT_QPA_PLATFORM=offscreen python -m unittest tests.test_sim_operator tests.test_sim_operator_boundaries
QT_QPA_PLATFORM=offscreen python -m unittest tests.test_transport_safety_regressions tests.test_ui_state_regressions
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest tests.top_match_bar_integration_test
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest tests.top_match_bar_minecraft_fusion_test
```

A bounded live QA runner starts your compatible bridge in the same process tree,
constructs the real main window with physical constructors replaced by failure
sentinels, sends original Qt keyboard/mouse/focus events, checks motion/aim/ammo,
focus loss, UI-stall latching and disconnect, and saves private evidence:

```sh
QT_QPA_PLATFORM=offscreen python tools/verify_sim_operator.py \
  --bridge-directory /path/to/bridge-project \
  --bridge-python /path/to/bridge-venv/bin/python \
  --out /path/to/private-qa-output
```

These screenshots are real `PolarRMWindow.paint_overlay` CPU renders containing
actual simulator JPEGs, not mockups. They do **not** verify desktop/OpenGL
presentation. Each screenshot has a JSON sidecar with frame sequence, video and
telemetry simulation times, robot and armed state. Actual recordings/screenshots
belong in private evidence storage; only independently synthetic protocol fixtures
are part of this repository.

## Follow-on truth cleanup (separate checkpoint)

The basic operator checkpoint retained original operational module rows whose
simulator commands were not implemented. The follow-on cleanup explicitly marks
those rows **[SIM N/A]**, prevents activation by GUI, hotkey, hotbar, configuration
load or direct module assignment, and leaves the normal device-mode registry
unchanged. It does not implement those missing features.

Spin/autoaim/FOV/friction/locking, unsupported modes, hardware links, purchasing,
revival, deployment, calibration and unconnected strategy/statistics panels cannot
claim success in SIM. Native special-case hotbar/menu actions are guarded as well
as generic module toggles. Movement key remapping remains available. Robot/team,
match and camera settings remain tied to the current simulator session; imported
configs cannot replace the SIM map's 12×8 coordinate frame.

Unobserved FRIC/LOCK/ANTIRO, capacitor/buffer/power and control-zone progress do not
appear as confirmed robot state. The blue Minecraft bar is explicitly **HEAT ROOM**,
derived from `1 - heat / heat_max`; it is not battery charge. World gimbal heading
is chassis heading plus the simulator's relative gimbal joint angle.

```sh
QT_QPA_PLATFORM=offscreen python -m unittest tests.test_sim_operator_truth tests.test_sim_operator_truth_review
```

This truth cleanup is deliberately separate from implementing actual spin or
source-camera automatic aiming. Their presence in the original ModList is not
proof of an end-to-end simulator implementation.

## Capability-qualified ModList controls

This incremental mode retains the original Jello modules and input dispatch.
An endpoint without the new capabilities keeps SpinMode and ArmorAutoAim at
SIM N/A. Supported modules are labelled SIM REQUEST: a checked module is a
request, while the main-window status and chassis compass use simulator feedback.
Unsupported settings such as power protection, follow modes, flywheel, aim
smoothing, target priority and lock decorations remain SIM N/A, with no active
checkbox or operational hotspot. These settings do not gain hardware outlets.

- SpinMode: original X toggle, Auto/CW/CCW and 120–720 degrees/s request.
  Auto is deterministic CCW. Actual yaw rate is reported separately; saturation
  may prevent reaching the requested rate. The only chassis coordinate mode is
  body-relative Free. There is no world-stabilized gimbal promise.
- ArmorAutoAim: original module toggle, right mouse hold, Fov and Circle Size.
  Automatic control requires the server's qualified source adapter and supported
  selected robot. Module/range changes clear all inputs and require F2 again.
  The circle is a requested source-image range, not a confirmed target lock.
  Its original HUD radius is mapped through the actual letterboxed video fit.
- In the currently supported stationary-only source mode, driving or spinning
  stays available but disengages automatic aiming. The server reports when the
  source becomes eligible again. Aiming never grants firing authority; virtual
  firing remains manual left click.
- Right-button release, menu/focus loss, F12, expired lease and disconnect clear
  automatic-aim authority. Re-arming cannot reuse an old confirmed spin/aim
  response. Actual body motion can coast when torque is zero.

The main window reports source availability/reason, active authority and measured
spin rate. It does not draw invented target boxes or feed the map truth into
perception. The selected source-camera image may be downsampled for display;
frame and source identities remain separate. Source-mode validation must use the
same camera geometry as the displayed image and explicitly name any test scene.

Focused synthetic tests:

```bash
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest tests.test_sim_operator_modes
```

A local integration check can launch a separately installed simulation bridge:
write a local JSON file containing `argv` (a list of command arguments) and `cwd`,
then run `python tools/verify_sim_modlist.py --bridge-command-file <local.json>
--out <evidence-directory> --scene-label <explicit-scene-name>`. This entry uses
real Qt handlers and actual simulator frames. It verifies CPU composition only;
a real desktop/OpenGL session and complete ModList parity are separate checks.
Do not put private bridge paths, source specifications or actual records into a
public repository.

## Limited checkpoint: repeatability is still open

This checkpoint saves capability-qualified client bindings and their synthetic
boundary tests. A same-session narrow-range rejection followed by wide-range
source activation was observed in an explicitly fixed visible-target simulation
scene. Repeating a fixed number of mouse pixels did not consistently reproduce
that complete workflow. Mouse deltas currently become wall-clock angular rates
while the simulator integrates simulation time; varying real-time factor changes
the resulting angle. Nearby camera views also exposed source-detector sensitivity.

These are open integration defects/limits, not passing general-use acceptance.
This checkpoint must not be described as full end-to-end qualification, generally
usable automatic aiming, or complete ModList parity. The next separate change is
a sequence-qualified cumulative-angle input contract, followed by repeated
same-session range tests and fixed-image detector checks. No actual test records,
private scenes or source specifications are included in this public source view.
