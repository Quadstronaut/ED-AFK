"""skip_to: a NON-required step returning False vaults the lane FORWARD to a
named action (council-ratified conditional-orbit fix, 2026-06-07 lock-speed
redesign). Strictly a forward hop — it never touches the required-fail / retry
machinery, and a step without skip_to behaves exactly as before."""

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.interpreter import run_procedure
from ed_autojump.flow.model import OnRequiredFail, Procedure, Step
from tests.flow import FakeSender


def _registry(calls, fail_actions):
    """Each action records its name and returns True unless in fail_actions."""
    def make(name):
        def fn(ctx, **params):
            calls.append(name)
            return name not in fail_actions
        return fn
    return {a: make(a) for a in ("a", "guard", "b", "c", "target", "orient", "jump")}


def _recorder():
    rows = []
    return rows, lambda kind, payload: rows.append((kind, payload))


def test_false_with_skip_to_jumps_forward_over_block():
    """guard fails (non-required) with skip_to=target -> b and c (the block) are
    skipped, the lane resumes at target."""
    calls = []
    rows, log = _recorder()
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("guard", skip_to="target"),
               Step("b"), Step("c"), Step("target"), Step("jump")),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender(), record=log),
                           registry=_registry(calls, {"guard"}))
    assert calls == ["a", "guard", "target", "jump"]   # b, c vaulted
    assert result.completed is True and result.aborted is False
    skip = dict(rows)["StepSkipped"]
    assert skip["from"] == "guard" and skip["to"] == "target"
    assert skip["from_index"] == 1 and skip["to_index"] == 4


def test_true_with_skip_to_advances_normally():
    """guard PASSES -> skip_to is inert, every step runs in order."""
    calls = []
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("guard", skip_to="target"),
               Step("b"), Step("c"), Step("target"), Step("jump")),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, set()))
    assert calls == ["a", "guard", "b", "c", "target", "jump"]
    assert result.completed is True


def test_false_without_skip_to_unchanged():
    """A non-required False with NO skip_to advances one step, as before —
    existing procedures must be identical."""
    calls = []
    proc = Procedure(name="p", steps=(Step("a"), Step("guard"), Step("b")))
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"guard"}))
    assert calls == ["a", "guard", "b"]
    assert result.completed is True


def test_skip_to_ignored_for_required_step():
    """skip_to on a REQUIRED step is ignored — a required fail goes through the
    abort/retry path, not the forward skip (skip is for opt-in best-effort
    guards only)."""
    calls = []
    rows, log = _recorder()
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("guard", required=True, skip_to="target"),
               Step("b"), Step("target"), Step("jump")),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender(), record=log),
                           registry=_registry(calls, {"guard"}))
    # required fail, no retry policy -> abort; target/jump never run, no skip log
    assert calls == ["a", "guard"]
    assert result.aborted is True
    assert "StepSkipped" not in dict(rows)


def test_skip_to_does_not_disturb_retry_machinery():
    """A skip on an earlier non-required guard, then a later required fail still
    follows retry_from — the two mechanisms coexist."""
    calls = []
    proc = Procedure(
        name="p",
        steps=(Step("guard", skip_to="orient"), Step("b"),
               Step("orient", required=True), Step("jump")),
        on_required_fail=OnRequiredFail(retry_from="orient", max_retries=1,
                                        backoff_s=0.0),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"guard", "orient"}))
    # guard fails -> skip to orient; orient fails -> retry_from orient ->
    # orient fails -> exhausted. b is vaulted by the skip.
    assert calls == ["guard", "orient", "orient"]
    assert result.aborted is True and result.retries == 1


def test_unresolvable_skip_to_falls_through_to_advance():
    """A skip_to naming no step (defensive; validate_procedure should catch it)
    must NOT crash and must NOT abort — it degrades to a one-step advance."""
    calls = []
    rows, log = _recorder()
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("guard", skip_to="nonexistent"), Step("b")),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender(), record=log),
                           registry=_registry(calls, {"guard"}))
    assert calls == ["a", "guard", "b"]    # plain advance, no skip
    assert result.completed is True
    assert "StepSkipped" not in dict(rows)


def test_loader_flags_unresolvable_skip_to():
    """validate_procedure catches a skip_to that matches no step (same loud
    check as retry_from) — the typo is caught at load, not silently swallowed
    into a one-step advance at runtime."""
    from ed_autojump.flow.loader import validate_procedure
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("guard", skip_to="nowhere"), Step("b")),
    )
    errors = validate_procedure(proc, known_actions={"a", "guard", "b"})
    assert any("skip_to" in e and "nowhere" in e for e in errors)
