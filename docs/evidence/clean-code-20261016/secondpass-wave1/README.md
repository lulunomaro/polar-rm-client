# Second-pass first integrated checkpoint

Completed 2026-10-16 10:44 UTC: **256 PASS, 12 SKIP, 4 INCOMPLETE, 0 FAIL**.
The selected scope remains **NO-GO**, exit code 1. This is a complete run of
`--profile offline`, not an accumulation of author-focused runs.

- Exact clean source: `c4d3bcf97ef43056bf316bbec593802652120878`
- Git tree: `7133ab90ca589f2b9afaf6e141186b42f5b6d6c1`
- Run ID: `bbb64591f0d44151b67bd7cccb7fb0b0`
- Declared source inputs: 581 files, identical before/after SHA-256
  `ff2ce877206599388c61ae45f9fcb5d25c75417089f9a7edbe8148c09bac8404`

The 271 selected registered checks produce 255 offline PASS and 1 offline
INCOMPLETE, plus 12 firmware SKIP and 3 firmware INCOMPLETE. The additional
settings-orphan audit passes, giving 272 reported results. The offline
INCOMPLETE is the existing missing team CRC reference (79 other assertions
pass). This same reference gap already appeared in wave13; it is not a new
loss caused by environment refresh. The remaining missing firmware, independent
reference, official-client dump and patch-installation prerequisites are also
reported individually, not counted as passes. No substitute firmware was chosen.

`integration-manifest.json` records each original reviewed candidate chain and
the resulting integrated commits. Domain, window assembly and normal timeline,
frame-layer composition, economy content/labels, verification infrastructure,
and angle/audit admission fixes are included. Serial discovery admission and
later quality ratchets are not included in this first source identity.

`results.json` preserves the complete machine report and environment provenance;
`console.log` and `logs/` preserve all output. `summary.json` records the exact
command, exit status and input scope. `SHA256SUMS` hashes every other evidence
file. These are evidence-only additions after the tested clean commit, so their
commit must not be substituted for the actual tested source identity.

The exact static report is `architecture.json`; its summary records 204 modules,
3342 functions, 36957 statements, 12999 decisions, 0 initialization cycles and
0 cycles including deferred imports. This newly closes both remaining static
cycles compared with wave13. Existing gates pass. This does not establish
runtime dynamic-import coverage, native GL, physical devices or field safety.
The three smaller hotspot measurements are proposals only at this checkpoint;
no baseline was changed during the run.

Reproduce with the hash-pinned Linux/CPython 3.12 dependencies described in
`docs/quality_gates.md` and the command in `summary.json`. Keep output paths
outside production/test source. The report's start/end guard does not cover
assets, installed wheels, external fixtures or transient reverted source edits.
