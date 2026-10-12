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
| AutoSpinOnHit | Capability-qualified actual damage counter, original policy/settings subset, bounded same-lease spin request; CPU/synthetic Qt tested | Physical projectile-collision qualification, power/buffer protection |
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

The earlier binding checkpoint saved capability-qualified controls and their
synthetic boundary tests. A same-session narrow-range rejection followed by
wide-range source activation was observed in an explicitly fixed visible-target
scene, but repeating a fixed number of mouse pixels did not consistently reproduce
the complete workflow. The former mouse-rate path mixed wall time with simulation
time. Nearby camera images also exposed source-detector sensitivity.

The cumulative-angle extension below addresses the input-clock defect when the
endpoint advertises that capability. Complete repeated narrow-to-wide acceptance
remains open: a correctly consumed and mechanically settled mouse movement can
still yield no admitted source detections. Zero detections must not be counted as
successful FOV rejection. This is not general-use automatic-aim qualification or
complete ModList parity. No actual test records, private scenes or source
specifications are included in this public source view.
