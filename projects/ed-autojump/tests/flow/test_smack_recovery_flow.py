"""Wiring tests for the operator-dictated smack_recovery v7 (2026-06-07):
throttle 0 -> honk (parallel) -> pips -> star lock -> throttle 100 -> pitch
180 -> cooldown gate -> DESELECT (star astern, T clears) -> charge until
LIVE (spawns the escape vector) -> orient to the vector -> hold to SC
entry -> hop lock (retry anchor) -> throttle -> 13s -> orient -> jump.
Real-space failures restart at step 0; in-supercruise failures return to
the hop lock."""

from pathlib import Path
from types import SimpleNamespace

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.interpreter import run_procedure
from ed_autojump.flow.loader import load_procedures
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def _smack():
    return load_procedures(PROC_DIR)["smack_recovery"]


def test_v7_step_order():
    actions = [s.action for s in _smack().steps]
    assert actions == [
        "set_throttle",        # 0  throttle 0
        "pips_engines",        # 0.6 all pips ENG
        "nav_panel_target",    # 1  star = row 0
        "set_throttle",        # 2  throttle 100 (operator: burn through the flip)
        "pitch_compass",       # 3  pitch 180 — hollow dot centered
        "wait_cooldown_clear", # 4  FsdCooldown flag gate
        "target_ahead",        # 4.5 T — star astern, nothing ahead -> clears
        "engage_supercruise",  # 5  until_charging: live charge spawns the vector
        "orient_compass",      # 5.5 center the escape-vector dot
        "hold_alignment",      # 5.6 hold until SupercruiseEntry
        "target_next_route",   # 6  hop lock — SC-segment retry anchor
        "set_throttle",        # 7  100 again (SC entry resets throttle)
        "wait",                # 7.5 13s clear of the star
        "orient_compass",      # 8
        "orient_widget_ring",  # 9
        "engage_jump",         # 10
        "hold_alignment",      # 11
    ]


def test_honk_rides_along_in_parallel():
    # Operator 0.5: honk in case the earlier one missed. Parallel track —
    # an already-honked system must not stall the escape lane.
    assert _smack().parallel_tracks == ("honk",)


def test_retry_split_real_space_vs_supercruise():
    proc = _smack()
    # "if fail in real space go to 0": policy entry = the FIRST set_throttle.
    assert proc.on_required_fail.retry_from == "set_throttle"
    assert proc.index_of_action("set_throttle") == 0
    # "if fail in supercruise go to 6": the hop lock is the one anchor.
    anchors = [i for i, s in enumerate(proc.steps) if s.retry_anchor]
    assert len(anchors) == 1
    assert proc.steps[anchors[0]].action == "target_next_route"
    # engage_supercruise sits BEFORE the anchor -> its failure restarts at 0.
    assert proc.index_of_action("engage_supercruise") < anchors[0]


def test_escape_vector_segment_charge_orient_hold():
    """v7 (operator, 2026-06-07): the smack charge spawns an ESCAPE VECTOR
    the ship must center before ED accepts entry. Deselect first (star is
    perfectly behind, T clears), charge until LIVE, orient to the spawned
    dot, hold until SupercruiseEntry."""
    proc = _smack()
    actions = [s.action for s in proc.steps]
    sc_i = actions.index("engage_supercruise")
    assert proc.steps[sc_i - 1].action == "target_ahead"
    sc = proc.steps[sc_i]
    assert sc.params["until_charging"] is True   # done = live charge, NOT entry
    assert sc.params["presses"] == 3
    assert sc.params["between_press_s"] == 15.0
    assert sc.params["max_charge_s"] == 240.0
    assert sc.required is True
    # orient to the vector, then hold gated on the ENTRY event
    assert proc.steps[sc_i + 1].action == "orient_compass"
    assert proc.steps[sc_i + 1].required is True
    hold = proc.steps[sc_i + 2]
    assert hold.action == "hold_alignment"
    assert hold.params["until_event"] == "SupercruiseEntry"
    assert hold.required is True


def test_first_throttle_is_zero_then_full_burn_before_the_pitch():
    proc = _smack()
    throttles = [s.params["pct"] for s in proc.steps if s.action == "set_throttle"]
    assert throttles == [0, 100, 100]


# ---- state-aware retry: the toml carries the SC override ----------------------

def test_toml_carries_supercruise_retry_key_at_the_anchor():
    """Operator-dictated (2026-06-07 14:24-14:29Z burn): a pre-anchor fail in
    supercruise must resume at the hop lock, not restart the real-space ladder.
    The key targets target_next_route, which IS the one retry_anchor — so the
    SC branch and the post-anchor branch converge on the same step."""
    proc = _smack()
    rfs = proc.on_required_fail.retry_from_if_supercruise
    assert rfs == "target_next_route"
    anchors = [i for i, s in enumerate(proc.steps) if s.retry_anchor]
    assert len(anchors) == 1
    assert proc.index_of_action(rfs) == anchors[0]


# ---- scene: a pre-anchor orient_compass fail routes by SC vs real space ------

def _scene_run(*, in_supercruise):
    """Run the live smack_recovery procedure with a fake registry: every step
    succeeds EXCEPT the first orient_compass (index 8, PRE-anchor), which fails
    once then succeeds — so the first required fail drives the retry decision.
    Status reads report `in_supercruise`. Returns the ProcedureRetry resume
    action recorded by the interpreter."""
    proc = _smack()
    records = []
    state = {"orient_failed": False}

    def make(name):
        def fn(ctx, **params):
            if name == "orient_compass" and not state["orient_failed"]:
                state["orient_failed"] = True
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


def test_scene_pre_anchor_orient_fail_in_supercruise_resumes_at_hop_lock():
    assert _scene_run(in_supercruise=True) == "target_next_route"


def test_scene_pre_anchor_orient_fail_in_real_space_resumes_at_throttle():
    assert _scene_run(in_supercruise=False) == "set_throttle"
