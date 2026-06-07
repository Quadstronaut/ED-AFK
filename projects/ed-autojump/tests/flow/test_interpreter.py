from types import SimpleNamespace

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


# ---------------------------------------------------------------------------
# State-aware retry: retry_from_if_supercruise (operator-dictated, the
# 2026-06-07 14:24-14:29Z burn — a pre-anchor orient failed AFTER the SC
# charge completed and the real-space ladder re-ran all-zero in SC).
# ---------------------------------------------------------------------------

def _sc_status(in_supercruise):
    return SimpleNamespace(in_supercruise=in_supercruise)


class _CountingStatus:
    """status_supplier that records how many times it was read and replays a
    scripted sequence of in_supercruise values (last value repeats)."""
    def __init__(self, *seq):
        self._seq = list(seq)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        i = min(self.calls - 1, len(self._seq) - 1)
        v = self._seq[i]
        return None if v is None else _sc_status(v)


def _sc_proc():
    """orient (pre-anchor, required) fails; retry_from=a (real-space lane),
    retry_from_if_supercruise=c which is ALSO the retry_anchor (the hop lock)."""
    return Procedure(
        name="p",
        steps=(Step("a"), Step("orient", required=True),
               Step("c", retry_anchor=True), Step("jump")),
        on_required_fail=OnRequiredFail(
            retry_from="a", retry_from_if_supercruise="c",
            max_retries=1, backoff_s=0.0),
    )


def test_pre_anchor_fail_in_supercruise_resumes_at_sc_key():
    """1a: pre-anchor required fail, key set, both status reads in_supercruise
    -> resume at the SC key (index 2 / c), NOT retry_from."""
    calls = []
    status = _CountingStatus(True, True)
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      status_supplier=status)
    result = run_procedure(_sc_proc(), ctx, registry=_registry(calls, {"orient"}))
    # a,orient(fail) -> SC branch resumes at c -> c,jump complete
    assert calls == ["a", "orient", "c", "jump"]
    assert result.completed is True and result.retries == 1


def test_pre_anchor_fail_in_real_space_resumes_at_retry_from():
    """1b: same procedure, both reads NOT in supercruise -> retry_from (a),
    today's pinned behavior."""
    calls = []
    status = _CountingStatus(False, False)
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      status_supplier=status)
    result = run_procedure(_sc_proc(), ctx, registry=_registry(calls, {"orient"}))
    # a,orient(fail) -> retry_from a -> a,orient(fail) -> exhausted
    assert calls == ["a", "orient", "a", "orient"]
    assert result.aborted is True and result.retries == 1


def test_post_anchor_fail_with_sc_key_still_resumes_at_anchor():
    """1c: a POST-anchor failure resumes at the ANCHOR even though the SC key
    is set — anchor precedence (1) wins over the SC branch (2)."""
    calls = []
    status = _CountingStatus(True, True)
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("b", retry_anchor=True),
               Step("orient", required=True), Step("jump")),
        on_required_fail=OnRequiredFail(
            retry_from="a", retry_from_if_supercruise="a",
            max_retries=1, backoff_s=0.0),
    )
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      status_supplier=status)
    result = run_procedure(proc, ctx, registry=_registry(calls, {"orient"}))
    # a,b,orient(fail) -> anchor b (NOT the SC key a) -> b,orient(fail) -> done
    assert calls == ["a", "b", "orient", "b", "orient"]
    # anchor short-circuits BEFORE the status reads -> supplier never consulted
    assert status.calls == 0


def test_no_sc_key_never_calls_status_supplier():
    """1d: a procedure WITHOUT the key never touches status_supplier (counting
    fake asserts zero calls), and the resolution is identical to today."""
    calls = []
    status = _CountingStatus(True, True)
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("orient", required=True), Step("jump")),
        on_required_fail=OnRequiredFail(retry_from="a", max_retries=1,
                                        backoff_s=0.0),
    )
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      status_supplier=status)
    result = run_procedure(proc, ctx, registry=_registry(calls, {"orient"}))
    assert status.calls == 0                       # short-circuit on key check
    assert calls == ["a", "orient", "a", "orient"]  # plain retry_from
    assert result.aborted is True and result.retries == 1


def test_status_none_falls_through_to_retry_from():
    """1e: status supplier returns None (unwired / unreadable) -> the SC gate
    is not satisfied, retry_from branch taken, no raise."""
    calls = []
    status = _CountingStatus(None, None)
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      status_supplier=status)
    result = run_procedure(_sc_proc(), ctx, registry=_registry(calls, {"orient"}))
    assert calls == ["a", "orient", "a", "orient"]   # retry_from a
    assert result.aborted is True


def test_two_read_gate_distrusts_mid_transition():
    """1f: first read in_supercruise=True, second False -> the two-read gate
    rejects the SC branch (a single mid-transition read is untrustworthy), so
    resolution falls to retry_from."""
    calls = []
    status = _CountingStatus(True, False)
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      status_supplier=status)
    result = run_procedure(_sc_proc(), ctx, registry=_registry(calls, {"orient"}))
    assert calls == ["a", "orient", "a", "orient"]   # real-space lane
    assert result.aborted is True


def test_non_required_failure_continues():
    calls = []
    proc = Procedure(name="p", steps=(Step("a"), Step("b"), Step("c")))
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"b"}))
    assert calls == ["a", "b", "c"]
    assert result.completed is True
