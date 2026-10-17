# Map input ownership recovery checkpoint

The author was interrupted before committing the green candidate. The parent reviewed the production diff against red commit a0214bf and ran the candidate on 2026-10-16 after recovery.

- 18 focused methods pass: real offscreen Qt window input routing and detached hit geometry.
- 32 adjacent methods pass: fixed-reference raster/command/failure-prefix contracts, geometry and telemetry.
- Actual tools/quality_gate.py passes; git diff --check passes.
- The original frozen renderer remains unchanged. Liquid field metadata intentionally changes from stale constructor values to the actual painted reference rectangle/layout/mirroring. Raster commands, pixels and other contract outputs remain compared exactly.
- Left click on an expanded painted field owns drag and release before any fallible dispatch/publication. Hotspots retain dispatch priority. Backend safety/capability checks remain unchanged, and rejected sends remain consumed.
- MapMarker.enabled retains its existing behavior; this patch does not introduce a new authority gate.
- These are Linux offscreen tests with inert final control sinks, not native GPU, Windows or physical robot qualification. Full integrated verification remains required.

The earlier python -m tools.quality.gates invocation does not execute the CLI and is not counted as evidence; quality-gate.txt is from tools/quality_gate.py.

Integrated verification exposed an extension-host regression: the early map
capture check assumed every input host had tactical_map_field_layout. Existing
ClickGUI-only hosts intentionally do not. The guard now treats absent layout as
no painted map, preserving their old input routing. No original assertion was
weakened. The pre-fix integrated FAIL is retained separately; 32 focused map and
ClickGUI methods pass after this one-line compatibility correction.
