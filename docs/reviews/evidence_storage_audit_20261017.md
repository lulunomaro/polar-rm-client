# Evidence storage audit, 2026-10-17

Audited evidence at commit `6b661c056cf2cc3501386755a71b72bd6bc0f90f`,
against base `b26b7b718e348fbda066412f6d86140727f32e73`.
This is a storage and reference audit, not certification of historical test
results or a reason to rewrite production code.

## Verified inventory and integrity

- The base-to-tip evidence delta is 2,672 added files and 509,136 added text
  lines. The current tree has 2,686 tracked evidence files containing
  18,758,773 bytes (approximately 24 MiB allocated on this filesystem).
- It contains 2,366 `.log`, 108 `.txt`, 83 `.json`, 48 `.md`, 27 `.gz`, and
  21 `.py` files, plus checksum files. JSON contributes 12,711,661 bytes;
  the two second-pass wave 1/2 `architecture.json` reports total 6,410,116 bytes.
- SHA-256 content inventory found 1,325 unique file contents. Identical bytes
  under different run paths do not make those run records disposable.
- All 1,797 entries in `*SHA256SUMS` resolved and matched. Paths were resolved
  relative to each manifest directory or, for repository-relative entries,
  the repository root.
- Each existing raw archive was read without extraction. Its regular-file
  member names, lengths, and SHA-256 hashes exactly matched its raw manifest,
  with no missing or extra regular-file members:
  - `clean-code-20261017/rejected-stage2/raw-records.tar.gz`: 654 members
  - `clean-code-20261017/pre-correction/raw-records.tar.gz`: 630 members
  - `clean-code-20261016/continued-integration/raw-runs.tar.gz`: 1,755 members
  - `clean-code-20261016/completed-checkpoint/raw-runs.tar.gz`: 1,234 members
  - The 20261017 manifests use `size`; the 20261016 manifests use `bytes`.

Inventory used `git diff --numstat <base> -- docs/evidence`,
`git ls-files -z docs/evidence`, and Python standard-library byte hashing,
JSON parsing, and `tarfile` member reads. No historical scripts were executed.
These checks establish consistency with stored hashes, not independent
authenticity, correctness of test claims, or absence of sensitive information.

## References that consolidation must preserve

No direct `docs/evidence` runtime consumer was found in `rm_client`, `main.py`,
or `tools`. However, historical evidence includes executable review scripts:

- `configuration_quiescence_independent_20261016.md:36` invokes
  `clean-code-20261016/quiescence-independent/review.py`.
- `crosshair_profile_independent_20261016.md:26` invokes
  `clean-code-20261016/crosshair-independent/review.py`.
- `focus_loss_independent_20261016.md:37` invokes
  `clean-code-20261016/focus-loss-independent/review.py`.
- `economy_view_boundary_audit_20261016.md:57` and
  `economy_hold_duration_20261016.md:31` invoke
  `clean-code-20261016/economy-grid-audit/review.py`.
- `constructor_matrix_root_review_20261017.md:8` invokes
  `clean-code-20261017/constructor-admission-matrix/mutation_controls.py`.
- Evidence-directory READMEs for `shader-independent-review-20261016:29` and
  `renderer-independent-review-20261016:13` invoke adjacent `review.py` files.

Review document paths above are relative to `docs/reviews`; script paths are
relative to `docs/evidence`. Shader and renderer scripts derive the repository
root with `__file__.parents[3]` at lines 7 and 4 respectively, so relocation can
break imports. Crosshair review line 5 also requires Git history at `fa2c2b8`.
There are additional human-readable evidence references in review documents;
the list above highlights executable commands, not every prose reference.

`tools/test_registry.py:88-100` discovers tests only in `tests` and `tools`.
`tools/verification_sources.py:12-23` excludes `docs` from source fingerprints.
`tests/test_verification_sources.py:76-83` verifies this with a synthetic
evidence path; `tests/test_repository_output_hygiene.py:12-36` verifies that a
synthetic evidence JSON path is not ignored. Neither reads historical logs.
Passing these checks alone cannot prove evidence preservation.

`tools/build_release.ps1:143-158` copies or archives the source tree, so evidence
affects staging size. `tools/POLAR-RM.spec:15-18` does not directly select this
directory as runtime data. The existing whole-source `git archive` operation
is not a dedicated evidence packing or verification utility.

## Lossless preservation plan

1. Prefer a small evidence index first: source commit, command, environment,
   conclusion, limitations, and accepted/rejected/superseded status. File count
   alone does not justify deleting evidence or rewriting source.
2. If consolidation is approved, keep the 21 Python scripts, their documented
   paths, human-readable reports, and essential result summaries initially.
   Pack selected bulky raw outputs by audit/run boundary using the existing
   archive-plus-manifest convention. Retain failed and rejected runs.
3. Preserve original bytes and relative member names. Do not normalize logs,
   replace raw evidence with summaries, or collapse distinct run names without
   a reversible mapping. Preserve existing checksums as historical records.
4. Add a migration manifest mapping every original path to its retained path
   or archive member, with original byte length/hash, archive hash, and source
   commit. Update links and provide exact extraction instructions.
5. Before removing any originals, prove full path/content reconstruction in a
   temporary destination. Validate hashes, no missing/extra/duplicate members,
   and safe member paths; reject traversal, absolute paths, and links. Test
   corruption rejection and deterministic archive generation. Recheck document
   links, source fingerprints, registry inventory, and the two tests above.
6. Handle new raw-log publication separately under its required authorization.
   Existing published evidence does not authorize adding private logs.

New packing commits can reduce checkout clutter and future review noise; they
do not remove existing blobs from Git history. Repository history rewriting is
not proposed. `CONTRIBUTING.md:115-127` and
`docs/packaging_hygiene_audit_20261017.md:8-23` already distinguish retained
evidence from disposable runtime output.

## Changes made

Only this audit document was added. No evidence was deleted, moved, rewritten,
or uploaded, and no mass cleanup was performed. No new raw logs or archives
were added. No production or test files were changed by this audit.

## Review-display addendum, 2026-10-17

The later review-hygiene change adds only this `.gitattributes` rule:

```gitattributes
docs/evidence/**/*.log linguist-generated=true
```

[GitHub's official documentation](https://docs.github.com/en/repositories/working-with-files/managing-files/customizing-how-changed-files-appear-on-github)
describes generated-file attributes as hiding matching diffs by default and
excluding them from language statistics. This narrows default review noise to
execution logs; it is not a code-quality improvement, file-count reduction or
verification of the reported 3,251-file PR total. GitHub's rendered PR UI was
not inspected here, so its actual presentation remains unobserved.

Local `git check-attr -z --stdin linguist-generated` over every tracked
`docs/evidence` path confirmed: all 2,366 `.log` files are `true`; all other
320 files are `unspecified`, including executable `.py` reviews, `.md` reports,
`.json` reports/manifests and checksum records. Sample checks also confirmed
`assets/minecraft_hud/font_default.json` retains `text: unset` and
`assets/minecraft_hud/UNIFONT_LICENSE.txt` retains both `text: unset` and
`whitespace: unset`; neither receives the generated marker.

Only `.gitattributes` and this addendum change in this step. Existing font rules
are preserved. No evidence bytes, paths, archives or history are changed, no
logs are uploaded, and no executable review or structured report is marked
by this rule. Existing evidence remains available at its original paths. The
lossless-consolidation plan above remains a proposal, not an executed migration.
