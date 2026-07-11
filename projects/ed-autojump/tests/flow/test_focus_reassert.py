"""Focus re-assert at error states (OPERATOR RULING 2026-07-11).

Overnight run 094825: something stole the ED window's foreground at ~11:00
UTC; SendInput went to the void for 4.8 hours while never-strand loudly
redispatched 48 times. The ruling: re-assert ED foreground WHENEVER AN ERROR
STATE IS SET (ProcedureRetry / ProcedureAborted / strand-guard redispatch),
never before every keypress ("refiring it all the time seems ... overkill").

Pure-Python: ctx.focus_reassert is a spy; the real cli wiring points it at
launcher.focus.focus_ed_window only when --engage-keys.
"""

from types import SimpleNamespace

from ed_core.flow.context import StepContext
from ed_core.flow.interpreter import run_procedure
from ed_core.flow.loader import load_procedures
from pathlib import Path

from . import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def _run_traversal(*, orient_fail_mode, focus_spy=None):
    """test_traversal_flow's harness shape: real traversal proc, fake registry,
    orient_compass scripted to fail once/always."""
    proc = load_procedures(PROC_DIR)["traversal"]
    records = []
    state = {"orient_calls": 0}

    def make(name):
        def fn(ctx, **params):
            if name == "orient_compass":
                state["orient_calls"] += 1
                if orient_fail_mode == "always":
                    return False
                return state["orient_calls"] != 1
            return True
        return fn

    registry = {s.action: make(s.action) for s in proc.steps}
    ctx = StepContext(
        sender=FakeSender(),
        sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(in_supercruise=True),
        record=lambda kind, payload: records.append((kind, payload)),
        focus_reassert=focus_spy,
    )
    result = run_procedure(proc, ctx, registry=registry)
    return result, records


def test_retry_fires_focus_reassert_once_per_retry():
    calls = []
    _, records = _run_traversal(orient_fail_mode="once",
                                focus_spy=lambda: calls.append(1) or True)
    assert len(calls) == 1                      # one retry -> one re-assert
    fr = [p for k, p in records if k == "FocusReassert"]
    assert fr == [{"ok": True, "at": "procedure_retry"}]


def test_abort_fires_focus_reassert_and_spy_failure_is_nonfatal():
    calls = []

    def bad_focus():
        calls.append(1)
        raise RuntimeError("win32 said no")

    result, records = _run_traversal(orient_fail_mode="always",
                                     focus_spy=bad_focus)
    assert result.aborted is True
    # 3 retries + 1 abort = 4 firings, every failure logged ok:False, no crash.
    assert len(calls) == 4
    fr = [p for k, p in records if k == "FocusReassert"]
    assert len(fr) == 4 and all(p["ok"] is False for p in fr)
    assert fr[-1]["at"] == "procedure_aborted"


def test_unwired_is_a_noop():
    """None (unit tests / keys-off runs) -> zero FocusReassert records."""
    _, records = _run_traversal(orient_fail_mode="always", focus_spy=None)
    assert not [p for k, p in records if k == "FocusReassert"]


def test_operator_abort_does_not_fire_focus():
    """Panic/stop abort is the OPERATOR taking the controls — the bot must
    not fight them for window focus."""
    calls = []
    proc = load_procedures(PROC_DIR)["traversal"]
    ctx = StepContext(
        sender=FakeSender(),
        sleeper=lambda s: None,
        should_abort=lambda: True,
        focus_reassert=lambda: calls.append(1) or True,
    )
    result = run_procedure(proc, ctx, registry={s.action: (lambda c, **p: True)
                                                for s in proc.steps})
    assert result.aborted is True
    assert calls == []


def test_redispatch_fires_dispatcher_level_focus():
    """The strand-guard window re-asserts focus before driving the
    redispatch (the exact overnight-094825 recovery point)."""
    from ed_core.flow.dispatcher import FlowRunner

    calls = []
    driven = []
    runner = FlowRunner(
        procedures=load_procedures(PROC_DIR),
        sender=FakeSender(),
        sleeper=lambda s: None,
        redispatch_driver=lambda r: driven.append(1),
        focus_reassert=lambda: calls.append(1) or True,
    )
    runner._needs_redispatch = True
    runner._redispatch_next_t = 0.0
    runner._maybe_redispatch()
    assert driven == [1]
    assert calls == [1]
