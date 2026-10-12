# Choosing a simulation clock and starting the client

## Live manual operation (existing default v2 profile)

Start the separate authorized simulator checkout in its Python environment:

```sh
python -m rm_match.platform_v2 --camera --port 8766 --camera-total-fps 2
```

Then, from the client checkout:

```sh
python main.py --sim-platform-v2 --sim-role operator --sim-endpoint http://127.0.0.1:8766
```

Choose the watched vehicle in the existing UI. Watching does not claim control.
After a current camera/state arrives, press F2 to request the lease, then press F2
again to arm. WASD moves, the mouse controls the gimbal, and the explicit firing
input is manual. F12 releases. Focus loss, stale state or camera and disconnect
release input. `--sim-role spectator` is read-only and cannot claim or fire.

This profile uses real monotonic wall leases and deadlines. CPU rendering may be
slow. A slow host does not extend the native algorithm's wall budget. The bounded
live native camera attempt did not pass that budget; do not describe it as a
qualified real-time autoaim system. Existing manual operation remains separate.

## Explicit offline/SIL modeled-time qualification

The offline profile is a separate startup path. It advances actual MuJoCo steps
on an integer simulation-time grid and pauses physics during computation.
Configured sensor/compute delays are engineering values, not measured hardware
latency. Wall-clock lease, stop and watchdog behavior still operates while the
simulation is paused. It never silently replaces the live profile.

```sh
QT_QPA_PLATFORM=offscreen python tools/verify_offline_platform.py \
  --bridge-root /path/to/authorized/mujoco_match \
  --bridge-python /path/to/authorized/simulator/python \
  --scene-json /path/to/authorized/physics_replay.json \
  --native-spec /path/to/authorized/native_spec.json \
  --out /path/to/new/evidence
```

This bounded driver supplies an explicit SDK algorithm-only gimbal lease and
opens the original Qt window as an **offline spectator**. It records CPU-painted
screenshots; it is not a keyboard-driving interface for offline mode. The first
scene is one stationary vehicle using the existing native camera profile. No
automatic fire is permitted. Scene/model/executor/configuration files must be
provided locally with the expected identities; none are downloaded by this tool.

For a separate Linux spectator window, use `main.py --sim-platform-v2 --sim-role
spectator --sim-endpoint` with the loopback URL printed by the server. The bounded
test's server lifetime is short; an unbounded interactive offline application is
not provided by this checkpoint.

## Renewal and shutdown records

The newer driver records each renewal request, success, failure or uncertainty
with wall time and a clearly labeled **last-known** simulator state and its
observation wall time. Missing state is null, not inferred. Shutdown requests the
renewal thread to stop and performs a bounded join before releasing or closing
the SDK session. A blocked worker prevents those concurrent SDK calls and yields
an explicit incomplete cleanup result. Prior renewal failures remain visible
regardless of successful cleanup.

After neutralizing the simulation, the bounded server waits at most two seconds
for a local driver acknowledgement proving joined renewal and session closure.
No acknowledgement or malformed/incomplete acknowledgement is marked
unconfirmed. This is a local test-lifecycle handshake, not an actuator credential
or a malicious-process security boundary. The original untimestamped paired-run
records remain unchanged; this improvement has only mock and real-loopback
no-GL qualification until a future explicitly scoped rendering run.

## Platform boundary

All commands above are loopback-only. No cross-device LAN port, firewall rule or
long-lived credential is configured. Linux CPU/offscreen qualification does not
qualify Windows native inference, desktop GPU rendering or real electronics.
The GUI may be run on Windows with its own supported dependencies, but this
Linux native binary cannot be represented as a Windows executable. Device
hardware is not constructed by these explicit simulation entries.
