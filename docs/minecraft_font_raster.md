# Preserve native pixel-font detail through the HUD cache

The bundled Unihex CJK glyphs have 16 raster rows, displayed at 8 logical pixels.
The styled font already builds a 2x-density image correctly. The combat HUD then
painted that image into a 1x logical cache, discarding alternate rows/columns,
before upscaling it for the screen. Missing strokes were therefore a rendering
loss rather than absent font assets. The old path can be reproduced directly by
the pixel regression test.

The intermediate HUD image now retains density 2 with `devicePixelRatio=2`.
Logical dimensions, position, text advances, hit targets and final destination
rectangle do not change. Original bitmap/Unihex assets, nearest-neighbour
sampling, colors and MC+CS/Jello design remain intact. This applies to the common
HUD renderer in both device and SIM presentation; it does not change any input
or device behavior.

The focused tests compare every native foreground pixel for representative CJK
characters in the actual scoreboard cache, the SIM heat label and the final 2x
screen composition. They also reproduce the old lossy path, check ASCII/hotbar
nearest sampling, fractional GUI scale, destination DPR, resize/cache reuse and
the existing telemetry refresh limit.

```sh
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest \
  tests.test_minecraft_font_raster tests.test_hud_display_locale \
  tests.top_match_bar_minecraft_fusion_test
```

A separate original-window CPU A/B used the same captured real simulator camera
and frozen telemetry, remaining disarmed. The source-rendered screenshots show
restored sidebar and thermal/power-label strokes; they were not retouched. This
qualifies the CPU raster path, not user-desktop/OpenGL/GPU performance or every
font in every UI panel. Small output sizes may still undersample a 16-row glyph
at the final screen boundary; the avoidable intermediate loss is removed.

## Cache and performance boundary

The denser image requires four times as many cached pixel bytes. For example, a
454x300 logical ARGB32 cache requires 544,800 bytes at density 1 and 2,179,200 bytes
at density 2, by dimension/format calculation. Cache reuse, the 30 Hz telemetry
refresh gate and priority invalidations are unchanged and tested; this does not
introduce per-frame rebuilding. The extra raster data can increase CPU cost.
GPU allocation/upload cost remains unmeasured and must be included in future
desktop qualification. Environment-specific performance records remain private.
