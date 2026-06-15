"""sc_resume must NOT throttle toward the star without an affirmative
clear-of-star signal (star-smack fix, session_2026-06-08T100951).

The clear-of-star gate is arrival.toml's verified distance proxy: nav_panel_target
bounded to max_rows=3. A CLOSE star (parked nose-on) locks in the top rows and the
orbit get-around runs BEFORE throttle-up; a FAR/clear star is buried and the gate
skips_to target_next_route (the Robigo fast-resume path, unchanged).
"""
from pathlib import Path

from ed_core.flow.context import StepContext
from ed_core.flow.interpreter import run_procedure
from ed_core.flow.loader import load_procedures
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def _sc_resume():
    return load_procedures(PROC_DIR)["sc_resume"]


def test_clear_of_star_gate_precedes_throttle():
    """PIN: the FIRST step is the bounded star-lock distance gate (max_rows=3,
    non-required, skip_to=target_next_route) and it sits BEFORE set_throttle.
    Without this gate sc_resume threw set_throttle(100) at the arrival star."""
    steps = _sc_resume().steps
    gate = steps[0]
    assert gate.action == "nav_panel_target", f"first step must be the clear-of-star gate; got {gate.action}"
    assert gate.required is False, "gate must be non-required so a FAR star skips, never aborts"
    assert gate.skip_to == "target_next_route", "FAR star must vault to the fast resume"
    assert gate.params.get("max_rows") == 3, "distance proxy must use the tight max_rows=3 bound"
    actions = [s.action for s in steps]
    assert actions.index("nav_panel_target") < actions.index("set_throttle"), \
        "the clear-of-star gate must run BEFORE any throttle toward the star"
    # orbit get-around present between the gate and the throttle
    assert "sc_assist_orbit" in actions[:actions.index("set_throttle")], \
        "the CLOSE-star path must have an orbit get-around before throttle-up"


def test_far_star_skips_getaround_to_fast_resume():
    """FAR/clear star (nav_panel_target False) -> skip_to vaults sc_assist_orbit
    and the orbit wait, lands on target_next_route. This is the Robigo loiter
    fast-path: it must still skip the orbit, never orbit a Fleet Carrier."""
    proc = _sc_resume()
    calls = []

    def make(name, fail):
        def fn(ctx, **params):
            calls.append(name)
            return not fail
        return fn
    # nav_panel_target returns False (star buried = FAR); everything else passes.
    actions = {s.action for s in proc.steps}
    reg = {a: make(a, a == "nav_panel_target") for a in actions}
    result = run_procedure(proc, StepContext(sender=FakeSender()), registry=reg)
    assert "sc_assist_orbit" not in calls, "FAR star must SKIP the orbit get-around"
    assert calls[0] == "nav_panel_target"
    assert "target_next_route" in calls and "set_throttle" in calls, \
        "FAR star must still run the fast resume (target + throttle + orient + jump)"
    assert result.completed is True


def test_close_star_runs_orbit_before_throttle():
    """CLOSE star (nav_panel_target True) -> orbit get-around runs, and it runs
    BEFORE set_throttle. This is exactly run 2 (parked nose-on the arrival star).
    """
    proc = _sc_resume()
    calls = []

    def make(name):
        def fn(ctx, **params):
            calls.append(name)
            return True
        return fn
    reg = {s.action: make(s.action) for s in proc.steps}
    run_procedure(proc, StepContext(sender=FakeSender()), registry=reg)
    assert "sc_assist_orbit" in calls, "CLOSE star must run the orbit get-around"
    assert calls.index("sc_assist_orbit") < calls.index("set_throttle"), \
        "the orbit get-around must complete BEFORE throttle toward the star"
