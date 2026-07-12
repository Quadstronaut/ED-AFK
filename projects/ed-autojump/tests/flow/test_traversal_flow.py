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

# The operator-verbatim action order (OPERATOR LAYOUT 2026-07-12, live-tuned).
# NOTE the throttle profile: full burn after the target lock, then EASE TO 75 for
# the orient window (operator anti-coast: at full throttle the ship drifts into
# the arrival star during a slow orient and star-smacks), then back to full for
# the jump.
EXPECTED_ACTIONS = [
    "wait",                     # 0  operator pacing (3.33s) pre-orbit settle
    "wait_sc_assist_orbiting",  # 1  CV orbit settle from the prior scene
    "confirm_orbiting",         # 2  CV confirm the orbit acquired
    "wait",                     # 3  operator pacing (13.0s)
    "target_next_route",        # 4  lock next hop — RETRY ANCHOR via retry_from
    "set_throttle",             # 5  full burn (100)
    "wait",                     # 6  operator pacing (3.33s)
    "set_throttle",             # 7  ease to 75 for orient (anti coast-into-star)
    "orient_compass",           # 8  coarse align
    "orient_widget_ring",       # 9  fine align
    "set_throttle",             # 10 back to full for the jump
    "engage_jump_clearance",    # 11 TERMINAL jump + clearance loop
]


def _traversal():
    return load_procedures(PROC_DIR)["traversal"]


# ---- AC-1 / AC-2: file exists, correct name, exact sequence -------------------

def test_traversal_file_exists_and_named_traversal():
    """AC-1: the file is present under procedures/ and its procedure name
    (filename stem) is 'traversal'."""
    assert (PROC_DIR / "traversal.toml").is_file()
    assert _traversal().name == "traversal"


def test_step_order_is_operator_verbatim():
    """AC-2 (INV-1): the wired action list matches the operator sequence exactly."""
    assert [s.action for s in _traversal().steps] == EXPECTED_ACTIONS


# ---- AC-3: required flags --------------------------------------------------

def test_required_flags_match_the_contract():
    """AC-3 (INV-2): exactly the four gates are required; the pacing waits, the
    CV orbit settle/confirm and every throttle step are non-required."""
    proc = _traversal()
    assert {s.action for s in proc.steps if s.required} == {
        "target_next_route",
        "orient_compass",
        "orient_widget_ring",
        "engage_jump_clearance",
    }
    # the orbit settle/confirm, the pacing waits and the throttle steps tolerate
    # failure (must NOT gate the lane).
    non_required = [s.action for s in proc.steps if not s.required]
    assert non_required == [
        "wait", "wait_sc_assist_orbiting", "confirm_orbiting", "wait",
        "set_throttle", "wait", "set_throttle", "set_throttle",
    ]


# ---- AC-4: operator literals -----------------------------------------------

def test_operator_literal_params_preserved():
    """AC-4 (INV-3): the operator's authored pacing waits and throttle profile
    are byte-faithful. Three pacing waits (3.33s pre-orbit, 13.0s post-confirm,
    3.33s pre-orient); the anti-coast throttle profile 100 -> 75 (orient) -> 100
    (jump)."""
    proc = _traversal()
    waits = [s.params["s"] for s in proc.steps if s.action == "wait"]
    assert waits == [3.33, 13.0, 3.33]
    throttles = [s.params["pct"] for s in proc.steps if s.action == "set_throttle"]
    assert throttles == [100, 75, 100]


# ---- AC-5: no extra waits / no hold_alignment / no honk --------------------

def test_pacing_waits_no_tail_no_parallel():
    """AC-5 (INV-1): three operator pacing waits (2026-07-12 live layout);
    engage_jump_clearance is terminal (no hold_alignment after it); no
    parallel honk track."""
    proc = _traversal()
    waits = [s for s in proc.steps if s.action == "wait"]
    assert len(waits) == 3
    # engage_jump_clearance is the last step -> nothing (no hold_alignment) after.
    assert proc.steps[-1].action == "engage_jump_clearance"
    assert "hold_alignment" not in {s.action for s in proc.steps}
    # honk is Arrival's job — traversal launches no parallel track.
    assert proc.parallel_tracks == ()


# ---- AC-6: retry policy ----------------------------------------------------

def test_retry_policy_anchors_on_target_next_route():
    """AC-6 (INV-5): the retry lane resumes at target_next_route (index 4 in the
    2026-07-12 layout), 3 retries, 2.0s backoff."""
    proc = _traversal()
    assert proc.on_required_fail.retry_from == "target_next_route"
    assert proc.on_required_fail.max_retries == 3
    assert proc.on_required_fail.backoff_s == 2.0
    assert proc.index_of_action("target_next_route") == 4


# ---- AC-7: no retry_anchor / no SC override --------------------------------

def test_no_retry_anchor_and_no_supercruise_override():
    """AC-7 (INV-6): traversal is a single steady-state lane — no per-step
    retry_anchor and no state-aware SC override (that's smack_recovery's)."""
    proc = _traversal()
    assert not any(s.retry_anchor for s in proc.steps)
    assert proc.on_required_fail.retry_from_if_supercruise is None


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
    result, records = _scene_run(orient_fail_mode="once")
    retries = [p for k, p in records if k == "ProcedureRetry"]
    assert len(retries) == 1
    assert retries[0]["resume_at"] == "target_next_route"
    assert retries[0]["resume_index"] == 4
    # one miss, then the lane recovers and finishes the jump.
    assert result.completed is True
    assert result.aborted is False
    assert result.retries == 1


def test_scene_orient_fail_always_aborts_before_the_jump():
    """AC-11 (INV-7): orient_compass failing every attempt exhausts the 3
    retries and ABORTS — engage_jump_clearance is NEVER recorded, proving a
    failed orient can never reach the FSD jump (fail closed)."""
    result, records = _scene_run(orient_fail_mode="always")
    assert result.aborted is True
    assert result.completed is False
    # the terminal jump step never ran.
    assert "engage_jump_clearance" not in [s.action for s in result.steps]
    # exactly max_retries (3) ProcedureRetry records, then a ProcedureAborted.
    assert sum(1 for k, _ in records if k == "ProcedureRetry") == 3
    assert any(k == "ProcedureAborted" for k, _ in records)
