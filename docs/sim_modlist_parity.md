# Simulation ModList acceptance boundaries

This is an incremental operator integration, not full ModList parity. The source
registry contains 73 modules; repository settings do not establish the user's
active local configuration. Original Jello names, layout and ordinary-device
behavior remain in place.

| Feature | SIM behavior in this increment | Not provided |
|---|---|---|
| Original keyboard/mouse input | Body-relative movement, manual gimbal, manual left fire | Hardware radio/DT7/serial/MQTT |
| SpinMode | X/request toggle, Auto/CW/CCW, speed request, actual yaw/torque feedback | Power Guard, world-stabilized gimbal, target-based Auto direction |
| ArmorAutoAim | Qualified selected-source capability, right hold, actual source FOV request, authoritative active/reason | Arbitrary robot support, moving-source qualification, automatic firing |
| Fov / Circle Size | Logical HUD radius mapped through actual fitted camera pixels; range changes require rearm | Invented target locks, map truth used for perception |
| Aim display style / Smooth / Priority | Explicit SIM N/A | Unimplemented algorithm parameters disguised as effective settings |
| AutoSpinOnHit | SIM N/A | Hit-event driven spin policy |
| ChassisAssist | SIM body-relative Free only | Gimbal/field following, power/buffer protection |
| FrictionWheel / LockState / GimbalPower | SIM N/A | Mock/default active robot indicators |
| Robot/team/scenario selection | Authoritative current session displayed | Switching the controlled robot through an unrelated UI profile |
| HUD / referee / minimap | Real own HP/ammo/heat, phase/time/VP, roster HP, labelled display-only XY map | Unknown official fields or invented protocol packets |
| Economy / strategy / deploy / chat | SIM N/A | Mock-success commands with no simulator consumer |
| ESC / GUI / focus / F12 / disconnect | Immediate request release, independent watchdog, explicit F2 rearm | Automatic resumption of previously held input |
| Pure visual modules and movement bindings | Original local presentation and key binding paths | Desktop/GPU qualification from offscreen tests |

A checked supported operational module is labelled **SIM REQUEST**. Only validated
simulator feedback after the current input lease can show confirmed execution.
Unimplemented settings within a partially supported module render **SIM N/A**
instead of an active checkbox or interactive control.

Source-mode validation uses real source pixels and inference through a separate
simulation-only bridge. A fixed visible-target test scene is a bounded contract
check, not a general match performance result. Stationary-only source limits,
calibration assumptions, frame freshness and actual test-scene details belong in
the private run evidence. Full moving-robot robustness remains a separate stage.

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
