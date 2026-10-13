# Routed manual chassis yaw in platform v2

This bounded change connects the original keyboard command's chassis-yaw slot to
strict `server_window_v1` manual motion. It is a CPU/input-protocol checkpoint;
it has not added a physical-yaw, rendered-image, desktop, Windows or LAN test.

## Keys, units and ownership

The original control builder computes the unit axis `E-slot - Q-slot`. Here that
means E-slot = +1 rad/s, Q-slot = -1 rad/s, both held = 0 rad/s, in `base_link` with
positive yaw counterclockwise about +Z. This is an explicit SIM convention, not
real hardware calibration or a promise of mechanical angular speed. Keybinds can
map other physical keys to these protocol slots.

Original UI routing has priority: physical Q may open the quick-action wheel and
E may open the Minecraft inventory. Those events are not stolen for turning.
When a GUI panel takes keyboard ownership, v2 immediately disarms, clears held
inputs and schedules lease release. Close the panel and use the normal explicit
F2 claim/arm sequence again. A remote packet already in flight is still bounded
by its original server deadline; this does not promise instantaneous network
revocation. A zero yaw request can apply braking, whereas disarm releases motion.

Navigation owns the entire motion channel while bound. Manual translation and
yaw are both excluded; pressing yaw keys never overrides or unbinds navigation.
An aim-only binding owns gimbal, leaving manual motion available. Fire remains
an independent manually controlled channel.

## Fresh samples and compatibility

`PlatformInputBridge` is v2-only. It forwards the routed axis through the separate
`chassis_yaw_rps` argument. The old `yaw_rate_rps` gimbal-rate argument is not
repurposed. Accepted axes are exact finite numbers in [-1, +1]; no silent clamp.
The unchanged server manual-motion bound remains +/-4 rad/s, and navigation's
separate action bound is not widened.

Strict motion packets obtain `wz_rps` from a complete input sample taken after the
window acknowledgement. Mouse deltas alone cannot make cached yaw fresh. New
windows, focus loss, disconnect, watch changes and rearm retain the existing
sequence/generation/deadline barriers. Initial, neutral and activated intent has
zero yaw. Invalid bridge input disarms without falling back to physical egress.

The explicit legacy-v2 control path still sends zero chassis yaw. The original
v1 input bridge, keyboard builder and shared input dispatcher are byte-unchanged.
No serial, DT7, MQTT or physical-device command path is added.

## ModList boundary

X / SpinMode remains unavailable in v2 and must not display active spin. A stray
spin flag at this bridge disarms rather than being clamped into manual yaw.
The original 120–720 degrees/s spin settings need their own capability/schema,
feedback and actual dynamics qualification. The eventual arbitration proposal is
spin overriding (not adding to) manual yaw, with explicitly defined translation
behavior. This checkpoint does not implement that proposal. C reference modes,
AutoSpinOnHit and original V/right-button aim hold also remain separate work.

## Reproduce focused tests

From the Polar client checkout root, with existing test dependencies available:

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=.:tests python -m unittest \
  test_platform_manual_yaw test_platform_manual_yaw_ui -v
```

The first suite uses fake SDK/state/camera fixtures. The second calls original Qt
key/focus routing with an inert SDK/backend and synthetic camera eligibility;
it does not show or paint a window and does not run MuJoCo, native aim or a
renderer. The tests cover rebindings, Q/E GUI priority, release inside a GUI,
focus loss, F2 inside inventory, fresh post-ACK input and unavailable X behavior.
