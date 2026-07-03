"""Exploration scene (flow-redesign #2/#13-15): the interpreter loop_to LOOP
driven through the REAL exploration.toml, plus the two new steps
confirm_sc_assist_active (observational HUD read) and wait_body_scanned
(AutoScan-seq event gate). Pure-Python — no game, no CV.
"""

from pathlib import Path

import ed_vision.hud_sc_indicators as hud
from ed_core.flow.context import StepContext
from ed_core.flow.interpreter import run_procedure
from ed_core.flow.loader import load_procedures
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def _exploration():
    return load_procedures(PROC_DIR)["exploration"]


def _loop_max():
    back = next(s for s in _exploration().steps if s.loop_to is not None)
    return back.loop_max


# ---- the LOOP, driven through the real exploration.toml ----------------------

def test_loop_runs_body_n_times_then_exits_via_skip_to():
    """nav_supercruise_unexplored True N times then False: the body
    (throttle/orient/confirm/wait) runs N times, the back-edge loops N times, then
    the False head vaults (skip_to) to target_next_route and the proc completes."""
    proc = _exploration()
    calls = []
    state = {"n": 0}
    N = 3

    def make(name):
        def fn(ctx, **params):
            calls.append(name)
            if name == "nav_supercruise_unexplored":
                state["n"] += 1
                return state["n"] <= N
            return True
        return fn

    registry = {s.action: make(s.action) for s in proc.steps}
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None)
    result = run_procedure(proc, ctx, registry=registry)

    assert result.completed is True and result.aborted is False
    assert calls.count("nav_supercruise_unexplored") == N + 1   # N True + 1 False exit
    assert calls.count("orient_compass") == N
    assert calls.count("confirm_sc_assist_active") == N
    assert calls.count("wait_body_scanned") == N
    # set_throttle runs twice per loop (body throttle 100 + back-edge throttle 0)
    assert calls.count("set_throttle") == 2 * N
    assert calls.count("target_next_route") == 1
    assert calls[-1] == "target_next_route"


def test_loop_immediate_terminator_exits_once_no_body():
    """No unexplored bodies (nav_supercruise_unexplored False on the first call):
    skip_to vaults straight to target_next_route, the body never runs, done."""
    proc = _exploration()
    calls = []
    registry = {s.action: (lambda ctx, _n=s.action, **p: calls.append(_n) or
                           (_n != "nav_supercruise_unexplored"))
                for s in proc.steps}
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None)
    result = run_procedure(proc, ctx, registry=registry)
    assert result.completed is True
    assert calls == ["nav_supercruise_unexplored", "target_next_route"]


def test_loop_always_engaged_stops_at_loop_max_no_hang():
    """nav_supercruise_unexplored ALWAYS True: the back-edge hits loop_max, logs
    LoopBudgetExceeded, and falls through to target_next_route — NO hang."""
    proc = _exploration()
    calls, logs = [], []
    registry = {s.action: (lambda ctx, _n=s.action, **p: calls.append(_n) or True)
                for s in proc.steps}
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      record=lambda k, p: logs.append((k, p)))
    result = run_procedure(proc, ctx, registry=registry)
    lm = _loop_max()
    assert result.completed is True
    assert any(k == "LoopBudgetExceeded" for k, _ in logs)
    assert calls.count("nav_supercruise_unexplored") == lm + 1
    assert calls.count("target_next_route") == 1


def test_loop_aborts_on_should_abort():
    """should_abort mid-loop aborts the tour immediately."""
    proc = _exploration()
    abort = {"n": 0}

    def should_abort():
        abort["n"] += 1
        return abort["n"] > 5

    registry = {s.action: (lambda ctx, **p: True) for s in proc.steps}
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      should_abort=should_abort)
    result = run_procedure(proc, ctx, registry=registry)
    assert result.aborted is True


# ---- confirm_sc_assist_active (observational, never gates) -------------------

def test_confirm_sc_assist_active_no_grabber_returns_true():
    logs = []
    ctx = StepContext(sender=FakeSender(), record=lambda k, p: logs.append((k, p)))
    assert STEP_REGISTRY["confirm_sc_assist_active"](ctx) is True
    states = [p["state"] for k, p in logs if k == "ScHudState"]
    assert states == ["none"]


