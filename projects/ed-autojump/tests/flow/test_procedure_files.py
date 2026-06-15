from pathlib import Path

from ed_core.flow.loader import load_procedures, validate_procedure
from ed_autojump.flow.steps import STEP_REGISTRY

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def test_all_procedures_load_and_validate():
    procs = load_procedures(PROC_DIR)
    assert {"honk", "arrival", "startup", "smack_recovery"} <= set(procs)
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


def test_arrival_orient_and_jump_are_required():
    procs = load_procedures(PROC_DIR)
    arrival = procs["arrival"]
    required = {s.action for s in arrival.steps if s.required}
    assert {"orient_compass", "engage_jump"} <= required
