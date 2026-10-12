# Opt-in simulation platform v2 client: bounded camera/Qt checkpoint

The original POLAR window, MC/CS HUD, Jello vehicle selection and input collection
are reused. This is a separate simulation-only dependency bundle; no physical
serial/MQTT transport, camera driver or native vision process is started. The v1
operator entry and the ordinary client remain separate.

## Start

First start the separately supplied private simulator from its directory containing
`rm_match/`, using its existing MuJoCo environment:

```sh
MUJOCO_GL=egl python -m rm_match.platform_v2 --camera --port 8766 --camera-total-fps 2
```

EGL is the Linux qualification path, not a tested Windows launch recommendation.
The server remains bound to `127.0.0.1`. Then, from this client's root:

```sh
python main.py --sim-platform-v2 --sim-role spectator --sim-endpoint http://127.0.0.1:8766
python main.py --sim-platform-v2 --sim-role operator --sim-endpoint http://127.0.0.1:8766
```

Without `--sim-role`, the v2 role is spectator. `--sim-platform-v2` is mandatory;
malformed simulator flags are rejected rather than falling through to hardware.
The v1 invocation remains `python main.py --mujoco-operator ...`.

Use the existing Jello RobotProfile selection to choose the watched robot.
Watching does not claim control. A spectator cannot move, aim or fire. For an
operator with a valid watched frame and state, the first F2 requests the motion,
gimbal and fire leases; after acknowledgement, a second F2 arms input. WASD,
mouse and left click then use the existing input path. F12, lost focus, stale
state/frame, missing UI heartbeat or a transport failure disables input. Switching
watched robots releases old control, clears old pixels and requires new explicit
claim/arm actions. Regaining focus never rearms automatically.

## Camera and timing boundaries

- Full 1440×1080 camera images are decoded for display; JPEG header dimensions,
  actual decoded size, calibration shape, own exposure state, episode, robot,
  watch generation and monotonic sequence are checked.
- Capture age includes a conservative request/transfer/decode allowance. Re-reading
  the same frame cannot refresh its capture time. Expiry is checked again after
  decoding; decoding cannot make an old frame appear fresh.
- The display explicitly shows frame age and a low-FPS label. The current display
  expiry is 2.5 seconds. That is distinct from the algorithm's unchanged 0.2-second
  wall/simulation safety window. No autoaim deadline has been extended.
- The server keeps camera/model/judge objects private. The client only receives
  serializable state and camera messages. World-truth map data is display-only.
- Source camera K/D and role_v23 optics are preserved, but the resolution association
  missing from the original calibration YAML remains labelled ASSUMED.

## What was actually tested

This checkpoint ran the actual v2 server, real MuJoCo physics and a single Mesa
llvmpipe/EGL camera worker. Two original Qt windows consumed independent real
1440×1080 views. Qt painted its original overlay into QImage on the CPU; this is
not a native Windows/desktop-OpenGL display qualification. Keyboard and mouse
events in the qualification were synthetic.

The final 22 checks covered: spectator isolation, watch without claim, two-step
F2 activation, actual motion and gimbal change, explicit fire with judge ammo
accounting, focus-loss release/neutral force, no automatic rearm, switch-frame
invalidation, no implicit new claim, isolated physical services and server-loss
cleanup. The final motion sample moved about 0.082 m; sentry ammo changed 750 to
748. The visible final samples were approximately 0.23–0.92 seconds old, so this
is a low-frame-rate UI/control qualification, not real-time camera autoaim.

Client focused coverage: 26 tests (22 transport/window, 4 CLI). A further 38
relevant v1/HUD regression tests passed. Earlier simulator results are retained:
109 CPU tests and a 33-check real-camera qualification, with unchanged production
camera/runtime bytes in this integration stage. Two new no-GL tests ensure the
camera qualification cannot overwrite an existing output directory and refuses
Python `-O`.

## Reproduce the bounded integration

Use a new output directory; the tool refuses an existing one:

```sh
QT_QPA_PLATFORM=offscreen python tools/verify_sim_platform.py \
  --bridge-directory /path/to/authorized/simulator \
  --bridge-python /path/to/its/venv/bin/python \
  --out /path/to/new/qualification-output
```

The client Python environment needs its existing PySide6/OpenCV dependencies.
The simulator's Python environment needs its existing MuJoCo/NumPy/Pillow
requirements. The tool does not install software, connect paid compute or expose
an external listener. It captures the original Qt CPU-rendered overlay and writes
checks/metadata to the new directory. Keep generated simulation images and raw
logs private. No input image, detector model, binary or CAD is bundled here.

## Known gaps and next bounded steps

1. Hero and infantry start with zero ammo under the existing judge; v2 has not
   integrated the old v1 navigation-driven virtual resupply. An initial infantry
   firing check therefore correctly found 0 → 0. The successful fire test used
   the existing sentry allocation, without changing judge state or granting ammo.
2. Camera/CPU latency often exceeds the 0.2-second algorithm window. Live native
   autoaim, real-time tracking and multi-camera frame-rate guarantees remain
   unqualified. Improve/measure the pipeline while preserving that safety window.
3. The Qt UI still has no navigation/aim-adapter picker; those bindings remain
   available at the protocol/SDK boundary. This window is the manual/observer seam.
4. Only the existing six-role 3v3 HUD is supported here. New rosters, 7v7 and new
   competition rules require their own implementation and qualification.
5. Actual cross-machine LAN, identity provisioning/network hardening and Windows
   desktop/OpenGL behaviour have not been tested. No network setting was changed.
6. These are author integration results, not independent acceptance. Review the
   frozen incremental manifest and run a separate boundary review before release.
