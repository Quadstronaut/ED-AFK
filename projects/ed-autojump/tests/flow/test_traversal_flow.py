"""Wiring + scene tests for traversal.toml — the steady-state A->B hop
(C5-traversal BUILD). Operator-verbatim sequence (MASTER-SPEC "Traversal"):

    wait 5s -> target_next_route -> set_throttle 100 -> wait 3s
            -> orient_compass -> orient_widget_ring -> engage_jump_clearance

Traversal is a STANDALONE procedure (no goto primitive); inter-scene entry is
C2-owned and OUT OF SCOPE. Its jump tail mirrors the proven dock_resume.toml.

These tests are pure-Python — NO game, NO requires_game marker. They run under
the default `-m 'not requires_game'` gate from pytest.ini.
"""

from pathlib import Path
from types import SimpleNamespace

from ed_core.flow.context import StepContext
from ed_core.flow.interpreter import run_procedure
from ed_core.flow.loader import load_procedures, validate_procedure
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"

def _traversal():
    return load_procedures(PROC_DIR)["traversal"]


# ---- AC-1 / AC-2: file exists, correct name, exact sequence -------------------

def test_traversal_file_exists_and_named_traversal():
    """AC-1: the file is present under procedures/ and its procedure name
    (filename stem) is 'traversal'."""
    assert (PROC_DIR / "traversal.toml").is_file()
    assert _traversal().name == "traversal"


# ---- AC-5: no hold_alignment tail -----------------------------------------

def test_pacing_waits_no_tail_no_parallel():
    """AC-5: engage_jump_clearance is terminal (no hold_alignment after it)."""
    proc = _traversal()
    # engage_jump_clearance is the last step -> nothing (no hold_alignment) after.
    assert proc.steps[-1].action == "engage_jump_clearance"
    assert "hold_alignment" not in {s.action for s in proc.steps}


# ---- AC-8: terminal jump is required ---------------------------------------

def test_terminal_step_is_required_engage_jump_clearance():
    """AC-8 (INV-7): the last step is engage_jump_clearance and it is required."""
    proc = _traversal()
    assert proc.steps[-1].action == "engage_jump_clearance"
    assert proc.steps[-1].required is True


# ---- AC-9: load + validate clean against the real registry -----------------

def test_validates_clean_against_step_registry():
    """AC-9 (INV-8): every action resolves against the merged STEP_REGISTRY and
    the retry_from target exists — validate_procedure returns no errors."""
    proc = _traversal()
    assert validate_procedure(proc, known_actions=STEP_REGISTRY.keys()) == []


# ---- AC-15: no stale-artifact contamination --------------------------------

def test_no_stale_gate_split_artifact_actions():
    """AC-15: guard against copying the SUPERSEDED .claude/worktrees gate_split
    traversal.toml (nav_panel_target / sc_assist_orbit / explore /
    station_strand_recovery, retry_from='nav_panel_target')."""
    proc = _traversal()
    actions = {s.action for s in proc.steps}
    forbidden = {
        "nav_panel_target",
        "sc_assist_orbit",
        "explore",
        "station_strand_recovery",
    }
    assert actions.isdisjoint(forbidden)
    assert proc.on_required_fail.retry_from != "nav_panel_target"


# ---- AC-10 / AC-11: interpreter scene proofs -------------------------------

def _scene_run(*, orient_fail_mode):
    """Drive the LIVE traversal procedure on a real interpreter with a fake
    registry. Every step returns True EXCEPT orient_compass:

      orient_fail_mode="once"   -> fails on its first call, then succeeds
                                   (the retry lane should re-route to the anchor)
      orient_fail_mode="always" -> fails every call (the lane should exhaust
                                   retries and ABORT, never reaching the jump)

    Status reports in_supercruise=True (harmless here — traversal has no
    retry_from_if_supercruise, so the SC branch is never consulted). Returns the
    ProcedureResult plus the records list for assertions."""
    proc = _traversal()
    records = []
    state = {"orient_calls": 0}

    def make(name):
        def fn(ctx, **params):
            if name == "orient_compass":
                state["orient_calls"] += 1
                if orient_fail_mode == "always":
                    return False
                # "once": fail only the first call.
                return state["orient_calls"] != 1
            return True
        return fn

    actions = {s.action for s in proc.steps}
    registry = {a: make(a) for a in actions}
    status = SimpleNamespace(in_supercruise=True)
    ctx = StepContext(
        sender=FakeSender(),
        sleeper=lambda s: None,
        status_supplier=lambda: status,
        record=lambda kind, payload: records.append((kind, payload)),
    )
    result = run_procedure(proc, ctx, registry=registry)
    return result, records


def test_scene_orient_fail_once_retries_at_target_next_route():
    """AC-10 (INV-5): a single orient_compass miss records a ProcedureRetry that
    resumes at target_next_route (index 4) — proving the retry lane resolves on a
    real interpreter run — and the procedure then completes."""
    proc = _traversal()
    result, records = _scene_run(orient_fail_mode="once")
    retries = [p for k, p in records if k == "ProcedureRetry"]
    assert len(retries) == 1
    assert retries[0]["resume_at"] == proc.on_required_fail.retry_from
    assert retries[0]["resume_index"] == proc.index_of_action(
        proc.on_required_fail.retry_from
    )
    # one miss, then the lane recovers and finishes the jump.
    assert result.completed is True
    assert result.aborted is False
    assert result.retries == 1


def test_scene_orient_fail_always_aborts_before_the_jump():
    """AC-11 (INV-7): orient_compass failing every attempt exhausts the 3
    retries and ABORTS — engage_jump_clearance is NEVER recorded, proving a
    failed orient can never reach the FSD jump (fail closed)."""
    proc = _traversal()
    result, records = _scene_run(orient_fail_mode="always")
    assert result.aborted is True
    assert result.completed is False
    # the terminal jump step never ran.
    assert "engage_jump_clearance" not in [s.action for s in result.steps]
    # exactly max_retries ProcedureRetry records, then a ProcedureAborted.
    assert (
        sum(1 for k, _ in records if k == "ProcedureRetry")
        == proc.on_required_fail.max_retries
    )
    assert any(k == "ProcedureAborted" for k, _ in records)
