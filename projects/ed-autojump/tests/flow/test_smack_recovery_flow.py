"""Wiring tests for the operator-dictated smack_recovery v7 (2026-06-07):
throttle 0 -> honk (parallel) -> pips -> star lock -> throttle 100 -> pitch
180 -> cooldown gate -> DESELECT (star astern, T clears) -> charge until
LIVE (spawns the escape vector) -> orient to the vector -> hold to SC
entry -> hop lock (retry anchor) -> throttle -> 13s -> orient -> jump.
Real-space failures restart at step 0; in-supercruise failures return to
the hop lock."""

from pathlib import Path

from ed_autojump.flow.loader import load_procedures

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
