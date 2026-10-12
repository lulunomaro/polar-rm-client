# SIM driving reference frames

This optional capability changes how WASD is interpreted. It does not implement automatic chassis-heading follow or a world-stabilized gimbal. In SIM, ChassisAssist's Follow setting is labelled **SIM drive frame**. Power/Buffer protections remain unavailable. Real-device controls are unchanged.

- Free: W follows the current physical chassis world yaw; A points left.
- Field: W is world +X and A is world +Y, including blue vehicles.
- Gimbal: W follows physical chassis yaw plus relative gimbal yaw.

Yaw is radians, counter-clockwise positive viewed from above. The server transforms inputs using its current own-robot state, not delayed client HUD coordinates. `drive_axes` contains `forward_mps` and `left_mps` (each bounded to 1; diagonals normalized). Legacy `vx_mps/vy_mps` remain chassis-body values and must be zero when the extension is present. Non-Free modes require axes, including zero release messages. Capability `chassis_drive_frame_version=1` and `chassis_drive_modes` advertise the extension separately from legacy chassis-follow support.

Enable ChassisAssist in SIM and choose the reference. Changing mode clears input and disarms; release acknowledgement and an explicit F2 are required before driving again. Selecting a vehicle clears this module too. Lost focus, timeout, invalid state and old feedback cannot confirm an active reference. Source aim's movement gate consumes these axes.

Drive feedback reports requested reference, target world velocity, and timestamped sampled velocity/actuator force. After release, a previously sampled nonzero force may still be shown with its sample time. This is not a promise that a future physics tick has already stopped the vehicle. Active feedback also requires the current control safety revision.

## Reproduce

Use the project's Qt test environment with `PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest tests.test_sim_drive_frames`. The bounded regression checkpoint includes 318 client tests. No desktop GPU or original-window directional drive qualification is claimed in this checkpoint.

With a separately installed compatible simulation checkout and its Python dependencies, run:

```
python tools/verify_sim_drive_contract.py --bridge-root /path/to/simulator --client-root .
```

This runs real no-GL simulation snapshots through the client parser for Free/Gimbal/Field. It is a protocol roundtrip, not a full match or visual qualification. To check an older client retaining only Free controls, pass its checkout as `--client-root` and add `--legacy`. Do not use Python optimization (`-O`). No private simulation recordings are included.
