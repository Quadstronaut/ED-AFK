from ed_autojump.flow.context import StepContext
from ed_autojump.flow.interpreter import run_procedure
from ed_autojump.flow.model import OnRequiredFail, Procedure, Step
from tests.flow import FakeSender


def _registry(calls, fail_actions):
    """Build a fake registry; each action appends its name and returns
    True unless it is in `fail_actions` (which return False)."""
    def make(name):
        def fn(ctx, **params):
            calls.append(name)
            return name not in fail_actions
        return fn
    return {a: make(a) for a in ("a", "b", "c", "orient", "jump")}


def test_runs_steps_in_order_to_completion():
    calls = []
    proc = Procedure(name="p", steps=(Step("a"), Step("b"), Step("c")))
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, set()))
    assert calls == ["a", "b", "c"]
    assert result.completed is True and result.aborted is False


def test_required_failure_aborts_without_running_later_steps():
    calls = []
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("orient", required=True), Step("jump")),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"orient"}))
    assert calls == ["a", "orient"]      # jump NEVER ran
    assert result.aborted is True and result.completed is False


def test_retry_from_resumes_then_aborts_after_max():
    calls = []
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("orient", required=True), Step("jump")),
        on_required_fail=OnRequiredFail(retry_from="a", max_retries=2, backoff_s=0.0),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"orient"}))
    # initial a,orient -> retry: a,orient -> retry: a,orient -> abort
    assert calls == ["a", "orient", "a", "orient", "a", "orient"]
    assert result.retries == 2 and result.aborted is True


def test_retry_anchor_catches_failures_at_or_after_it():
    """Operator rule (2026-06-07 startup redesign): 'if it makes it to 13,
    failures after that should return to 13' — a required failure AT OR AFTER
    a retry_anchor step resumes from the anchor, not from retry_from."""
    calls = []
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("b", retry_anchor=True),
               Step("orient", required=True), Step("jump")),
        on_required_fail=OnRequiredFail(retry_from="a", max_retries=1, backoff_s=0.0),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"orient"}))
    # a,b,orient(fail) -> anchor b -> b,orient(fail) -> retries exhausted
    assert calls == ["a", "b", "orient", "b", "orient"]
    assert result.retries == 1 and result.aborted is True


def test_failure_before_anchor_falls_back_to_retry_from():
    """An anchor LATER in the procedure must not catch earlier failures —
    those still restart from retry_from (the recovery-lane entry)."""
    calls = []
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("orient", required=True),
               Step("c", retry_anchor=True), Step("jump")),
        on_required_fail=OnRequiredFail(retry_from="a", max_retries=1, backoff_s=0.0),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"orient"}))
    # orient at index 1 fails; the anchor at index 2 is AFTER it -> retry_from
    assert calls == ["a", "orient", "a", "orient"]
    assert result.retries == 1 and result.aborted is True


def test_non_required_failure_continues():
    calls = []
    proc = Procedure(name="p", steps=(Step("a"), Step("b"), Step("c")))
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"b"}))
    assert calls == ["a", "b", "c"]
    assert result.completed is True
