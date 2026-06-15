# Shared-docking placement decision (ED-AFK)

**Date:** 2026-06-15
**Source:** council run `wf_8093a404-a2b` (arch tier, unanimous COMMIT, winner `gen-opus-2`)
**Status:** decided — implementation is a Stage-1 follow-on (this decision moves NO code)

## Decision

The **common docking seam** — `approach → docking-request → dock-confirm → RRR/refuel` —
**sinks into ed-core** as a new module `ed_core/flow/steps_dock.py`, exactly analogous to the
existing `ed_core/flow/steps_shared.py`. It registers its dock actions into registration
surface #3 (the merged `STEP_REGISTRY`) on import.

**Per-domain divergences** (trading buy/sell, combat rearm-priority) **stay in the owning
domain** as additional steps that the domain's own `activate()` registers, and are
**sequenced — not subclassed —** through surface #4 (TOML procedures): a domain's procedure
lists the shared core dock step-names first, then its own divergent step-names. No
domain↔domain edge is ever created, because every domain references the shared seam **only
through the core step-name table** (string action-names), never by importing another domain.
This is the same indirection that already lets the core interpreter run domain-registered
steps without importing any domain.

## (a) Where — ed-core, duplication rejected
The DAG (`ed_vision=0 < ed_core=1 < domains=2`; domains never import each other; deferred
imports count) forbids any domain owning code shared across domains — ed-autojump owning the
dock steps with ed-trading/ed-combat reusing them needs a rank-2→rank-2 sideways import, which
`whole_tree_import_check.py` flags. The only non-duplicating home below all domains is ed-core.
Duplication is rejected: (i) drift across three copies; (ii) `register_step` is
**fail-on-duplicate** — two domains registering `dock_approach` into the one merged table would
raise `ValueError` and crash the (unguarded) CLI plug-in loop at activate() time.

## (b) Additive loading — import-on-activate
`steps_dock` registers as an import side-effect, but is imported **only when a docking-needing
domain activates**: each such domain's `activate()` does `from ed_core.flow import steps_dock`
(mirroring how `ed_autojump.activate()` already pulls `from . import flow` and
`ed_explore.activate()` pulls `from . import steps_body_tour`). A workspace with no docking
domain active never imports it — true additive loading, no dead registration.

## (c) Interface
- **Action-names (surface #3 keys):** `dock_approach`, `dock_request`, `dock_await_docked`, plus
  an RRR/refuel primitive (proposed `dock_refuel_rearm_repair`; final name is Stage-1's, must be
  disjoint from every existing registered name). `scoop_refuel` already lives core-shared in
  `steps_shared`.
- **Signature:** every shared dock step matches the `steps_shared` contract —
  `def step_dock_x(ctx: StepContext, **kwargs) -> bool` — registered via
  `register_step("dock_x", step_dock_x, input_exclusive=<bool>)`.
- **Extension contract:** a domain extends docking by registering **new, disjoint** action-names
  from its own `activate()` (e.g. `trading_sell_cargo`, `trading_buy_refill`,
  `combat_rearm_priority`). Disjointness is **enforced** by fail-on-duplicate. Composition is by
  procedure (list shared names then divergent names in TOML), not inheritance.

## (d) Borderline steps — Stage-1 cut-line (UNRESOLVED, settle before relocation)
Only the genuinely common `approach→request→dock→RRR` seam sinks. The borderline cases —
`dock_target_station`, `dock_sc_assist`, `station_services{,_macro}`, `dock_blind_maneuver` —
are arguably domain-flavored and may stay in the owning domain. Stage-1 settles the exact cut;
this decision fixes the principle (common seam sinks, flavored steps stay).

## (e) Migration debt — follow-on, NOT performed here
Relocating the `dock_*` bodies out of `ed_autojump/flow/steps.py:1291-1301` into
`ed_core/flow/steps_dock.py` **requires deleting their `register_step(...)` calls in the same
atomic change**, or fail-on-duplicate crashes the unguarded CLI loop at activate() time.
- **`input_exclusive` flags must carry over verbatim** — arbiter-verified that
  `dock_await_docked`, `scoop_refuel`, `wait_masslock_clear`, `confirm_menu_item` are **not**
  exclusive; the other `dock_*` are. `INPUT_EXCLUSIVE_ACTIONS` membership must stay byte-identical.
- **Rebase onto the in-flight `ED-AFK-wt-dock [dock-approach-fix]` worktree's final bodies**, not
  master text, or the dock-approach fix is clobbered.
- The `steps_shared` helper chain (`_press` / `_ensure_cockpit_focus` / `_supercruise_lost_guard`)
  travels with the bodies; `steps_dock` importing them from `steps_shared` is a legal intra-core edge.

## Carried-forward risk (Stage-1 to enforce)
The import-trigger (`from ed_core.flow import steps_dock` inside `activate()`) is a **manual
convention, not framework-enforced**. If a domain's `activate()` forgets it, that domain's TOML
procedures reference `dock_approach` etc. that were never registered → silent `KeyError` at
FlowRunner step lookup. Consider an enforcement mechanism in Stage-1.
