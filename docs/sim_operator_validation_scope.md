# Operator validation scope

## Bounded same-session source/FOV check

A frozen client using cumulative mouse-angle input was checked against a frozen
simulation source adapter configured with its existing qualified optical profile.
The original Qt input and module paths completed one continuous, explicitly static
visible-target scene with normal physics:

- Original mouse input was acknowledged exactly and the mechanical gimbal settled.
- A real source detection was rejected by a narrow range, then admitted after a
  same-session change to a wider range and explicit rearm. The client displayed
  source authority only from fresh, identity/revision-qualified feedback.
- Right-button release removed source authority. Manual firing authority remained
  separate; automatic aiming did not grant permission to fire.
- Original spin controls produced observed CCW motion, increased measured speed,
  CW motion and braking when disabled.
- Focus loss released control; disconnect did not resume old inputs.

This closes the previously open **bounded static-scene workflow** after two
integration corrections: cumulative mouse angles replaced mixed-clock rate
conversion, and the source adapter selected its already qualified optical profile.
No detector threshold was reduced to make the scene pass. Earlier failed attempts
remain valid failure evidence; they are not converted into passing results.

It does not establish moving-source robustness, arbitrary robot support, a
complete competitive match, full ModList parity or desktop/GPU presentation.
The source adapter's selected-robot/stationary-only capability restrictions still
apply. AutoSpinOnHit physical-collision acceptance is a separate check.

## CPU screenshot and visual boundaries

The screenshots use the actual original `PolarRMWindow` HUD painted to a CPU
`QImage`, together with real first-person simulator camera pixels. They are not
mockups, but they are also not proof of desktop/OpenGL behavior.

Some CPU paint/save operations exceed the normal UI input watchdog. The resulting
safe disarm is retained, and checks that require a new input lease use explicit
F2 rearm. A snapshot read after saving a picture must not be labelled as the exact
state or camera frame at the start of its painting. Independent control/telemetry
checkpoints establish functional acceptance; image/metadata timing is recorded
separately.

The offscreen environment shows missing/fragmented Chinese glyphs and bitmap-text
artifacts in parts of the legacy HUD. Those are known visual defects awaiting
font/render-path investigation, not accepted final visual quality. Original images
are retained without retouching or hiding these defects.

Actual images, source logs, local launch paths and scene records remain private.
Only source, generic validation instructions and synthetic protocol examples are
intended for the public client repository. No new CI or desktop acceptance is
implied by this document.
