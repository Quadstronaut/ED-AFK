"""The MERGED step registry — registration surface #3 (G12 seam).

This is the ONE core-owned step table. Every domain (ed-autojump jump/dock,
ed-explore body_tour) registers its step impls BY NAME into this table at import
/ activation time; the core shared/generic/honk steps register here too
(steps_shared). The interpreter and cli-validation read THIS merged table —
never a domain step module. That is what keeps `flow.interpreter` (core) and
`cli` (core) from importing a domain (the G12 core->domain edge).

The public names mirror the pre-reorg `flow.steps` surface so the interpreter's
`from .steps import INPUT_EXCLUSIVE_ACTIONS, STEP_REGISTRY` becomes
`from .step_registry import INPUT_EXCLUSIVE_ACTIONS, STEP_REGISTRY` with
identical semantics: STEP_REGISTRY is the live action->fn dict; the interpreter
reads it directly (or via merged_step_registry()).

Concurrency invariants carried from the pre-reorg dispatcher (Phase-1 reorg
mandatory fix 3):
  F1  FlowRunner._apply_state's deferred `from ...import` of step helpers runs
      under _TailHub._lock; CPython-GIL-safe today. A lock-ordering hazard only
      under 3.13 free-threading (no `gil` build is used here).
  F2/F3  This registry is a module-level dict populated at IMPORT TIME (each
      domain step module calls register_step on import; the cli host imports +
      activate()s every active app BEFORE FlowRunner.run_live() starts). There
      is no runtime barrier guaranteeing registration precedes run_live; the
      ORDERING is guaranteed by the cli host, which completes every active
      app's activate() (which imports its step modules) before the live loop.
      Do NOT lazily register a step from inside run_live.
  F6  register_step is FAIL-ON-DUPLICATE (below): a colliding name RAISES rather
      than silently overwriting (no dict.update last-wins). Today the
      shared/jump/dock/explore step names are disjoint, so this never fires in
      normal use — it is a guard, not a constraint.
"""

from __future__ import annotations

from typing import Callable

StepFn = Callable[..., bool]

# The ONE merged action -> step-fn table. Domains register INTO this.
STEP_REGISTRY: dict[str, StepFn] = {}

# Actions whose step owns input exclusively (the heat watchdog pauses for their
# duration). A live module-level set, populated by register_step(..,
# input_exclusive=True). The interpreter membership-tests this directly, so it
# must be a live container (not a frozenset rebound on each registration).
INPUT_EXCLUSIVE_ACTIONS: set[str] = set()


def register_step(name: str, fn: StepFn, *, input_exclusive: bool = False) -> None:
    """Register a step impl by action name into the merged core table.

    FAIL-ON-DUPLICATE (F6 / addendum fold-in): a name already present RAISES
    ValueError instead of silently overwriting. The step names across
    shared/jump/dock/explore are disjoint, so this only fires on a genuine
    collision bug.
    """
    if name in STEP_REGISTRY:
        raise ValueError(
            f"duplicate step registration for {name!r}: a step by that name is "
            f"already in the merged registry (fail-on-duplicate, no last-wins)")
    STEP_REGISTRY[name] = fn
    if input_exclusive:
        INPUT_EXCLUSIVE_ACTIONS.add(name)


def merged_step_registry() -> dict[str, StepFn]:
    """The merged action -> step-fn table the interpreter/cli read."""
    return STEP_REGISTRY


def input_exclusive_actions() -> frozenset[str]:
    """The set of input-exclusive action names, as an immutable snapshot."""
    return frozenset(INPUT_EXCLUSIVE_ACTIONS)
