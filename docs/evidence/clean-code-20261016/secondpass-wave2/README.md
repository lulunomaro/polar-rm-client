# Second-pass second integrated checkpoint

Completed 2026-10-16 11:00 UTC: **259 PASS, 12 SKIP, 3 INCOMPLETE, 0 FAIL**.
The complete `--profile offline` scope remains **NO-GO**, exit code 1.
All 258 offline-category checks and the separate settings-orphan audit pass;
the 15 firmware-category prerequisites remain missing. These categories are
reported separately and do not turn the whole selected scope into PASS.

- Exact clean source: `6ad0123c64121b49c6e5f6ec654b3a12ac039aa2`
- Git tree: `860cf7f5c0e3af3f9c8e262041887afd8a807d6b`
- Run ID: `064ff796054d44ceab7d812d52fc900e`
- 583 declared source inputs have identical before/after SHA-256
  `c980f1dd4a95682d282f851a503a024b51318e72a4c08bad8b8dc1df741e0fd4`.

This adds the independently reviewed serial-discovery admission fix, three
lowered responsibility budgets, an explicit deferred-inclusive cycle guard,
and the narrow pure-domain backend/GUI boundary to the first integrated source.
Quality negative controls, independent review artifacts and precise historical
scope accounting are also retained. Tactical-map work is not in this source.
`integration-additions.json` lists every intervening local commit and original
source candidate.

The exact documented independent team CRC file is explicitly selected in this
run. Its SHA-256 `f46cce6382bbbae2056b81538c1de5c06a93c2199b4d07784d9bc551509ed2f5`
was verified before and after; the polar-link check now passes all 81 assertions.
See reference-manifest.json and the two reference hash files. The external file
is not copied into the repository. This closes the previously unprovided or
unselected CRC input only; it does not substitute generic firmware, modify a
selected firmware tree, or overwrite the first run's original INCOMPLETE.

The remaining 12 SKIP and 3 INCOMPLETE concern missing firmware/independent
reference, an official-client dump and a patch-installation prerequisite.
Each actual result is retained in results.json. Native desktop GPU, Windows
packaging, physical audio/serial/camera/robot and field qualification are separate
unperformed scopes; they are not the cause of these fifteen counts.

Static source SHA-256 is
`b8de156ae6c5277c371621bf8360a29f0aa66b05952b287da15dd4d228941a55`:
204 modules, 3342 functions, 36968 statements, 13003 decisions, zero initialization
and zero deferred-inclusive cycles. The serial correction adds 11 statements
and 4 decisions compared with c4d3bcf; the three reviewed hotspot caps are
unchanged. Current gates pass. Counts are review signals, not proof that all
project complexity or all lifecycle risks have been removed.

The full result, exact command, runtime provenance, complete stdout/stderr and
static reports are preserved. SHA256SUMS hashes every other file in this folder.
Raw output bytes, including existing trailing whitespace, are intentionally
retained. This evidence-only commit comes after the tested source; it must not
be substituted for the clean tested identity above.

Reproduce using the pinned environment in docs/quality_gates.md, the verified
external reference manifest and the exact command in summary.json. The source
fingerprint excludes external fixtures, installed wheels, assets and transient
reverted edits; the separate CRC before/after checks narrow only that one gap.
