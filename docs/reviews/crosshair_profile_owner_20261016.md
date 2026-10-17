# Pure crosshair profile ownership

## Fixed scope

Baseline: `fa2c2b8397295d27ada5d105854b751b7dd0940d`.
Pre-extraction characterization: `6086155`, then catalog contract `a7bfbce`.
Production candidate: `12693ef`.

The inventory explicitly identifies the combined crosshair editor/parser as a
remaining configuration boundary. This change separates the domain schema and
lossless wire interchange from native UI and registry effects; it is not a
size-driven editor decomposition or a referee/transport rewrite.

`crosshair_profile` owns field limits, profile defaults, JSON/CFG validation and
four share-code formats. The old `cs2_crosshair` surface retains identity aliases
for every moved function and constant, including historical private codec names.
All twelve moved function ASTs match the pre-extraction source exactly. Qt editor,
painter, import dialogs, settings mutation, notification text and display scaling
remain in the adapter. The catalog now uses the same 23 numeric field ranges as
the validator, eliminating duplicate range declarations. Catalog defaults retain
their original concrete number types, step sizes, ordering and setting identity.

The domain and catalog execute with adapter, UI, Qt, model, backend and serial
imports blocked. Both domain consumers use the same schema without constructing
an editor or registry through an application window. The prior adapter was also
lazy about Qt imports; this change does not claim it previously eagerly loaded Qt.

## Evidence

Evidence directory: `docs/evidence/clean-code-20261016/crosshair-profile/`.
`focused-tests.log` records 80 passing tests at the candidate source:

```
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen /workspace/shared/client-clean-venv/bin/python -m unittest test_crosshair_profile_contract test_crosshair_profile_boundaries test_cs2_crosshair_import test_default_catalog_characterization test_module_catalog_boundaries test_cs_crosshair_target_bounds test_cs_crosshair_visibility test_inventory_text_crosshair_scale -v
```

The ten pre-extraction contracts passed before any production replacement.
They retain factual public format vectors, all numeric/boolean bounds, malformed
and conflicting JSON/CFG, UTF-8 size admission, 240 deterministic wire mutations,
reserved-bit round trips, exact exception messages, independent results, complete
settings preflight and setter failure prefixes. Three subsequent boundary tests
check aliases, unchanged algorithm ASTs and blocked-runtime imports.
Existing Qt tests cover editor actions, import/export, raster visibility and size;
existing catalog fixtures remain unchanged. `api-freeze.log` passes the actual
window API and historical snapshot. `static.log` and compact `static.json` record
the unchanged static gate and measured entire production tree. Full-range
`git diff --check fa2c2b8..HEAD` is clean.

Whole-tree accounting: modules 208 -> 209, functions 3368 -> 3368, statements
37123 -> 37123, decisions 13069 -> 13069, import edges 598 -> 600. Both immediate
and all-import cycle graphs remain empty. The schema is moved, not counted as a
complexity reduction; the concrete benefit is ownership plus shared limits.
The full frozen adapter is test-only historical source, never production code.

## Limits

No claim of native GPU, packaging, CS2 engine fidelity or physical telemetry
qualification. Existing static approximation and lossy-conversion refusals stay
unchanged. Profile application retains ordered partial writes when a setting
setter raises; this is not a new atomic transaction. Public call signatures,
results and shared constant objects are preserved; rebinding facade globals to
monkeypatch internal codec-to-codec dispatch is not an established contract.
The editor's large UI construction function remains cohesive native work and is
not decomposed by this change. No new control commands, access or data sources.
