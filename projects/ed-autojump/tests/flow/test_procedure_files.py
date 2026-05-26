from pathlib import Path

from ed_autojump.flow.loader import load_procedures, validate_procedure
from ed_autojump.flow.steps import STEP_REGISTRY

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def test_all_procedures_load_and_validate():
    procs = load_procedures(PROC_DIR)
    assert {"honk", "arrival", "startup", "smack_recovery"} <= set(procs)
    errors = []
    for proc in procs.values():
        errors += validate_procedure(proc, known_actions=STEP_REGISTRY.keys())
    assert errors == [], errors


def test_arrival_orient_and_jump_are_required():
    procs = load_procedures(PROC_DIR)
    arrival = procs["arrival"]
    required = {s.action for s in arrival.steps if s.required}
    assert {"orient_compass", "engage_jump"} <= required
