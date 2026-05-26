"""Run a Procedure's steps in order. A failed `required` step triggers the
retry-from policy and, when exhausted, ABORTS the procedure — which never runs
later steps and therefore never throttles or jumps (fail closed)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .context import StepContext
from .model import Procedure
from .steps import STEP_REGISTRY


@dataclass
class StepResult:
    action: str
    ok: bool


@dataclass
class ProcedureResult:
    name: str
    completed: bool = False
    aborted: bool = False
    retries: int = 0
    steps: list[StepResult] = field(default_factory=list)


def run_procedure(
    proc: Procedure,
    ctx: StepContext,
    *,
    registry: Optional[dict[str, Callable[..., bool]]] = None,
) -> ProcedureResult:
    reg = registry if registry is not None else STEP_REGISTRY
    result = ProcedureResult(name=proc.name)
    i = 0
    n = len(proc.steps)
    while i < n:
        step = proc.steps[i]
        fn = reg.get(step.action)
        ok = False if fn is None else bool(fn(ctx, **step.params))
        result.steps.append(StepResult(step.action, ok))
        ctx.log("Step", {"procedure": proc.name, "action": step.action, "ok": ok})

        if not ok and step.required:
            policy = proc.on_required_fail
            target = (proc.index_of_action(policy.retry_from)
                      if policy.retry_from is not None else None)
            if target is not None and result.retries < policy.max_retries:
                result.retries += 1
                if policy.backoff_s > 0:
                    ctx.sleeper(policy.backoff_s)
                i = target
                continue
            result.aborted = True
            ctx.log("ProcedureAborted",
                    {"procedure": proc.name, "at": step.action, "retries": result.retries})
            return result
        i += 1

    result.completed = True
    return result
