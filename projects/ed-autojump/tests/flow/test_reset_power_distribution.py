"""reset_power_distribution step (operator request 2026-06-07): a best-effort
pip-normalise tap that fires immediately AFTER the post-SC-entry throttle-100,
so every supercruise leg starts from a balanced 2/2/2 power distribution.

Two halves:
  1. the step itself presses ResetPowerDistribution (Down arrow in the live
     preset) 3 times with inter-press sleeps, best-effort like set_throttle.
     WHY: a single press fired at PAUSE=0 immediately after the prior keypress
     was silently dropped by ED (pip_probe.py live test 2026-06-08). 3 presses
     with spacers are idempotent (already-balanced pips are a no-op) but robust.
  2. placement — each procedure that enters supercruise and then throttles 100
     has the reset tap immediately after that throttle-100 (real TOMLs loaded).
"""

from pathlib import Path

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.loader import load_procedures
from ed_autojump.flow.steps import INPUT_EXCLUSIVE_ACTIONS, STEP_REGISTRY
from ed_autojump.binds_validate import REQUIRED_ACTIONS, validate_live_binds
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"
BINDS = (Path(__file__).resolve().parents[2]
         / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds")


def _ctx_with_sleeper():
    """Return (ctx, sender, sleeps) with a fake sleeper that records calls."""
    sleeps = []
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda t: sleeps.append(t))
    return ctx, s, sleeps


# ---- the step ---------------------------------------------------------------

def test_reset_power_distribution_presses_the_action():
    """Default presses=3: fires ResetPowerDistribution 3 times."""
    ctx, s, sleeps = _ctx_with_sleeper()
    ok = STEP_REGISTRY["reset_power_distribution"](ctx)
    assert ok is True
    assert s.actions() == ["ResetPowerDistribution"] * 3


def test_reset_power_distribution_sleeper_called_before_and_between():
    """A pre-press settle + an inter-press settle between each subsequent press.
    presses=3 -> 1 pre-settle + 2 inter-press sleeps = 3 sleeper calls total."""
    ctx, s, sleeps = _ctx_with_sleeper()
    STEP_REGISTRY["reset_power_distribution"](ctx)
    # 1 pre-settle (before first press) + 2 inter-press sleeps (between press 1-2, 2-3)
    assert len(sleeps) == 3
    assert all(t > 0 for t in sleeps)


def test_reset_power_distribution_fails_clean_on_missing_bind():
    """Best-effort: an unbound action returns False without raising."""
    sleeps = []
    s = FakeSender(unbound={"ResetPowerDistribution"})
    ctx = StepContext(sender=s, sleeper=lambda t: sleeps.append(t))
    ok = STEP_REGISTRY["reset_power_distribution"](ctx)
    assert ok is False


def test_reset_power_distribution_is_registered():
    assert "reset_power_distribution" in STEP_REGISTRY


def test_reset_power_distribution_is_not_input_exclusive():
    # A plain arrow tap like set_throttle / pips_engines — NOT a UI macro, so
    # it must not pause the heat watchdog.
    assert "reset_power_distribution" not in INPUT_EXCLUSIVE_ACTIONS


# ---- bind contract ----------------------------------------------------------

def test_reset_power_distribution_action_is_required_and_bound():
    assert "ResetPowerDistribution" in REQUIRED_ACTIONS
    # The live preset binds it (Down arrow) -> validation passes.
    validate_live_binds(BINDS)


# ---- placement: reset immediately after the post-SC throttle-100 ------------

def _actions(proc):
    return [s.action for s in proc.steps]


def _assert_reset_follows_sc_throttle(actions, *, throttle_index):
    """The step at throttle_index is the post-SC-entry set_throttle(100); the
    next step must be reset_power_distribution."""
    assert actions[throttle_index] == "set_throttle"
    assert actions[throttle_index + 1] == "reset_power_distribution"


def test_startup_resets_after_post_sc_throttle():
    # Recovery lane: ... engage_supercruise -> orient -> sc_assist_orbit ->
    # target_next_route -> set_throttle(100) -> reset_power_distribution.
    actions = _actions(load_procedures(PROC_DIR)["startup"])
    # The reset sits between the engage_supercruise lane's throttle-100 and the
    # 13s clearance wait (retry anchor).
    i = _index_of_reset_after_sc(actions, sc_step="engage_supercruise")
    assert actions[i - 1] == "set_throttle"


def test_smack_recovery_resets_after_post_sc_throttle():
    actions = _actions(load_procedures(PROC_DIR)["smack_recovery"])
    i = _index_of_reset_after_sc(actions, sc_step="engage_supercruise")
    assert actions[i - 1] == "set_throttle"


def test_arrival_resets_after_post_sc_throttle():
    # arrival has NO engage_supercruise step — the ship arrives in supercruise
    # via FSDJump. The reset follows the first (and only) throttle-100, which is
    # the supercruise-leg burn after the hop lock. (Flagged ambiguous in the
    # TOML for operator confirmation.)
    actions = _actions(load_procedures(PROC_DIR)["arrival"])
    ti = actions.index("set_throttle", actions.index("target_next_route"))
    _assert_reset_follows_sc_throttle(actions, throttle_index=ti)


def _index_of_reset_after_sc(actions, *, sc_step):
    """Index of the reset_power_distribution that follows the throttle-100 that
    itself follows the supercruise entry (sc_step). Asserts that ordering."""
    sc = actions.index(sc_step)
    # First set_throttle(100) AFTER the SC entry, then the reset right after it.
    ti = actions.index("set_throttle", sc)
    assert actions[ti + 1] == "reset_power_distribution", actions
    return ti + 1


# ---- negative: procedures that do NOT enter SC get no reset -----------------

def test_dock_resume_has_no_reset_power_distribution():
    # dock_resume launches into NORMAL space (no SC entry) and burns toward the
    # hyperspace jump; that throttle-100 is not a post-SC-entry burn.
    assert "reset_power_distribution" not in _actions(
        load_procedures(PROC_DIR)["dock_resume"])


def test_route_complete_park_has_no_reset_power_distribution():
    # route_complete_park never throttles up (only set_throttle(0)).
    assert "reset_power_distribution" not in _actions(
        load_procedures(PROC_DIR)["route_complete_park"])


def test_startup_first_lane_throttle_has_no_reset():
    # The first-try lane's set_throttle(100) at the top is a HYPERSPACE-jump
    # setup, NOT a supercruise entry -> no reset right after it.
    actions = _actions(load_procedures(PROC_DIR)["startup"])
    first_throttle = actions.index("set_throttle")
    assert actions[first_throttle + 1] != "reset_power_distribution"
