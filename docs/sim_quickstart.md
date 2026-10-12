# Local manual SIM and bounded visible OFFLINE/native

Use the simulation branches, not either repository's default hardware/LLM entry:

- navigation: the user’s separately supplied, stage7-qualified navigation checkout, referenced below as `NAV_ROOT`; its private repository identity is recorded separately.
- this client: branch `feat/mujoco-operator-20261012`; stage7 lifecycle base `634222147c3c61d3dc9d967c87ff46963d4b4a52`.
- The launcher is an additional reviewed increment on those bases. Check out the publisher's exact launcher commit; the base commits alone do not contain it.

Both repositories must be on the same machine/network namespace: the transport accepts only `http://127.0.0.1:PORT`. No LAN listener, firewall adjustment, remote host, or WSL interoperation has been qualified. Paths may contain spaces. The command arguments below are explicit local inputs; no models or dependencies are downloaded by the launcher.

## 1. Prepare the two Python environments

Python 3.12 is the documented source setup. From the navigation repository's `simulation/mujoco_match` folder, install `requirements-camera.txt` into its own environment. From this client root, install `requirements-core.txt` into a separate environment. Use their full Python executable paths below. Do not assume the client's OpenCV installation is visible in the navigation environment.

The camera dependency file includes the existing nav requirements and its declared OpenCV 5.0.0.93 pin. The standalone camera extra is also exposed as `.[camera]`. No fresh package installation or Windows wheel validation was performed in the launcher-only stage. The older qualification environment used OpenCV 4.10.0.84/NumPy 2.2.6; these import checks do not certify the complete pinned install. The client needs PySide6, OpenCV, NumPy and its existing core communication packages for imports. OpenVINO and policy weights are unnecessary for manual SIM.

Example PowerShell setup, after choosing these local paths:

```powershell
py -3.12 -m venv C:\RM\nav-venv
C:\RM\nav-venv\Scripts\python.exe -m pip install -r C:\RM\navigation\simulation\mujoco_match\requirements-camera.txt
py -3.12 -m venv C:\RM\client-venv
C:\RM\client-venv\Scripts\python.exe -m pip install -r C:\RM\polar-rm-client\requirements-core.txt
```

These are installation instructions for the user, not actions performed by this checkpoint. Linux uses the corresponding `venv/bin/python` paths. A desktop graphics context is required for the Qt window; Windows desktop/OpenGL remains untested.

## 2. Manual local simulation

From the client root, first print the plan without starting anything:

```powershell
C:\RM\client-venv\Scripts\python.exe tools\launch_sim_platform.py --mode manual --nav-root C:\RM\navigation\simulation\mujoco_match --nav-python C:\RM\nav-venv\Scripts\python.exe --client-python C:\RM\client-venv\Scripts\python.exe --out C:\RM\runs\manual-new
```

Add `--run` to start the planned server and original Qt SIM operator window. The default server wall budget is 300 seconds; `--max-wall-seconds` changes it within (0, 3600] seconds. Output must be new. Default port 8766 is loopback only. Linux headless server can use EGL; Windows uses GLFW and refuses an inherited EGL setting. The Qt window is separate and visible: unset `QT_QPA_PLATFORM=offscreen/minimal` rather than pretending a CPU screenshot is a visible window.

In the original Jello RobotProfile list, choose the watched vehicle. Watching alone cannot control it. F2 requests leases; a second F2 arms. WASD/mouse/left click use the existing manual SIM path. F12, lost focus, stale frames, session errors and closing the window release/neutralize through existing gates. Ammo and referee restrictions still apply. This is the existing simplified 3v3 vehicle model, not the missing MOD4 wheel-leg asset/policy package.

For direct separate-console launch, from nav's `simulation/mujoco_match` run:

```powershell
C:\RM\nav-venv\Scripts\python.exe -m rm_match.platform_v2 --camera --port 8766 --camera-total-fps 2
```

Then from this client root:

```powershell
C:\RM\client-venv\Scripts\python.exe main.py --sim-platform-v2 --sim-role operator --sim-endpoint http://127.0.0.1:8766
```

Always retain the explicit SIM flag. Ordinary `main.py`, bare `main.py --help`, and the default double-click launcher are not this safety-isolated route. The dedicated new launcher's `--help` works with standard-library Python alone.

## 3. Visible OFFLINE/native diagnosis: Linux only

Required separately supplied, locally authorized artifacts:

- exact stage7 server/client lifecycle sources;
- historical physics initializer JSON for the qualified scenario;
- `rmcs-sim-operator-fov-v1` native spec with matching Linux ELF64/x86-64 binary;
- executor YAML, autoaim YAML, `shenzhen-0526.onnx` and matching native shared libraries/rpath layout;
- a Linux GUI-capable client environment and nav camera dependencies.

These are not interchangeable with T2/T3 benchmark actors, a PyTorch wheel-leg policy, or the old Isaac actor. No private scene, weights, native binary or runtime libraries are distributed by this launcher increment. The original native build/spec tools still require their explicit external source/build/dependency tree. Unresolved absolute paths must be migrated and requalified; the launcher does not rewrite hashes or pretend to repair ABI/rpaths. Static byte validation cannot prove a usable native runtime.

```sh
CLIENT_PY tools/launch_sim_platform.py --mode offline --nav-root NAV_ROOT --nav-python NAV_PY --client-python CLIENT_PY --scene-json PRIVATE_SCENE.json --native-spec LOCAL_VERIFIED_SPEC.json --out NEW_RUN
```

Replace all placeholders with real paths; add `--run` only after inspecting the plan. This starts one original visible Qt spectator, auto-watches red_sentry without claiming or arming, and waits for a valid watch acknowledgement before the separate supervisor claims only the algorithm gimbal lease. It reuses stage7 RenewalLifecycle and its terminal cleanup handshake. An observer-ready event confirms a shown window and subscription, not that any camera frame was displayed.

The run remains exactly one stationary, fire-off 24-exposure scene with modeled delays and a 90-second server wall budget, not an unlimited game/full match. Native tracking or process startup is not a performance qualification. The old `verify_offline_platform.py` remains a separate 2×24 offscreen qualification driver; it was not repurposed as a game launcher. There is no automatic live/native fallback.

Current native ELF/.so and subprocess-pipe select require Linux. Changing file extensions or enabling EGL cannot make them Windows binaries. Windows manual command generation is covered by mocks only. A Windows client on another host is not supported by the existing loopback contract.

## Logs and qualification limits

The new output stores the exact command plan, source/input hashes, child stderr, lifecycle/cleanup result and readiness observations. No bearer token is printed. On close/failure the supervisor reaps only its own children, after stopping renewal and attempting ordered cleanup. A timeout, uncertain renewal, unconfirmed cleanup or forced kill is recorded as failure, not retried or reported as a clean qualification.

This increment is qualified initially only by dry-run/help/relocation and mocked process/lifecycle tests. Actual first-frame presentation, keyboard/mouse interaction, Windows GPU behavior, native dependency availability and full-match stability require separate explicit validation. The entry does not load a real serial/DT7/MQTT backend or contact a paid service.
