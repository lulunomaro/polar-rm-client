# Typed SIM spin, bounded CPU checkpoint

## Scope and use

The opt-in v2 client reuses the original SpinMode / X module. This checkpoint adds an explicitly negotiated `spin_v1` motion request. It has CPU contract, original Qt event-logic, synthetic guard, and loaded-model metadata tests. It has **not** run a new spin physics, rendered-camera, desktop or cross-device qualification. Prior manual-yaw results do not qualify spin.

From the Polar client checkout root, use the existing v2 entry:

    python main.py --sim-platform-v2 --sim-role operator --sim-endpoint http://127.0.0.1:8766

The server must advertise the matching capability. Watch a robot, select X/SpinMode and its direction/speed, then use F2 to claim and F2 to arm after acknowledgement. Changing X, direction, speed, robot or ownership releases controls and requires explicit rearm. Capability arrival does not automatically enable saved spin settings. Spectators cannot control vehicles.

- Original Q quick-wheel / E inventory routing and remapped keys retain priority.
- Manual chassis yaw is still its separate bounded command. Spin replaces yaw, rather than adding to it; body-relative WASD translation is retained and norm-limited to 1 m/s.
- A navigation adapter owns the entire motion channel. X and manual yaw cannot overlay it or silently unbind it.
- Power Guard and Mock Output settings remain unavailable in SIM; there is no new power model or hardware egress. Fire stays separately manual.
- C / field or gimbal-relative driving and right-button source-aim parity remain separate work.

## Wire and units

For a strict server-window session, motion value is exactly:

    {"mode":"spin_v1","frame":"base_link","vx_mps":0.0,"vy_mps":0.0,"direction":"auto","speed_dps":420.0}

Directions are `auto`, `cw`, `ccw`; auto deterministically resolves to CCW. Positive yaw is CCW. Speed is 120–720 degrees/s, converted to radians/s. The default 420 degrees/s is a target of about 7.33038 rad/s, not proof the robot reaches it. Existing manual ±4 rad/s and navigation ±2 rad/s limits are unchanged. Huge/non-finite numbers, booleans in numeric fields and extra fields are rejected.

The trusted backend derives support from the actual loaded yaw actuator/joint mapping, including +Z hinge, own generalized coordinates, unit transmission/gain, control limits and force-limit assumptions. No role-name shortcut grants support. Current model immutability is required; vehicle hot reload is outside this checkpoint.

A mode signature is tied to the motion lease epoch. Only a fully valid admitted request with a different signature faults the mode. A fault requires release and a fresh claim; renew, new windows and bind/unbind do not bypass it. Manual velocity changes do not change its signature. Literal auto and ccw are intentionally different setting signatures even though auto currently resolves CCW.

## Safety and display

The existing 150 ms server window, fresh post-ACK input sample, final consumption rechecks and no-automatic-reclaim behavior remain. Old inputs retain their original deadline. Loss of focus, state/camera, connection, capability or ownership clears input. A released vehicle can coast by inertia; zero manual yaw is a braking request, while release removes drive force/torque authority.

The catalog piggybacks cached capability data with session/watch/episode identity. The HTTP thread never reads live model/data. Delayed replies cannot restore old negotiation. No additional polling or synchronous HTTP is added to the Qt input thread.

Spin confirmation requires a matching actual integration-entry receipt, final pre-step guard, exact input payload/sequence/window/deadline, current ownership, and matching post-step measurement. A later fire guard can revoke motion before integration; that is not displayed as applied spin. Target angular speed, measured angular speed, measured actuator torque and saturation remain distinct. All server timestamps are compared only within the server clock domain.

## Reproducible CPU checks

From the Polar client checkout root with its existing test dependencies:

    QT_QPA_PLATFORM=offscreen PYTHONPATH=.:tests python -m unittest test_platform_spin test_platform_manual_yaw test_platform_manual_yaw_ui test_platform_handoff test_platform_handoff_lookahead test_platform_control_windows test_platform_adapters test_platform_diagnostics test_platform_display_generation test_sim_platform_v2 test_sim_operator test_sim_operator_boundaries test_sim_operator_modes test_sim_operator_mouse_angle

From the navigation checkout's `simulation/mujoco_match` Python root with its existing MuJoCo/numpy dependencies:

    python -m unittest rm_match.platform_v2.test_spin rm_match.platform_v2.test_spin_feedback rm_match.platform_v2.test_spin_model_metadata rm_match.platform_v2.test_control rm_match.platform_v2.test_control_windows rm_match.platform_v2.test_runtime rm_match.platform_v2.test_adapter_activation rm_match.platform_v2.test_observation_refresh rm_match.platform_v2.test_contracts rm_match.platform_v2.test_adapters

The metadata test creates an MjModel only; no MjData, physics integration or renderer. Guard/feedback tests use explicit synthetic backends. No real camera frame or actual spin response is inferred from those tests.
