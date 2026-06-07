"""Run a Procedure's steps in order. A failed `required` step triggers the
retry-from policy and, when exhausted, ABORTS the procedure — which never runs
later steps and therefore never throttles or jumps (fail closed)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .context import StepContext
from .model import Procedure
from .steps import INPUT_EXCLUSIVE_ACTIONS, STEP_REGISTRY


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
        if ctx.should_abort():
            # Panic / stop: abort the whole procedure NOW — no retries, no
            # later steps (a retry after panic would press keys the operator
            # just tried to stop).
            result.aborted = True
            ctx.log("ProcedureAborted",
                    {"procedure": proc.name, "at": "operator_abort",
                     "retries": result.retries})
            return result
        step = proc.steps[i]
        if ctx.overlay is not None:
            # cosmetic; the writer is fail-soft but guard anyway so a broken
            # overlay can never abort a procedure.
            try:
                ctx.overlay.step(proc.name, step.action, i + 1, n)
            except Exception:  # noqa: BLE001
                pass
        fn = reg.get(step.action)
        if fn is None:
            ok = False
        elif step.action in INPUT_EXCLUSIVE_ACTIONS and ctx.exclusive_guard is not None:
            # UI macro owns input for its duration — the heat watchdog pauses
            # so a DeployHeatSink tap can't desync the panel state.
            with ctx.exclusive_guard():
                ok = bool(fn(ctx, **step.params))
        else:
            ok = bool(fn(ctx, **step.params))
        result.steps.append(StepResult(step.action, ok))
        ctx.log("Step", {"procedure": proc.name, "action": step.action, "ok": ok})

        if not ok and step.required:
            policy = proc.on_required_fail
            # Retry-target precedence (council-settled 2026-06-07, load-bearing):
            #   (1) a retry_anchor at or before the failure wins — once the
            #       procedure is past the anchor, failures return THERE (e.g.
            #       the smack-recovery hop lock) instead of restarting the lane.
            #   (2) else, if retry_from_if_supercruise is set AND the ship is
            #       already in supercruise, resume from that action — the SC
            #       branch (the 14:24-14:29Z burn: a PRE-anchor orient_compass
            #       failed AFTER the SC charge completed, and 3 retries re-ran
            #       the real-space ladder all-zero in SC).
            #   (3) else retry_from, as before.
            target = proc.anchor_at_or_before(i)
            if target is None and policy.retry_from_if_supercruise is not None:
                # Two-read gate (conservative): Status.json rewrites only ~1s,
                # so a single mid-transition read is untrustworthy. BOTH reads
                # must be non-None AND in_supercruise truthy to take the SC
                # branch — a false negative is exactly today's behavior, no
                # worse. CRITICAL: the key-set check above short-circuits this,
                # so status_supplier is NEVER called when the key is None
                # (arrival/startup must not poll status on a required fail).
                st1 = ctx.status_supplier()
                ctx.sleeper(0.3)
                st2 = ctx.status_supplier()
                in_sc = (st1 is not None and st2 is not None
                         and getattr(st1, "in_supercruise", False)
                         and getattr(st2, "in_supercruise", False))
                if in_sc:
                    target = proc.index_of_action(
                        policy.retry_from_if_supercruise)
            if target is None and policy.retry_from is not None:
                target = proc.index_of_action(policy.retry_from)
            if target is not None and result.retries < policy.max_retries:
                result.retries += 1
                ctx.log("ProcedureRetry",
                        {"procedure": proc.name, "failed": step.action,
                         "resume_at": proc.steps[target].action,
                         "resume_index": target, "retries": result.retries})
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
