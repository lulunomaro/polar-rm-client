# SIM v2 status display

This presentation stage retains the original MC/CS HUD and Jello ModList. It does not change motion, fire, leases, algorithm inputs or server physics.

- **SPIN REQUEST**: local mode and target. A requested target is not measured speed.
- **ADMITTED[SNAP]**: the current fresh server state contains a matching owned input, original window and deadline. Local focus/capability loss clears this indication.
- **ACTIVE[SNAP]**: the existing strict receipt validator plus local safety/freshness gates agree at that server snapshot. This does not assert current mechanical rotation at display time.
- **LAST APPLIED / HIST**: a previously validated post-step sample, with its original lease/sequence, measured angular velocity, actual torque, saturation, simulation time, simulation age and local receive age. Repainting or receiving the same sample again does not reset its age. Disarm can retain explicitly historical data; reconnect/watch/episode changes clear it.
- Unknown measurements remain unknown. Stale receive age is labelled. Local receive age is not camera exposure age, and the client does not subtract clocks from different machines.

The status column avoids the actual CS layout and progress bar, original map area and ModList side. HUD-logical rectangles are scaled into viewport coordinates; MC rectangles are already viewport coordinates. Dense layouts with no safe rectangle omit the new column rather than covering another module. Supported normal dimensions are tested; arbitrary customized layouts are not a universal layout guarantee. An extra v2 paint invalidates stale SIM model validity before the original sidebar paints, even if the regular model heartbeat has not run yet. The mapped display model is stamped under the transport lock with its connection/watch/robot/episode and exact state-receive identity. A fresh but different robot or sample cannot validate the previously mapped sidebar.

## CPU checks

From the Polar client checkout root with existing test dependencies:

    QT_QPA_PLATFORM=offscreen PYTHONPATH=.:tests python -m unittest test_platform_status test_offline_platform_view -v

PowerShell users can set the two environment variables separately before running the same Python module command. These instructions do not constitute Windows qualification.

Optional synthetic-only layout tool (requires a new output directory):

    QT_QPA_PLATFORM=offscreen python tools/render_v2_spin_status.py --out synthetic-status-layout

It produces two explicitly labelled synthetic original-Qt CPU images. It does not connect to a server, render MuJoCo, or qualify camera/vehicle behavior. Real motion qualification remains separate. The tool uses the repository's synthetic test fixtures, so the tests directory must be present.
