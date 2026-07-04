"""loop_to: an ON-SUCCESS back-edge jumps the lane BACK to a named action, the
dual of skip_to's on-False forward hop (council #4, the exploration LOOP). It is
BOUNDED per back-edge by loop_max so a non-terminating loop can never hang, and
it never touches the required-fail / retry / skip machinery.

Pure-interpreter tests over synthetic Procedures (no exploration.toml, no game).
"""

from ed_core.flow.context import StepContext
from ed_core.flow.interpreter import run_procedure
from ed_core.flow.model import Procedure, Step
from tests.flow import FakeSender


def _recorder():
    rows = []
    return rows, lambda kind, payload: rows.append((kind, payload))


def _registry(calls, head_true_times=None, fail_actions=()):
    """Each action records its name. `head` returns True `head_true_times` times
    then False (when set); everything else returns True unless in fail_actions."""
    state = {"head": 0}

    def make(name):
        def fn(ctx, **params):
            calls.append(name)
            if name == "head" and head_true_times is not None:
                state["head"] += 1
                return state["head"] <= head_true_times
            return name not in fail_actions
        return fn
    return {a: make(a) for a in ("head", "body", "back", "exit")}


def _loop_proc(loop_max=64, head_skips=True):
    """head -> body -> back(loop_to=head) -> exit. head carries skip_to=exit so a
    False head vaults to the exit (the loop's natural terminator)."""
    head = Step("head", skip_to="exit") if head_skips else Step("head")
    return Procedure(
        name="p",
        steps=(head, Step("body"),
               Step("back", loop_to="head", loop_max=loop_max),
               Step("exit")),
    )


def test_loop_back_edge_repeats_until_head_returns_false():
    """head True N times then False: the body + back-edge run N times, then head
    False vaults to exit via skip_to and the proc completes."""
    N = 3
    calls = []
    rows, log = _recorder()
    result = run_procedure(_loop_proc(), StepContext(sender=FakeSender(), record=log),
                           registry=_registry(calls, head_true_times=N))
    assert result.completed is True and result.aborted is False
    assert calls.count("head") == N + 1     # N True + 1 False (exit)
    assert calls.count("body") == N
    assert calls.count("back") == N
    assert calls.count("exit") == 1
    assert calls[-1] == "exit"
    # each loop-back logged StepLooped with the from/to
    looped = [p for k, p in rows if k == "StepLooped"]
    assert len(looped) == N
    assert looped[0]["from"] == "back" and looped[0]["to"] == "head"


def test_loop_always_true_stops_at_loop_max_and_exits():
    """head ALWAYS True (never exits via skip_to): the back-edge fires loop_max
    times, then the budget is spent -> LoopBudgetExceeded -> fall through to exit.
    No hang."""
    LM = 4
    calls = []
    rows, log = _recorder()
    # head_skips=False so head never returns False; the ONLY exit is loop_max.
    proc = _loop_proc(loop_max=LM, head_skips=False)
    result = run_procedure(proc, StepContext(sender=FakeSender(), record=log),
                           registry=_registry(calls))     # everything True
    assert result.completed is True         # exited, did not hang
    assert any(k == "LoopBudgetExceeded" for k, _ in rows)
    assert calls.count("back") == LM + 1    # ran LM+1 times, jumped LM times
    assert calls.count("head") == LM + 1
    assert calls.count("exit") == 1
    budget = next(p for k, p in rows if k == "LoopBudgetExceeded")
    assert budget["loop_max"] == LM and budget["at"] == "back"


def test_loop_aborts_on_should_abort_mid_loop():
    """should_abort flipping True mid-loop aborts immediately — the loop never
    spins to loop_max."""
    calls = []
    abort = {"n": 0}

    def should_abort():
        abort["n"] += 1
        return abort["n"] > 4     # abort after a few steps

    proc = _loop_proc(loop_max=64, head_skips=False)   # would loop forever but for abort
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      should_abort=should_abort)
    result = run_procedure(proc, ctx, registry=_registry(calls))
    assert result.aborted is True and result.completed is False
    assert len(calls) < 64      # nowhere near the budget


def test_loop_to_ignored_when_step_returns_false():
    """loop_to is ON-SUCCESS only: a step that returns False does NOT loop — it
    advances one step (non-required, no skip_to), same as before."""
    calls = []
    rows, log = _recorder()
    proc = Procedure(
        name="p",
        steps=(Step("head"), Step("back", loop_to="head"), Step("exit")),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender(), record=log),
                           registry=_registry(calls, fail_actions={"back"}))
    assert calls == ["head", "back", "exit"]     # no loop
    assert result.completed is True
    assert "StepLooped" not in dict(rows)


def test_unresolvable_loop_to_falls_through_to_advance():
    """A loop_to naming no step (validate should catch it) degrades to a one-step
    advance — no crash, no hang, no abort."""
    calls = []
    rows, log = _recorder()
    proc = Procedure(
        name="p",
        steps=(Step("head"), Step("back", loop_to="nonexistent"), Step("exit")),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender(), record=log),
                           registry=_registry(calls))
    assert calls == ["head", "back", "exit"]
    assert result.completed is True
    assert "StepLooped" not in dict(rows)
