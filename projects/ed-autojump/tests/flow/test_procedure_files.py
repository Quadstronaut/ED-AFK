from pathlib import Path

from ed_core.flow.loader import load_procedures, validate_procedure
from ed_autojump.flow.steps import STEP_REGISTRY

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def test_all_procedures_load_and_validate():
    procs = load_procedures(PROC_DIR)
    # exploration.toml is now a first-class scene (flow-redesign #2/#13-15).
    assert {"honk", "arrival", "startup", "smack_recovery",
            "traversal", "exploration"} <= set(procs)
    errors = []
    for proc in procs.values():
        errors += validate_procedure(proc, known_actions=STEP_REGISTRY.keys())
    assert errors == [], errors


def test_honk_holds_primary_fire_not_the_fss_bind():
    """ROOT CAUSE 2026-06-06 (manual_honk_probe.py, live experiment): the
    ExplorationFSSDiscoveryScan bind only works INSIDE FSS mode — held 12s
    from the cockpit it does nothing, which is why FSSDiscoveryScan never
    appeared in any journal all day. The cockpit honk is the FIRE-GROUP
    trigger: PrimaryFire held in analysis mode fired the honk in 5.0s.
    Analysis mode must gate the hold — PrimaryFire in COMBAT HUD is weapons."""
    procs = load_procedures(PROC_DIR)
    steps = procs["honk"].steps
    assert steps[0].action == "ensure_analysis_mode" and steps[0].required
    hold = steps[1]
    assert hold.action == "hold_until_event"
    assert hold.params["bind"] == "PrimaryFire"
    assert hold.params["event"] == "FSSDiscoveryScan"


def test_arrival_does_not_orient_or_jump():
    """REWIRE 2026-06-27 (flow-redesign #1): the jump TAIL is GONE from arrival —
    traversal owns the onward hop (run_arrival_then_branch chains it), so arrival
    must NOT orient or jump. Replaces the old test_arrival_orient_and_jump_are_
    required, whose contract (arrival ends with orient_compass + engage_jump)
    was exactly the double-jump bug."""
    procs = load_procedures(PROC_DIR)
    actions = [s.action for s in procs["arrival"].steps]
    forbidden = {"nav_panel_target", "sc_assist_orbit", "wait", "explore",
                 "station_strand_recovery", "target_next_route", "orient_compass",
                 "orient_widget_ring", "engage_jump", "engage_jump_clearance"}
    assert set(actions).isdisjoint(forbidden), (
        f"arrival must not orient/jump/get-around; found {set(actions) & forbidden}")


def test_arrival_is_exactly_throttle_scoop_star():
    """The new arrival contract (acceptance): the action list is EXACTLY
    [set_throttle, scoop_refuel, nav_supercruise_star]; scoop refuel_below==1.0
    (always top off); nav_supercruise_star required; parallel_tracks==(honk,);
    retry_from==scoop."""
    arrival = load_procedures(PROC_DIR)["arrival"]
    assert [s.action for s in arrival.steps] == [
        "set_throttle", "scoop_refuel", "nav_supercruise_star"]
    scoop = next(s for s in arrival.steps if s.action == "scoop_refuel")
    # OPERATOR 2026-07-06 final: 0.75 = scoop under three-quarter tank
    # (supersedes same-day 1.0 restore; council-A's 0.50 drained tank live).
    # The destination top-off (0.99) stays in route_complete_park.toml.
    assert scoop.params.get("refuel_below") == 0.75
    star = next(s for s in arrival.steps if s.action == "nav_supercruise_star")
    assert star.required is True
    assert arrival.parallel_tracks == ("honk",)
    assert arrival.on_required_fail.retry_from == "scoop_refuel"
    # OPERATOR 2026-07-06 (run 085221, "we do shit once"): transient reads are
    # absorbed IN-STEP; the procedure retry is ONE re-settle pass, then abort.
    assert arrival.on_required_fail.max_retries == 1


def test_exploration_loads_and_is_a_bounded_loop():
    """exploration.toml (OPERATOR LAYOUT 2026-07-07): the loop-head skip_to
    exits to target_next_route, the set_throttle back-edge loops to
    nav_supercruise_unexplored, and the operator's own jump tail
    (orient -> widget -> throttle 100 -> engage_jump_clearance) is TERMINAL —
    the tour jumps out itself now (supersedes the no-jump-in-exploration
    contract; the orchestrator's exploration->traversal chain is redundant
    but harmless: traversal re-locks and the clearance loop fails closed)."""
    exp = load_procedures(PROC_DIR)["exploration"]
    head = next(s for s in exp.steps if s.action == "nav_supercruise_unexplored")
    assert head.skip_to == "target_next_route"
    back = next(s for s in exp.steps if s.loop_to is not None)
    assert back.action == "set_throttle"
    assert back.loop_to == "nav_supercruise_unexplored"
    assert exp.steps[-1].action == "engage_jump_clearance"
    assert exp.steps[-1].required is True
    # validates clean against the ed_autojump registry
    assert validate_procedure(exp, known_actions=STEP_REGISTRY.keys()) == []