def test_confirm_sc_assist_active_logs_state_and_is_always_true(monkeypatch):
    """ACTIVE, ORBITING and NONE all return True (observational, never gates) and
    log the classified ScHudState."""
    from ed_vision.hud_sc_indicators import ScHudRead, ScHudState
    for state in (ScHudState.ACTIVE, ScHudState.ORBITING, ScHudState.NONE):
        logs = []
        monkeypatch.setattr(
            hud, "read_sc_hud",
            lambda frame, _s=state: ScHudRead(_s, "txt", _s is not ScHudState.NONE))
        ctx = StepContext(sender=FakeSender(),
                          record=lambda k, p: logs.append((k, p)))
        ctx.hud_grabber = lambda: object()
        assert STEP_REGISTRY["confirm_sc_assist_active"](ctx) is True
        states = [p["state"] for k, p in logs if k == "ScHudState"]
        assert states == [state.value]


def test_confirm_sc_assist_active_detector_error_returns_true(monkeypatch):
    def boom(frame):
        raise RuntimeError("detector blew up")
    monkeypatch.setattr(hud, "read_sc_hud", boom)
    logs = []
    ctx = StepContext(sender=FakeSender(), record=lambda k, p: logs.append((k, p)))
    ctx.hud_grabber = lambda: object()
    assert STEP_REGISTRY["confirm_sc_assist_active"](ctx) is True
    assert any(p.get("reason") == "detector_error" for k, p in logs if k == "ScHudState")


# ---- wait_body_scanned (AutoScan seq-advance gate, no wall-clock) ------------

def test_wait_body_scanned_returns_on_seq_advance():
    logs = []
    state = {"seq": 0}

    def autoscan():
        return (state["seq"], frozenset())

    def sleeper(_s):
        state["seq"] += 1        # a scan lands after the first poll

    ctx = StepContext(sender=FakeSender(), sleeper=sleeper,
                      autoscan_supplier=autoscan,
                      record=lambda k, p: logs.append((k, p)))
    assert STEP_REGISTRY["wait_body_scanned"](ctx, poll_s=0.0) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "scanned"


def test_wait_body_scanned_no_deadlock_when_scan_already_advancing():
    """A supplier whose seq is already advancing (a scan in progress) is caught on
    the first in-loop check -> returns immediately, no deadlock waiting for a
    second scan."""
    calls = {"n": 0}

    def autoscan():
        calls["n"] += 1
        return (calls["n"], frozenset())

    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      autoscan_supplier=autoscan)
    assert STEP_REGISTRY["wait_body_scanned"](ctx, poll_s=0.0) is True


def test_wait_body_scanned_returns_on_should_abort():
    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      autoscan_supplier=lambda: (0, frozenset()),
                      should_abort=lambda: True,
                      record=lambda k, p: logs.append((k, p)))
    assert STEP_REGISTRY["wait_body_scanned"](ctx, poll_s=0.0) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "abort"


def test_wait_body_scanned_poll_count_backstop_prevents_hang():
    """No scan ever arrives (constant seq): the poll-COUNT backstop (not a wall
    clock) exits after max_polls with result True — never hangs."""
    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      autoscan_supplier=lambda: (5, frozenset()),
                      record=lambda k, p: logs.append((k, p)))
    assert STEP_REGISTRY["wait_body_scanned"](ctx, poll_s=0.0, max_polls=3) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "backstop"


def test_wait_body_scanned_prompt_when_scan_landed_between_waits():
    """ARBITER-MANDATED MERGE REGRESSION (council wf_7783dbe3): a scan that
    lands BETWEEN two waits — i.e. during the next body's engage/throttle/
    orient steps — must be caught by the persisted high-water baseline and
    return PROMPTLY (zero sleeps), not burn the whole poll budget waiting for
    a second scan. The entry-snapshot version failed exactly this."""
    logs = []
    state = {"seq": 0}
    sleeps = {"n": 0}

    def autoscan():
        return (state["seq"], frozenset())

    def sleeper(_s):
        sleeps["n"] += 1
        state["seq"] += 1        # body 1's scan lands after one poll

    ctx = StepContext(sender=FakeSender(), sleeper=sleeper,
                      autoscan_supplier=autoscan,
                      record=lambda k, p: logs.append((k, p)))
    # wait #1 (body 1): consumes seq 1 after one poll.
    assert STEP_REGISTRY["wait_body_scanned"](ctx, poll_s=0.0) is True
    # body 2's scan fires DURING its engage/orient steps, before wait #2 runs.
    state["seq"] += 1
    sleeps["n"] = 0
    # wait #2 (body 2): must see seq(2) > consumed(1) on the FIRST check.
    assert STEP_REGISTRY["wait_body_scanned"](ctx, poll_s=0.0, max_polls=3) is True
    assert sleeps["n"] == 0, "prompt path regressed to polling"
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "scanned"
