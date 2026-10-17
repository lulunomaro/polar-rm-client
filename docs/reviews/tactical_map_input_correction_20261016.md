# Tactical-map input correction: separate red characterization

This is a separately authorized behavioral correction after the accepted
behavior-preserving map extraction chain ending at `184db55`. The immutable
`71ebb0f` source fixture and the accepted extraction branch remain unchanged.

## Reproduced before correction

`tests/test_tactical_map_input_ownership.py` uses an actual `PolarRMWindow`,
`QPainter` over a `QImage`, and real `QMouseEvent`/`QKeyEvent` objects. The test
captures the actual returned field rectangle and painter combined transform,
then chooses blank map points without registered hotspots. Only final control
or command sinks are intercepted, except the safety test which calls the real
backend publisher against a fake inert transport client.

- Liquid drawing never publishes the field layout, rectangle or mirroring.
  A fresh map click becomes a combat mouse press; switching from classic can
  instead reuse the previous map geometry and alliance orientation.
- The classic map consumes a successful press but does not own its release or
  drag. Publisher exceptions leave the old mouse-motion anchor replayable.
- Classic inverse mapping ignores the painter transform (HUD/animation scale
  and translation), whereas Qt mouse coordinates are logical pixels.
- Hotspots retain precedence, but their left release can escape to combat.
- A disabled map or a failed new paint can leave old classic click geometry.

The separately committed red run contains 11 methods, 30 failing subtests and
no test errors. Collapsed/closing map clicks and right-button routing retain
passing controls. The output is `docs/evidence/clean-code-20261016/tactical-map/input-red.log`.

Reproduce without the aggregate verifier:

```
XDG_CACHE_HOME=/tmp/map-contract-xdg QT_QPA_PLATFORM=offscreen \
  /workspace/shared/client-quality-venv/bin/python tests/test_tactical_map_input_ownership.py
```

The intended correction publishes the geometry actually drawn, consumes the
complete left-button sequence before fallible map effects, and preserves the
backend as the sole command permission/rate-limit authority. It does not
claim native GL hit/raster equivalence or a general overhaul of HUD hotspots.
