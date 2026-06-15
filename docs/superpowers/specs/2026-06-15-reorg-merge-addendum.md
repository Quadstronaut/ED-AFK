# Reorg execution blueprint — merge addendum (post-council)

**Date:** 2026-06-15
**Council:** arch-tier, run `wf_805dc3c8-b39`. Decision: **merge_then_review**, base **gen-opus-1**.
**Base plan (verified):** `reorg_artifacts/PLAN-gen-opus-1.md` (arbiter re-ran its A1 on the
real tree → PASS, 0 violations/cycles; A2 7/7 on master).
**Lenses (executable gates):** `reorg_artifacts/reorg_import_graph.py` (A1, dep-cycle),
`reorg_artifacts/reorg_behavior_assert.py` (A2, behavior).

This addendum is the **delta** applied to the base plan. The base plan's D1–D5 stand as
written EXCEPT the three arbiter-mandated fixes below + the one fold-in.

## Fold-in (from gen-opus-2)
- **Step registry = fail-on-duplicate.** `register_step(name, fn, ...)` raises on a
  colliding name instead of `dict.update()` last-wins. Closes the SEC-5 / F6 silent
  step-replacement finding shared by every other candidate. (Today shared/jump/dock/explore
  step names are disjoint, so this never fires in normal use — it's a guard, not a
  constraint.) Do **not** adopt gen-opus-2's placement.json (non-portable: uses post-split
  logical module names absent on disk).

## Mandatory fix 1 — FR2: Step 4 is the dispatcher split ONLY (the big one)
Base-plan Step 4 git-mv'd ~20 files (steps.py, executor/{jump,navpanel}, fsd/*,
session_audit, data, config.toml, binds/, all TOMLs) **together with** the
dispatcher→boot_routes split, so a routing-regression revert would also undo every file
move. **Re-scope:**
- **New Step 3.5 — "ed-autojump file relocations"** (its own commit): all the autojump
  `git mv`s (steps.py, executor/jump+navpanel, fsd/*, session_audit, data/{galmap,
  fsd_modules,fuel_scoops}.json, config.toml, binds/, the 8 procedure TOMLs) + their import
  rewrites. Behavior unchanged; the dispatcher still imports its halves from the old
  locations via shims if needed. Gate: 16-red/1581-green.
- **Step 4 — dispatcher→boot_routes split ONLY** (its own isolated commit): extract the
  classifier ladder + dispatch table + route-complete helpers into
  `ed_autojump/flow/boot_routes.py`; engine half stays in `ed_core/flow/dispatcher.py`.
  Nothing else moves in this commit. **Now "revertable alone" is literally true** (spec §9).
  Gate: 16-red/1581-green + A2 `--src` green.

## Mandatory fix 2 — security: path-confine `register_procedure_dir`
Surface #4 must reject a path outside the workspace/installed-package roots before loading
TOML (the pre-reorg loader read a single fixed dir; the new surface widens it). Resolve the
path, assert it is under an allowed root (the package's own `procedures/` dir), else raise.
Low practical risk for a single-operator tool, but the surface is new vs baseline — guard it.

## Mandatory fix 3 — concurrency: document the carried invariants
State (not leave implicit) in `ed_core/flow/`:
- **F1** `_apply_state`'s deferred `from .steps import` runs under `_TailHub._lock`;
  CPython-GIL-safe today, a lock-ordering hazard only under 3.13 free-threading.
- **F2/F3** the merged step registry is a module-level dict populated at import time; there
  is no barrier guaranteeing registration precedes `run_live()`. Document that domain
  `activate()` MUST complete before the live loop starts (the cli host already orders this).
- **F6** resolved by the fail-on-duplicate fold-in.

## Execution-gating protocol (every step)
1. `git mv` + import rewrites per the (re-scoped) base-plan step.
2. **A1** (`python reorg_import_graph.py --placement placement.resolved.json`) → exit 0.
3. **A2** (`reorg_behavior_assert.py`, `--src` after Step 4) → 7/7.
4. **pytest** → exactly the 16 pinned reds, 1581 green. A **17th failure = revert**, fix,
   retry.
5. Commit the step (small, labelled, revertable). FlowRunner split is its own commit (fix 1).

## Re-review (how the merge_then_review is honored)
The base architecture is **arbiter-verified** (A1 PASS on the real tree) — not a Stage-0
failure. The open items were plan-quality (commit isolation, registry policy, path
confinement, concurrency docs), now resolved above. Rather than re-bless a plan *document*,
the merged plan is verified **empirically at execution**: every step is gated by the A1/A2
executable lenses + the pytest invariant, and the **executed reorg** then faces a code-review
council (task #8) with a **unanimous gate** before Phase 1 is declared done. Nothing lands as
"final" un-reviewed — review happens on the real code, where it bites hardest.
