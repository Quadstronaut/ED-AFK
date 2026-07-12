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
    # analysis-mode gate must be present (order is tunable).
    assert any(s.action == "ensure_analysis_mode" for s in steps)
    # the honk hold must be PrimaryFire waiting on FSSDiscoveryScan (present).
    holds = [s for s in steps if s.action == "hold_until_event"]
    assert any(h.params.get("bind") == "PrimaryFire"
               and h.params.get("event") == "FSSDiscoveryScan" for h in holds)


def test_arrival_does_not_orient_or_jump():
    """REWIRE 2026-06-27 (flow-redesign #1): the jump TAIL is GONE from arrival —
    traversal owns the onward hop (run_arrival_then_branch chains it), so arrival
    must NOT orient or jump. Replaces the old test_arrival_orient_and_jump_are_
    required, whose contract (arrival ends with orient_compass + engage_jump)
    was exactly the double-jump bug."""
    procs = load_procedures(PROC_DIR)
    actions = [s.action for s in procs["arrival"].steps]
    forbidden = {"orient_compass", "orient_widget_ring", "engage_jump",
                 "engage_jump_clearance", "nav_panel_target", "sc_assist_orbit",
                 "explore", "station_strand_recovery"}
    assert set(actions).isdisjoint(forbidden), (
        f"arrival must not orient/jump/get-around; found {set(actions) & forbidden}")


def test_exploration_loads_and_is_a_bounded_loop():
    """exploration.toml (OPERATOR LAYOUT 2026-07-07): the loop-head skip_to
    exits to target_next_route, the set_throttle back-edge loops to
    nav_supercruise_unexplored, and the operator's own jump tail
    (orient -> widget -> throttle 100 -> engage_jump_clearance) is TERMINAL —
    the tour jumps out itself now (supersedes the no-jump-in-exploration
    contract; the orchestrator's exploration->traversal chain was REMOVED
    (G2, 2026-07-11) — it double-pressed this tail's jump keys)."""
    exp = load_procedures(PROC_DIR)["exploration"]
    # the tour jumps out itself now: terminal step is the clearance jump (required).
    assert exp.steps[-1].action == "engage_jump_clearance"
    assert exp.steps[-1].required is True
    # validates clean against the ed_autojump registry
    assert validate_procedure(exp, known_actions=STEP_REGISTRY.keys()) == []
