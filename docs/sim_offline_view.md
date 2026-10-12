# Explicit OFFLINE / SIL spectator view

The original opt-in v2 window recognizes `clock_profile.kind =
"offline_modeled_latency_v1"` and labels the modeled clock. The existing live and
v1 entries remain separate. Camera pixels are received from the server; the UI
never replaces exposure time with receipt or rendering time. A displayed camera
wall age or display FPS is not the algorithm's modeled simulation age.

This checkpoint is a **read-only original-Qt viewing seam**. The bounded test
entry uses a separate SDK operator session to claim only the algorithm's gimbal
lease and explicitly renew it. The spectator never claims or sends input. It is
not an interactive offline driving interface or a full adapter-selection UI.
The first offline server supports one stationary vehicle with manual fire off.

## Reproduce the bounded qualification

From this client checkout, with PySide6, NumPy and OpenCV installed:

```sh
QT_QPA_PLATFORM=offscreen python tools/verify_offline_platform.py \
  --bridge-root /path/to/authorized/mujoco_match \
  --bridge-python /path/to/authorized/simulator/python \
  --scene-json /path/to/authorized/physics_replay.json \
  --native-spec /path/to/authorized/native_spec.json \
  --out /path/to/new/evidence
```

The simulator checkout must include the separate offline server entry. Supply
locally authorized scene and native artifacts; this public client contains
neither assets nor recorded scene data. `--out` must not exist. Python `-O` is
refused. The driver runs the preregistered two host-delay cases and never retries
a failed case or tunes parameters. It records actual Qt CPU rendering and server
receipts. This does not qualify desktop GPU rendering, Windows native inference,
real-time operation, physical hardware, arbitrary vehicles or a complete match.

The visible OFFLINE label currently overlaps the roster header in the bounded
CPU screenshot layout. Evidence is retained as painted; a layout adjustment is
separate work. Existing MC/CS/Jello components have not been restyled.

CPU checks:

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=.:tests python -m unittest \
  test_offline_platform_view test_sim_platform_v2 test_sim_platform_entry \
  test_api_freeze_platform_regressions -v
```

## Known lifecycle evidence gap

The bounded driver records renewal exception strings without their event times.
A renewal `Uncertain` entry therefore cannot be attributed to shutdown or another
phase from that log alone. Integration receipts establish the lease checks at
actual consumption, but this checkpoint does not establish an error-free network
renewal/shutdown lifecycle. Preserve the original records. Timestamped lifecycle
logging and close-order tests belong to a separate fix, not edited evidence.
