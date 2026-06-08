"""Wiring tests for startup.toml structure.

Original (2026-06-07 operator redesign): pips first in both lanes, recovery
clears the star (lock row 0 -> SC -> SC-assist orbit) before the hop lock,
and the 13s clearance wait is the retry anchor for everything after it.

Updated (2026-06-08 council Fix 3): pitch_compass(until=behind) REMOVED from
the recovery lane — it caused the "pitched 180° away for no reason" incident
on the Wolf 359 no-route fresh login, and was already removed from arrival.toml
in the 2026-06-07 redesign. SC-assist orbit + the 13s clearance burn are
sufficient (proven live by arrival). See test_startup_recovery_has_no_pitch_astern."""

from pathlib import Path

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.loader import load_procedures
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


# ---- pips_engines step ------------------------------------------------------

def test_pips_engines_resets_then_maxes_engines():
    sleeps = []
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda t: sleeps.append(t))
    ok = STEP_REGISTRY["pips_engines"](ctx)
    assert ok is True
    # Reset to 2/2/2 first, then 4x ENG: 4 = the pip cap, extra presses are
    # in-game no-ops so over-pressing can never misallocate.
    assert s.actions() == (["ResetPowerDistribution"]
                           + ["IncreaseEnginesPower"] * 4)
    # Inter-press sleeps: one settle before each of the 4 IncreaseEnginesPower
    # presses (WHY: PAUSE=0 back-to-back SendInput drops presses, live-confirmed
    # by pip_probe.py 2026-06-08).
    assert len(sleeps) == 4
    assert all(t > 0 for t in sleeps)


def test_pips_engines_fails_clean_on_missing_bind():
    sleeps = []
    s = FakeSender(unbound={"IncreaseEnginesPower"})
    ctx = StepContext(sender=s, sleeper=lambda t: sleeps.append(t))
    ok = STEP_REGISTRY["pips_engines"](ctx)
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
    """Fix 3 (2026-06-08 council): pitch_compass(until=behind) removed from the
    recovery lane. The step caused the Wolf 359 "pitched 180° away for no reason"
    incident and was already removed from arrival's recovery in the 2026-06-07
    redesign. SC-assist orbit + 13s clearance burn clear the star without it
    (arrival proves this live). The rest of the recovery lane is unchanged."""
    actions = [s.action for s in _startup().steps]
    assert actions[7:] == [
        "target_ahead", "set_throttle", "nav_panel_target",
        "pips_engines", "engage_supercruise",
        "orient_compass",      # nose back on the star before the assist
        "sc_assist_orbit",
        "target_next_route", "set_throttle",
        "reset_power_distribution",   # pip-normalise after the post-SC throttle-100
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


def test_startup_recovery_has_no_pitch_astern():
    """Fix 3 REGRESSION GUARD: pitch_compass must not re-appear in startup.toml.
    The 'pitch star astern' step caused the Wolf 359 180°-away incident and was
    already removed from arrival. Explicit guard against re-introduction."""
    proc = _startup()
    assert "pitch_compass" not in [s.action for s in proc.steps]
