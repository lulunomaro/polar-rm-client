# SIM v2 adapter selection (bounded CPU integration)

Launch the existing opt-in entry using `python main.py --sim-platform-v2
--sim-role operator --sim-endpoint http://127.0.0.1:8766` (one command line).
The server must expose `adapter_ui_version=1` and `adapter_activation_version=1`.
Servers without these contracts retain manual operation; adapter choices are N/A.

In the existing Jello RobotProfile settings, choose a watched vehicle, then
`V2 Navigation` and `V2 Aim`. Manual is the default for both. Selecting or changing
parameters releases control. F2 first requests leases, keeping input disarmed;
a second F2 explicitly authorizes the displayed plan. Binding and waiting for a
sensor/result are not proof of actuation. F12, loss of focus, stale input/camera,
watch changes or disconnect release all channels. Reclaim is explicit.

Motion belongs to either manual WASD or the selected navigation adapter; gimbal
belongs to either manual mouse or the selected aim adapter. Fire stays a separate
manual channel; an algorithm's fire_permission never fires the simulation.

## Actual choices

- `nav_rule_v1`: existing sensor A* rule baseline. Requires own odometry and
  ranges. Goal X/Y are explicit operator-entered world coordinates in metres;
  neither opponent truth nor map-click target coordinates are injected. This is
  not full competition decision-making or qualified MID360 navigation.
- `aim_pixel_baseline_v1`: existing red-blob pixel baseline. Requires own raw camera
  and odometry. It is not native RMCS and its colour selection is not guaranteed
  to correspond to the opposing team. No automatic fire.
- `aim_native_rmcs_v1`: only offered after explicit trusted native registration
  on the server and on a supported robot. Native FOV and ellipse radii are typed
  settings. Existing calibration, stationary and freshness gates still apply.
  Registration does not qualify real-time inference or Windows native support.
- Zero-motion and hold fixtures stay diagnostic N/A descriptions, not operational
  choices. Unknown versions/config contracts, unavailable registration and missing
  declared sensor providers are disabled.

Declared sensor support means a provider exists. A new binding must still receive
fresh observations with its own identity. The UI shows pending or a timestamped
historic applied sample; it does not turn an old sample into current confirmation.

## Server-relative authorization

Strict-profile bindings start inactive. After binding, the control worker obtains
an original server-issued 150ms window and waits for a newly sampled Qt input.
It sends `/v2/adapter_input` independently per binding. No client/server clock
synchronization is assumed: local expiry is conservatively based on request start.

Expiry revokes a used binding epoch. New windows/lease renewals cannot revive it.
Each observation/action additionally retains its original authorization deadline,
so an old asynchronous result cannot borrow a newer pulse's deadline. Both final
actuator and integration gates recheck these limits. In-flight packets can remain
valid until their bounded server deadline; network focus loss is not instantaneous.

Legacy SDK bindings retain their earlier semantics. The original hardware and v1
entry are unchanged. Loopback remains the only enabled deployment. This milestone
contains CPU protocol, mocked sensors/Qt input logic and synthetic-backend HTTP
checks; it adds no camera rendering, native inference, desktop or physical-device
qualification. Real-time and offline SIL profiles remain distinct.

## Reproducible focused client checks

From this checkout, install the existing project test dependencies and run:

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=.:tests python -m unittest test_platform_adapters test_platform_control_windows test_platform_display_generation test_sim_platform_v2 test_sim_platform_entry
```

On Windows set those environment variables through PowerShell before running the
same Python module command. Offscreen tests do not establish desktop GPU support.
