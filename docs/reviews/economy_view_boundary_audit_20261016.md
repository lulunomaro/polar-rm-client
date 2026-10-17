# Read-only economy view boundary audit

Reviewed combined candidate `66deab1d34ea56e0e9ce07812ef6b565491b704f`.
The seven economy source files examined are byte-identical to the review checkout;
no production change or root/integration-worktree modification was made.

## Findings

1. `economy_content.py` is still a drawing-only port. Its supplied immutable
   offers, context and confirmation values do not grant transaction authority.
   The callbacks expose drawing/font/locale capabilities, not window mutation.
2. Adapter paint/build methods still mutate state deliberately and visibly:
   `draw_economy_overlay_compat` normalizes a missing category and calls explicit
   selection reconciliation; `build_gpu_economy_workspace` does likewise;
   `cs2_buy_menu.build_grid` reconciles selection and clamps both scroll models.
   Hover animation advancement, hotspot replacement and clip publication also
   remain adapter responsibilities. `draw_economy_overlay_legacy` clamps its
   historical selection index but has no current production call site.
   These are residual coupling, not newly discovered authority defects. Moving
   them to helper(window) or silently removing reconciliation is unjustified.
3. Actual confirmation projection is duplicated in the CS2 grid at
   `cs2_buy_menu.py:204-218`. It repeats eligibility/pending handling, hold timing,
   armed expiry and text logic already owned by `EconomyConfirmationState`.
   The grid hardcodes `.6`; other confirmation paths use `HOLD_SECONDS`.
4. The two projections are not identical contracts. Grid displays pending before
   no-selection; the shared presenter displays no-selection first. Grid reads
   hold fields only for an eligible hold offer and reads expiry twice only when
   the matching armed key is unexpired. An eager generic snapshot would add live
   observations on pending/disabled/empty paths. Existing compat held-fill behavior
   is also intentionally distinct from grid progress rendering.
5. No new transport-authorization bypass was found. UI enabled/hit visibility is
   presentation admission; `submit_economy_offer` still rechecks availability and
   pending status, and request/bridge/backend paths retain their own authority.
   Those checks are layered defenses, not duplicates to remove for style.

## Characterization and bounded next step

The retained script extracts the exact current confirmation block by AST without
editing it, then exercises 320 cases across selection, availability, status-only,
pending, confirmation mode, key match and timing. It verifies exact text, enabled,
action and progress output plus lazy field-read order in every case. Eighty
combinations intentionally differ from the shared presenter solely due to
pending/no-selection priority. The script is a current-source characterization,
not a claim of immutable historical-fixture coverage for a future rewrite.

A narrow justified future change is to make the grid use the shared hold-duration
constant, with a changed-duration negative control proving display, interaction
and threshold alignment. A broader consolidation should first freeze grid command
geometry/hotspots and failure/read prefixes, then explicitly preserve the two
presentation priorities and compat held-fill rules. This audit does not authorize
or implement that change. No functional defect currently requires moving all
paint-time reconciliation or rewriting the renderer.

## Reproduce

```
PYTHONPATH=. python docs/evidence/clean-code-20261016/economy-grid-audit/review.py
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest test_cs2_buy_matrix test_economy_content test_economy_presentation test_economy_selection test_economy_interaction test_economy_confirmation
```

Evidence is under the directory above. This is scoped synthetic/offscreen testing
of identical economy sources, not a whole-integrated-tree run, native pixel/GPU
qualification, native focus validation or physical transaction acceptance.

Results: all 320 projection/read-order cases and 44 focused test methods PASS.
