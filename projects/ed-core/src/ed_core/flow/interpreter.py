"""Run a Procedure's steps in order. A failed `required` step triggers the
retry-from policy and, when exhausted, ABORTS the procedure — which never runs
later steps and therefore never throttles or jumps (fail closed)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .context import StepContext
from .model import Procedure
# G12: read the MERGED core step table (surface #3), never a domain step module.
from .step_registry import INPUT_EXCLUSIVE_ACTIONS, STEP_REGISTRY


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


def _reassert_focus(ctx: StepContext, at: str) -> None:
    """OPERATOR RULING 2026-07-11: re-assert ED window foreground whenever an
    error state is set (retry/abort) — not before every keypress ("overkill").
    Overnight run 094825: a stolen foreground ate keypresses for 4.8 h; the
    first retry's re-assert would have recovered it. Fail-soft, logged, no-op
    when unwired (tests / keys-off runs)."""
    fr = getattr(ctx, "focus_reassert", None)
    if fr is None:
        return
    try:
        ok = bool(fr())
    except Exception:  # noqa: BLE001 — focus is best-effort, never fatal
        ok = False
    ctx.log("FocusReassert", {"ok": ok, "at": at})


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
    # Per-back-edge fire counters for the loop primitive, keyed by step index.
    # A spent budget (>= loop_max) stops the loop — the fail-closed cap that
    # keeps a non-terminating loop from hanging the flight.
    loop_counts: dict[int, int] = {}
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

        # WITCHSPACE PAUSE (operator: "we should NOT move during that screen").
        # While a Hyperspace FSD jump is in flight (StartJump→FSDJump, ~18s,
        # journal-confirmed) the nav panel / orient scene is invalid — every
        # input is wasted or harmful. HOLD the next step at this guard until
        # the FSDJump arrival clears the latch. NOT a clock gate — FSDJump is
        # the only exit (no-arbitrary-timed-waits rule). should_abort is
        # rechecked inside so the operator can still stop mid-witchspace.
        # Log-once: WitchspacePause on entry, WitchspaceResume on exit.
        if ctx.in_witchspace():
            _paused_logged = False
            while ctx.in_witchspace():
                if ctx.should_abort():
                    result.aborted = True
                    ctx.log("ProcedureAborted",
                            {"procedure": proc.name,
                             "at": "operator_abort_witchspace",
                             "retries": result.retries})
                    return result
                if not _paused_logged:
                    ctx.log("WitchspacePause",
                            {"procedure": proc.name,
                             "at": proc.steps[i].action})
                    _paused_logged = True
                ctx.sleeper(0.5)
            ctx.log("WitchspaceResume",
                    {"procedure": proc.name, "at": proc.steps[i].action})

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
        else:
            try:
                if (step.action in INPUT_EXCLUSIVE_ACTIONS
                        and ctx.exclusive_guard is not None):
                    # UI macro owns input for its duration — the heat watchdog
                    # pauses so a DeployHeatSink tap can't desync the panel state.
                    with ctx.exclusive_guard():
                        ok = bool(fn(ctx, **step.params))
                else:
                    ok = bool(fn(ctx, **step.params))
            except KeyboardInterrupt:
                raise  # operator abort / panic must propagate, never be parked
            except Exception as exc:  # noqa: BLE001
                # A step that RAISES (e.g. pydirectinput.FailSafeException from a
                # cursor-corner trip, or a vision/OS error) must NOT kill the
                # process and freeze the overlay mid-flight (2026-06-09 NE-Y b34-0
                # incident). Treat a crash as a FAILED step: ok=False routes into
                # the required-fail / abort machinery below — fail-closed, the
                # process stays alive, and the failing step is named. Per-press
                # key release is handled by the sender's own finally.
                ok = False
                ctx.log("StepCrashed",
                        {"procedure": proc.name, "action": step.action,
                         "error": repr(exc)})
        result.steps.append(StepResult(step.action, ok))
        ctx.log("Step", {"procedure": proc.name, "action": step.action, "ok": ok})

        # Forward-skip for a NON-required step that opted in via skip_to
        # (council-ratified conditional-orbit fix, 2026-06-07 lock-speed
        # redesign): a False here jumps the lane FORWARD to the named action
        # instead of advancing one step — arrival's nav_panel_target uses it to
        # vault the get-around block when the star is NOT found in the bounded
        # scan (far -> safe to jump direct). Strictly a forward hop; it never
        # touches the required-fail / retry_from / retry_anchor / abort
        # machinery below.
        if not ok and not step.required and step.skip_to is not None:
            if step.skip_to == "__end__":
                # SKIP-TO-END (2026-07-12): a non-required False FINISHES the
                # procedure cleanly (completed, not aborted) with no terminal
                # step to land on. arrival's FAR distance gate uses it to skip
                # the SC-assist get-around and hand straight off to the
                # orchestrator's traversal branch — arrival has no onward step
                # (the jump lives in traversal), and a `wait` terminal is
                # forbidden there (no blind-pacing regression). Reserved sentinel
                # name, whitelisted in loader.validate_procedure.
                ctx.log("StepSkipped",
                        {"procedure": proc.name, "from": step.action,
                         "to": "__end__", "from_index": i, "to_index": n})
                break
            target = proc.index_of_action(step.skip_to)
            if target is not None:
                ctx.log("StepSkipped",
                        {"procedure": proc.name, "from": step.action,
                         "to": step.skip_to, "from_index": i,
                         "to_index": target})
                i = target
                continue
            # Unresolvable skip target: fall through to normal advance — a
            # non-required miss is best-effort, never a lane abort. (The
            # loader's validate_procedure should have caught a bad name.)

        # ON-SUCCESS BACK-EDGE (loop primitive, council #4 exploration LOOP): the
        # on-True dual of skip_to's on-False forward hop. A step that opted into
        # loop_to jumps the lane BACK to the named action so a scene can repeat a
        # body (throttle/orient/confirm/wait) until the loop-head returns False
        # (its skip_to exit). BOUNDED per back-edge by loop_max: once the budget
        # is spent, log LoopBudgetExceeded and fall through to the next step (the
        # loop's natural exit lies just past the back-edge) — a non-terminating
        # loop can NEVER hang the flight. should_abort is rechecked at the top of
        # the next iteration, so an operator stop mid-loop still aborts promptly.
        if ok and step.loop_to is not None:
            target = proc.index_of_action(step.loop_to)
            if target is not None:
                fired = loop_counts.get(i, 0)
                if fired >= step.loop_max:
                    ctx.log("LoopBudgetExceeded",
                            {"procedure": proc.name, "at": step.action,
                             "loop_to": step.loop_to, "loop_max": step.loop_max})
                    # budget spent -> fall through to i += 1 (exit the loop)
                else:
                    loop_counts[i] = fired + 1
                    ctx.log("StepLooped",
                            {"procedure": proc.name, "from": step.action,
                             "to": step.loop_to, "from_index": i,
                             "to_index": target, "iteration": fired + 1})
                    i = target
                    continue
            # Unresolvable loop target: fall through to a normal one-step advance
            # (validate_procedure should have caught a bad name at load).

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
                _reassert_focus(ctx, "procedure_retry")
                if policy.backoff_s > 0:
                    ctx.sleeper(policy.backoff_s)
                i = target
                continue
            result.aborted = True
            ctx.log("ProcedureAborted",
                    {"procedure": proc.name, "at": step.action, "retries": result.retries})
            _reassert_focus(ctx, "procedure_aborted")
            return result
        i += 1

    result.completed = True
    return result
