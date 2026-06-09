"""A step that RAISES must NOT propagate out and kill the live loop — it becomes
a fail-closed abort the operator can see. Council fix 2026-06-09: an unhandled
pydirectinput.FailSafeException (cursor in a screen corner) crashed the process
mid-flight and froze the overlay on "STARTUP > HOLD_ALIGNMENT", so arrival never
ran. The dispatch trigger was proven sound (replay_driver) — the bug was the
process dying. The interpreter now turns a step raise into a normal step
failure; KeyboardInterrupt still propagates so panic/Ctrl+C work.
"""

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.interpreter import run_procedure
from ed_autojump.flow.model import Procedure, Step


def _ctx():
    logs: list[tuple[str, dict]] = []
    ctx = StepContext(sender=None, record=lambda t, p: logs.append((t, p)))
    return ctx, logs


def test_required_step_crash_aborts_not_propagates():
    """A required step that raises -> aborted procedure (fail-closed), not a
    propagated exception that would kill run_live."""
    proc = Procedure(name="boom", steps=(Step(action="kaboom", required=True),))

    def kaboom(ctx, **kw):
        raise RuntimeError("cursor in corner")  # stand-in for FailSafeException

    ctx, logs = _ctx()
    result = run_procedure(proc, ctx, registry={"kaboom": kaboom})

    assert result.aborted is True
    assert result.completed is False
    assert any(t == "StepCrashed" and p["action"] == "kaboom" for t, p in logs)


def test_nonrequired_step_crash_continues():
    """A non-required step that raises just fails (ok=False); the lane advances
    and a later required step can still complete the procedure."""
    proc = Procedure(name="soft", steps=(
        Step(action="kaboom", required=False),
        Step(action="good", required=True),
    ))

    def kaboom(ctx, **kw):
        raise ValueError("boom")

    def good(ctx, **kw):
        return True

    ctx, logs = _ctx()
    result = run_procedure(proc, ctx, registry={"kaboom": kaboom, "good": good})

    assert result.completed is True
    assert result.aborted is False
    assert any(t == "StepCrashed" for t, _ in logs)


def test_keyboardinterrupt_still_propagates():
    """Panic / Ctrl+C must NEVER be swallowed by the crash guard."""
    proc = Procedure(name="panic", steps=(Step(action="stop", required=True),))

    def stop(ctx, **kw):
        raise KeyboardInterrupt()

    ctx, _ = _ctx()
    try:
        run_procedure(proc, ctx, registry={"stop": stop})
        raise AssertionError("KeyboardInterrupt must propagate, not be parked")
    except KeyboardInterrupt:
        pass
