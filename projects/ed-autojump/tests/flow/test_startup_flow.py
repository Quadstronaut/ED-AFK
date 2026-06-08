"""Wiring tests for the 2026-06-07 operator-dictated startup flow: pips first
in both lanes, recovery clears the star (lock row 0 -> pitch astern -> SC ->
SC-assist orbit) before the hop lock, and the 13s clearance wait is the
retry anchor for everything after it."""

from pathlib import Path

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.loader import load_procedures
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


# ---- pips_engines step ------------------------------------------------------

def test_pips_engines_resets_then_maxes_engines():
    s = FakeSender()
    ok = STEP_REGISTRY["pips_engines"](StepContext(sender=s))
    assert ok is True
    # Reset to 2/2/2 first, then 4x ENG: 4 = the pip cap, extra presses are
    # in-game no-ops so over-pressing can never misallocate.
    assert s.actions() == (["ResetPowerDistribution"]
                           + ["IncreaseEnginesPower"] * 4)


def test_pips_engines_fails_clean_on_missing_bind():
    s = FakeSender(unbound={"IncreaseEnginesPower"})
    ok = STEP_REGISTRY["pips_engines"](StepContext(sender=s))
    assert ok is False


# ---- startup.toml wiring ----------------------------------------------------

def _startup():
    return load_procedures(PROC_DIR)["startup"]


def test_first_lane_is_pips_then_direct_jump():
    actions = [s.action for s in _startup().steps]
    assert actions[:7] == [
        "pips_engines", "set_throttle", "target_next_route",
        "orient_compass", "orient_widget_ring", "engage_jump",
        "hold_alignment",
    ]


def test_recovery_lane_clears_the_star_before_the_hop():
    actions = [s.action for s in _startup().steps]
    assert actions[7:] == [
        "target_ahead", "set_throttle", "nav_panel_target", "pitch_compass",
        "pips_engines", "engage_supercruise",
        "orient_compass",      # 12b nose back on the star before the assist
        "sc_assist_orbit",
        "target_next_route", "set_throttle",
        "reset_power_distribution",   # 15b pip-normalise after the post-SC throttle-100
        "wait",
        "orient_compass", "orient_widget_ring", "engage_jump",
        "hold_alignment",
    ]


def test_clearance_wait_is_the_only_retry_anchor():
    proc = _startup()
    anchors = [i for i, s in enumerate(proc.steps) if s.retry_anchor]
    assert len(anchors) == 1
    assert proc.steps[anchors[0]].action == "wait"
    # failures BEFORE the wait restart the lane; the policy entry is the
    # deselect, and three total retries (operator-confirmed 2026-06-07).
    assert proc.on_required_fail.retry_from == "target_ahead"
    assert proc.on_required_fail.max_retries == 3


def test_recovery_pitch_is_best_effort():
    proc = _startup()
    pitch = next(s for s in proc.steps if s.action == "pitch_compass")
    assert pitch.required is False   # a noisy classifier must not burn retries
