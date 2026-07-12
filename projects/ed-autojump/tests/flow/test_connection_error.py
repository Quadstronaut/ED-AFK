"""Connection-error real-time monitor: watch-loop latching + main-thread
recovery dispatch + the recovery menu macro (operator 2026-07-12).

The CONNECTION ERROR dialog carries NO journal event, so a background CV/OCR
watch is the only signal. On a hit the watch daemon latches a preempt (aborts
the running scene) + a recovery flag; run_live's idle branch runs
connection_recovery on the MAIN thread (procedure execution stays
single-threaded -- the watch thread never presses keys). The detector itself is
tested in tests/vision/test_hud_sc_indicators.py; here we mock it and pin the
dispatcher plumbing + the operator-verified key sequence.
"""

from pathlib import Path
from types import SimpleNamespace

from ed_autojump.flow.dispatcher import FlowRunner
from ed_core.flow.model import Procedure, Step
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"
_DETECT = "ed_vision.hud_sc_indicators.detect_connection_error"


def _runner(procs=None, sender=None):
    return FlowRunner(
        procedures=procs or {},
        sender=sender or FakeSender(),
        clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(
            docked=False, in_supercruise=True, fsd_charging=False,
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False),
    )


# ---- watch tick: detection -> latches --------------------------------------

def test_connection_tick_latches_preempt_and_flag(monkeypatch):
    """A detected modal sets the recovery flag AND (a proc running) a preempt
    that aborts it -- unconditionally, no scene allow-list (a drop obsoletes
    EVERY scene, unlike star_smack)."""
    monkeypatch.setattr(_DETECT, lambda f: True)
    r = _runner()
    r._connection_grabber = lambda: object()   # any non-None frame
    r._running_proc = "smack_recovery"          # even smack_recovery is preempted
    r._connection_tick()
    assert r._connection_error_seen is True
    assert r._preempt == "connection_error"


def test_connection_tick_no_hit_no_latch(monkeypatch):
    monkeypatch.setattr(_DETECT, lambda f: False)
    r = _runner()
    r._connection_grabber = lambda: object()
    r._running_proc = "traversal"
    r._connection_tick()
    assert r._connection_error_seen is False
    assert r._preempt is None


def test_connection_tick_debounced_while_pending(monkeypatch):
    """While a recovery is already pending, the tick does NOT re-grab/re-detect
    -- one episode, one recovery."""
    calls = []
    monkeypatch.setattr(_DETECT, lambda f: calls.append(1) or True)
    r = _runner()
    r._connection_grabber = lambda: calls.append("grab") or object()
    r._connection_error_seen = True   # already pending
    r._connection_tick()
    assert calls == []                # neither grabber nor detector consulted


def test_connection_tick_skips_when_input_exclusive(monkeypatch):
    """A UI macro owns input -> the watch does not grab (a mid-panel grab is
    garbage and a preempt would abort the macro)."""
    calls = []
    monkeypatch.setattr(_DETECT, lambda f: calls.append(1) or True)
    r = _runner()
    r._connection_grabber = lambda: object()
    with r._exclusive_input():
        r._connection_tick()
    assert calls == []
    assert r._connection_error_seen is False


def test_connection_tick_no_grabber_is_noop(monkeypatch):
    monkeypatch.setattr(_DETECT, lambda f: True)
    r = _runner()                      # no connection_grabber wired
    r._connection_tick()
    assert r._connection_error_seen is False


# ---- main-thread recovery consumer -----------------------------------------

def test_maybe_recover_runs_recovery_and_clears_flag():
    """The consumer runs connection_recovery (the operator key sequence) then
    clears the latch so a still-down server re-triggers next tick."""
    sender = FakeSender()
    procs = {"connection_recovery": Procedure(
        name="connection_recovery", steps=(Step("connection_recovery"),))}
    r = _runner(procs, sender)
    r._connection_error_seen = True
    r._maybe_recover_connection()
    assert r._connection_error_seen is False
    # operator-verified sequence: OK -> CONTINUE -> Solo(D,D) -> load -> replot.
    assert sender.actions() == [
        "UI_Select", "UI_Select", "UI_Right", "UI_Right",
        "UI_Select", "GalaxyMapOpen", "UI_Back"]


def test_maybe_recover_unwired_clears_flag_and_logs():
    """No connection_recovery procedure (minimal build): clear the flag + log,
    don't spin re-detecting with no consumer."""
    logs = []
    r = _runner()
    r.record = lambda k, p: logs.append((k, p))
    r._connection_error_seen = True
    r._maybe_recover_connection()
    assert r._connection_error_seen is False
    assert any(k == "ConnectionRecoveryUnwired" for k, _ in logs)


def test_maybe_recover_noop_when_not_flagged():
    sender = FakeSender()
    r = _runner(sender=sender)
    r._maybe_recover_connection()
    assert sender.actions() == []      # nothing flagged -> nothing pressed


# ---- the recovery procedure wires up ---------------------------------------

def test_connection_recovery_procedure_registered_and_valid():
    """connection_recovery.toml loads and every action resolves against the
    merged step registry (the connection_recovery step is registered)."""
    from ed_core.flow.loader import load_procedures, validate_procedure
    from ed_autojump.flow.steps import STEP_REGISTRY
    procs = load_procedures(PROC_DIR)
    assert "connection_recovery" in procs
    assert validate_procedure(
        procs["connection_recovery"], known_actions=STEP_REGISTRY.keys()) == []
