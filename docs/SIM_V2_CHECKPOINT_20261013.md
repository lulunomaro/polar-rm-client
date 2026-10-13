# Opt-in SIM v2 client and control checkpoint

This source checkpoint joins the existing opt-in v2 platform with adapter selection, strict server-issued control windows, bounded diagnostics and handoff scheduling, fresh own-sensor sampling for the rule-navigation profile, manual yaw, typed spin, status presentation, and preservation of virtual-environment Python invocation paths.

## Source roots and basic startup

`CLIENT_ROOT` is the Polar client checkout root containing `main.py` and `rm_client`. `NAV_ROOT` is the navigation checkout's `simulation/mujoco_match`, containing `rm_match`. All client commands below run in CLIENT_ROOT; all server commands run in NAV_ROOT. This also resolves the older `From this checkout` wording in `docs/SIM_ADAPTER_SELECTION.md` and `rm_match/platform_v2/ADAPTER_UI.md`.

With the existing dependencies, assets, and a working Linux desktop environment, invoke the selected virtual-environment executables without resolving their symlinks to a base Python:

Server, from NAV_ROOT:

    /absolute/nav-venv/bin/python -m rm_match.platform_v2 --camera --port 8766 --camera-total-fps 2 --max-wall-seconds 300

Client, from CLIENT_ROOT:

    /absolute/client-venv/bin/python main.py --sim-platform-v2 --sim-role operator --sim-endpoint http://127.0.0.1:8766

These are explicit simulation entry points. The package is not self-contained: it does not bundle interpreters, native models, or vehicle assets. A visible Qt window needs an actual display server. Offscreen CPU tests do not establish visible-desktop or Windows qualification. Launcher help, dry-run, and standard-library environment probes do not establish an end-to-end visible launch.

Watching a vehicle does not claim it. F2 claims the selected channels; a subsequent explicit F2 arms after acknowledgements and fresh state/frame eligibility. Editing spin mode/settings, changing vehicles, losing focus, or losing authority clears controls. Navigation owns the entire motion channel; manual yaw/spin cannot overlay it. Aim owns gimbal only. Fire remains separately manual.

## Reproduce focused CPU checks

Use the existing dependency environments. These commands are Linux examples, not a promise that all platforms or dependencies have been qualified.

Client, from CLIENT_ROOT:

    QT_QPA_PLATFORM=offscreen PYTHONPATH=.:tests python -m unittest test_platform_adapters test_platform_diagnostics test_platform_handoff test_platform_handoff_lookahead test_platform_manual_yaw test_platform_manual_yaw_ui test_platform_spin test_platform_status test_sim_launcher_venv -v

Server, from NAV_ROOT:

    python -m unittest rm_match.platform_v2.test_adapter_activation rm_match.platform_v2.test_observation_refresh rm_match.platform_v2.test_spin rm_match.platform_v2.test_spin_feedback rm_match.platform_v2.test_spin_model_metadata -v

The adapter-activation module includes loopback HTTP tests with a synthetic backend. Model-metadata tests instantiate an MjModel but do not integrate physics. Qt event/geometry tests and synthetic state fixtures do not establish rendered-camera or hardware qualification.

## Limits that remain

- The 150 ms control window and original action deadlines remain in force. New windows and leases cannot renew an old input or adapter result. Server and client monotonic clocks are not subtracted across machines.
- Prospective sampling is limited to the current simplified own-odometry/anonymous-range rule-navigation profile. It is not MID360 SLAM or a new sensor-rate guarantee.
- Manual yaw and typed spin are simulation controls. Requested spin speed is distinct from measured speed, torque, and saturation. Releasing authority removes drive commands; inertia and gimbal holding remain possible.
- Status distinguishes requests, admission, a fresh server-snapshot gate, and historical applied samples. Final status-source changes have CPU qualification; earlier synthetic preview images do not qualify the final source pixels.
- Bounded Linux loopback/noGL evidence does not establish a complete match, Windows desktop operation, cross-device LAN, or real-device output. The real-time camera/native path has not met its unchanged wall-clock qualification. Independent offline SIL uses explicitly modeled delays and currently supports Qt observation, not arbitrary manual offline control.
- C reference-frame driving, full original V/RMB/FOV parity, v2 hit-triggered spin, power/buffer models, automatic fire, and all 73 original modules are not completed by this checkpoint.

Earlier failed attempts remain historical failures. One spin evidence verifier initially rejected trace-envelope differences; a closed-envelope read-only correction rechecked the same physical records without a second run. Do not describe the original verdict as having passed. Raw logs, images, credentials, local configuration, and private evidence are excluded from this source publication.

## Publication identity

Final source-only checkpoints: client `0eacb37bf23eb1644de78e02790f426fa378c0c8` and navigation `c4ce7dc130b9e5be6cf00b959cec4b20e9b2db61`. The reviewed overlay manifest SHA-256 is `8a04db77b848d72b667a17e52ed6900b255be4c7003bd7815e1d186836edd327`. The 34 client and 16 navigation payloads retain their reviewed bytes. This companion document is a new publication note and is not one of those historical frozen payloads.
