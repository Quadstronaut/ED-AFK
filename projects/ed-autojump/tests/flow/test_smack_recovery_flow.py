"""Wiring tests for smack_recovery v8 ALL-CV (OPERATOR ORDER 2026-07-06:
"NO MORE OLD BLIND BULLSHIT ... WIRE ALL CV").

v8 = the operator's own flown recovery: throttle 0 -> 75% burn through the
flip -> pitch the star OFF-SCREEN by CV brightness (pitch_star_off — no lock,
no panel, no compass) -> cooldown gate -> charge until LIVE (spawns the
world-space ESCAPE VECTOR sky marker) -> throttle 100 -> center the marker
and ride it to SupercruiseEntry (orient_escape_vector) -> hop lock (retry
anchor) -> throttle -> 13s -> orient -> jump.

Supersedes the v7 shape (star-lock nav_panel_target blind walk + compass
escape dance) — REFUTED live 2026-07-06: the walk burned rows hunting a name
(run 235430) and the escape vector is NOT a compass element (operator-flown
capture, fixtures in tests/fixtures/smack/). Real-space failures restart at
step 0; in-supercruise failures return to the hop lock (unchanged operator
law)."""

from pathlib import Path
from types import SimpleNamespace

from ed_core.flow.context import StepContext
from ed_core.flow.interpreter import run_procedure
from ed_core.flow.loader import load_procedures
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def _smack():
    return load_procedures(PROC_DIR)["smack_recovery"]


def test_v8_step_order():
    actions = [s.action for s in _smack().steps]
    assert actions == [
        "set_throttle",           # 0  throttle 0
        "set_throttle",           # 1  75% burn through the flip (operator order)
        "pitch_star_off",         # 2  CV brightness — star off-screen, no lock
        "wait_cooldown_clear",    # 3  FsdCooldown flag gate
        "engage_supercruise",     # 4  until_charging: live charge spawns the marker
        "set_throttle",           # 5  100 — ride the vector out
        "orient_escape_vector",   # 6  CV sky marker -> center -> SC entry
        "target_next_route",      # 7  hop lock — SC-segment retry anchor
        "set_throttle",           # 8  100 again (SC entry resets throttle)
        "wait",                   # 8.5 13s clear of the star
        "orient_compass",         # 9
        "orient_widget_ring",     # 10
        "engage_jump",            # 11
        "hold_alignment",         # 12
    ]


def test_no_blind_macros_remain():
    """THE OPERATOR ORDER: no nav_panel_target / target_ahead / compass escape
    dance anywhere in the smack scene. CV or nothing."""
    actions = {s.action for s in _smack().steps}
    assert "nav_panel_target" not in actions
    assert "target_ahead" not in actions
    assert "pitch_compass" not in actions


def test_honk_rides_along_in_parallel():
    assert _smack().parallel_tracks == ("honk",)


def test_retry_split_real_space_vs_supercruise():
    proc = _smack()
    # "if fail in real space go to 0": policy entry = the FIRST set_throttle.
    assert proc.on_required_fail.retry_from == "set_throttle"
    assert proc.index_of_action("set_throttle") == 0
    # "if fail in supercruise go to the hop lock": the one anchor.
    anchors = [i for i, s in enumerate(proc.steps) if s.retry_anchor]
    assert len(anchors) == 1
    assert proc.steps[anchors[0]].action == "target_next_route"
    # engage_supercruise sits BEFORE the anchor -> its failure restarts at 0.
    assert proc.index_of_action("engage_supercruise") < anchors[0]


def test_escape_segment_charge_then_cv_marker_orient():
    """The charge spawns the WORLD-SPACE escape-vector marker; the CV orient
    centers it and rides to entry. until_charging semantics unchanged from
    the operator's v7 dictation."""
    proc = _smack()
    actions = [s.action for s in proc.steps]
    sc_i = actions.index("engage_supercruise")
    assert proc.steps[sc_i - 1].action == "wait_cooldown_clear"
    sc = proc.steps[sc_i]
    assert sc.params["until_charging"] is True   # done = live charge, NOT entry
    assert sc.params["presses"] == 3
    assert sc.params["between_press_s"] == 15.0
    assert sc.params["max_charge_s"] == 240.0
    assert sc.required is True
    # throttle 100 then the CV marker orient (holds to SupercruiseEntry itself)
    assert proc.steps[sc_i + 1].action == "set_throttle"
    assert proc.steps[sc_i + 1].params["pct"] == 100
    orient = proc.steps[sc_i + 2]
    assert orient.action == "orient_escape_vector"
    assert orient.required is True


def test_pitch_star_off_before_cooldown_gate():
    """Pitch-star-first (operator law): the CV pitch-away precedes the
    cooldown wait and the SC press; it is required (fail-closed, no blind
    fallback)."""
    proc = _smack()
    actions = [s.action for s in proc.steps]
    pitch_i = actions.index("pitch_star_off")
    assert proc.steps[pitch_i].required is True
    assert pitch_i < actions.index("wait_cooldown_clear")
    assert pitch_i < actions.index("engage_supercruise")


def test_first_throttle_is_zero_then_burn_before_the_pitch():
    proc = _smack()
    throttles = [s.params["pct"] for s in proc.steps if s.action == "set_throttle"]
    assert throttles == [0, 75, 100, 100]
    # the 75 burn lands BEFORE the pitch (operator: burn through the flip)
    actions = [s.action for s in proc.steps]
    assert actions.index("pitch_star_off") > 1


# ---- state-aware retry: the toml carries the SC override ----------------------

def test_toml_carries_supercruise_retry_key_at_the_anchor():
    proc = _smack()
    rfs = proc.on_required_fail.retry_from_if_supercruise
    assert rfs == "target_next_route"
    anchors = [i for i, s in enumerate(proc.steps) if s.retry_anchor]
    assert len(anchors) == 1
    assert proc.index_of_action(rfs) == anchors[0]


# ---- scene: a pre-anchor CV fail routes by SC vs real space -------------------

def _scene_run(*, in_supercruise):
    """Run the live smack_recovery procedure with a fake registry: every step
    succeeds EXCEPT orient_escape_vector (PRE-anchor), which fails once then
    succeeds — the first required fail drives the retry decision."""
    proc = _smack()
    records = []
    state = {"failed": False}

    def make(name):
        def fn(ctx, **params):
            if name == "orient_escape_vector" and not state["failed"]:
                state["failed"] = True
                return False
            return True
        return fn

    actions = {s.action for s in proc.steps}
    registry = {a: make(a) for a in actions}
    status = SimpleNamespace(in_supercruise=in_supercruise)
    ctx = StepContext(
        sender=FakeSender(), sleeper=lambda s: None,
        status_supplier=lambda: status,
        record=lambda kind, payload: records.append((kind, payload)),
    )
    run_procedure(proc, ctx, registry=registry)
    resumes = [p["resume_at"] for k, p in records if k == "ProcedureRetry"]
    return resumes[0] if resumes else None


def test_scene_pre_anchor_cv_fail_in_supercruise_resumes_at_hop_lock():
    assert _scene_run(in_supercruise=True) == "target_next_route"


def test_scene_pre_anchor_cv_fail_in_real_space_resumes_at_throttle():
    assert _scene_run(in_supercruise=False) == "set_throttle"
