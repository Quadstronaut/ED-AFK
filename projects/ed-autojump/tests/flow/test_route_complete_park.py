"""route_complete_park terminal procedure. REBUILT by Council B (2026-07-02,
MASTER-SPEC Docking §3): set_throttle 0, best-effort scoop, CV-confirmed
nav_supercruise_star (replaces the old blind sc_assist_orbit macro AND the
separate nav_panel_target lock — resolves the pre-existing RED divergence
between this file and the old TOML's commented-out nav_panel_target step),
non-required orbit confirm — then STOP. It must NOT carry the jump half
(target_next_route / orient / engage_jump / hold_alignment): there is no
next hop at route end, and re-jumping is exactly the false-abort bug it
fixes."""

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


def test_step_order_is_the_rebuilt_shape():
    actions = [s.action for s in _park().steps]
    assert actions == [
        "set_throttle",           # 1 throttle 0
        "scoop_refuel",           # 2 best-effort pit stop
        "nav_supercruise_star",   # 3 CV-confirmed lock+engage (required)
        "confirm_orbiting",       # 4 non-required D4 orbit confirm
    ]


def test_has_no_jump_or_next_hop_steps():
    """The whole point: NO re-jump at route end."""
    actions = {s.action for s in _park().steps}
    for forbidden in ("target_next_route", "orient_compass",
                      "orient_widget_ring", "engage_jump", "hold_alignment"):
        assert forbidden not in actions, f"{forbidden} must not be in the park"


def test_engages_via_nav_supercruise_star_not_sc_assist_orbit():
    """The rebuild REPLACES sc_assist_orbit with nav_supercruise_star — the
    old blind macro must not be present in the rebuilt procedure."""
    actions = {s.action for s in _park().steps}
    assert "nav_supercruise_star" in actions
    assert "sc_assist_orbit" not in actions
    assert "nav_panel_target" not in actions


def test_nav_supercruise_star_is_required_others_best_effort():
    proc = _park()
    by_action = {s.action: s for s in proc.steps}
    assert by_action["nav_supercruise_star"].required is True   # wrong/unconfirmed lock blocks
    assert by_action["scoop_refuel"].required is False          # degrade-friendly
    assert by_action["confirm_orbiting"].required is False      # D3-dependent observability


def test_honk_rides_along_like_arrival():
    assert _park().parallel_tracks == ("honk",)


def test_retry_anchor_is_the_engage_bounded():
    proc = _park()
    assert proc.on_required_fail.retry_from == "nav_supercruise_star"
    assert proc.on_required_fail.max_retries == 3


def test_runs_to_completion_firing_engage_only():
    """Scene run with a fake registry: every step succeeds. The park reaches
    the end firing nav_supercruise_star (and the throttle/scoop/confirm
    scaffolding) — and NEVER a jump step (none exist to fire)."""
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
    assert "nav_supercruise_star" in fired
    # no jump-half step ever entered the run
    assert "engage_jump" not in fired
    assert "target_next_route" not in fired


def test_required_engage_failure_aborts_after_bounded_retries_no_jump():
    """If the required CV-confirmed engage can't be established, the park
    aborts (human eyes) after its bounded retries — it never falls through to
    a jump, because there is no jump step."""
    proc = _park()
    fired = []

    def make(name):
        def fn(ctx, **params):
            fired.append(name)
            return name != "nav_supercruise_star"   # the required engage always fails
        return fn

    actions = {s.action for s in proc.steps}
    registry = {a: make(a) for a in actions}
    ctx = StepContext(
        sender=FakeSender(), sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(in_supercruise=True))
    result = run_procedure(proc, ctx, registry=registry)
    assert result.aborted is True
    # retried the engage (1 + max_retries attempts), never reached/created a jump
    assert fired.count("nav_supercruise_star") == 1 + proc.on_required_fail.max_retries
    assert "engage_jump" not in fired
