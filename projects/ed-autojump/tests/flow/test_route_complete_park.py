"""route_complete_park terminal procedure (council-ratified 2026-06-07). The
FRONT HALF of arrival (steps 1-5): cut throttle, best-effort scoop, verified
star lock, SC-assist orbit, settle — then STOP. It must NOT carry the jump
half (target_next_route / orient / engage_jump / hold_alignment): there is no
next hop at route end, and re-jumping is exactly the false-abort bug it fixes."""

from pathlib import Path
from types import SimpleNamespace

from ed_core.flow.context import StepContext
from ed_core.flow.interpreter import run_procedure
from ed_core.flow.loader import load_procedures, validate_procedure
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def _park():
    return load_procedures(PROC_DIR)["route_complete_park"]


def test_loads_and_validates():
    proc = _park()
    errors = validate_procedure(proc, known_actions=STEP_REGISTRY.keys())
    assert errors == [], errors


def test_step_order_is_arrival_front_half_only():
    actions = [s.action for s in _park().steps]
    assert actions == [
        "set_throttle",    # 1 throttle 0
        "scoop_refuel",    # 2 best-effort pit stop
        "nav_panel_target",# 3 verified star lock (required)
        "sc_assist_orbit", # 4 guarded orbit
        "wait",            # 5 settle, then STOP
    ]


def test_has_no_jump_or_next_hop_steps():
    """The whole point: NO re-jump at route end."""
    actions = {s.action for s in _park().steps}
    for forbidden in ("target_next_route", "orient_compass",
                      "orient_widget_ring", "engage_jump", "hold_alignment"):
        assert forbidden not in actions, f"{forbidden} must not be in the park"


def test_nav_panel_target_is_required_orbit_is_best_effort():
    proc = _park()
    by_action = {s.action: s for s in proc.steps}
    assert by_action["nav_panel_target"].required is True   # wrong lock blocks
    assert by_action["sc_assist_orbit"].required is False   # degrade-friendly


def test_honk_rides_along_like_arrival():
    assert _park().parallel_tracks == ("honk",)


def test_retry_anchor_is_the_lock_bounded():
    proc = _park()
    assert proc.on_required_fail.retry_from == "nav_panel_target"
    assert proc.on_required_fail.max_retries == 3


def test_runs_to_completion_firing_lock_and_orbit_only():
    """Scene run with a fake registry: every step succeeds. The park reaches
    the end firing nav_panel_target + sc_assist_orbit (and the throttle/scoop/
    wait scaffolding) — and NEVER a jump step (none exist to fire)."""
    proc = _park()
    fired = []

    def make(name):
        def fn(ctx, **params):
            fired.append(name)
            return True
        return fn

    actions = {s.action for s in proc.steps}
    registry = {a: make(a) for a in actions}
    ctx = StepContext(
        sender=FakeSender(), sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(in_supercruise=True))
    result = run_procedure(proc, ctx, registry=registry)
    assert result.aborted is False
    assert "nav_panel_target" in fired
    assert "sc_assist_orbit" in fired
    # no jump-half step ever entered the run
    assert "engage_jump" not in fired
    assert "target_next_route" not in fired


def test_required_lock_failure_aborts_after_bounded_retries_no_jump():
    """If the required star lock can't be established, the park aborts (human
    eyes) after its bounded retries — it never falls through to a jump, because
    there is no jump step. Event-gate-needs-state-check discipline: the lock
    fails deterministically here (no event, state never satisfied)."""
    proc = _park()
    fired = []

    def make(name):
        def fn(ctx, **params):
            fired.append(name)
            return name != "nav_panel_target"   # the required lock always fails
        return fn

    actions = {s.action for s in proc.steps}
    registry = {a: make(a) for a in actions}
    ctx = StepContext(
        sender=FakeSender(), sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(in_supercruise=True))
    result = run_procedure(proc, ctx, registry=registry)
    assert result.aborted is True
    # retried the lock (1 + max_retries attempts), never reached/created a jump
    assert fired.count("nav_panel_target") == 1 + proc.on_required_fail.max_retries
    assert "engage_jump" not in fired
