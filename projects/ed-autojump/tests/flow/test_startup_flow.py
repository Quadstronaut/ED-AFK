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

from ed_autojump.flow.loader import load_procedures

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"

# NOTE 2026-06-08: pip management (pips_engines / reset_power_distribution) was
# RIPPED from the bot (operator: "we're scrapping it"). The recovery-lane order
# and retry-anchor assertions below diverge from the operator's hand-edited
# startup.toml (two anchors, retry_from = sc_assist_orbit, reordered SC entry)
# and are left RED on purpose — reconciling startup.toml with these wiring tests
# is part of the #31 smacked-startup recovery council redesign, not the pip rip.


# ---- startup.toml wiring ----------------------------------------------------

def _startup():
    return load_procedures(PROC_DIR)["startup"]


def test_first_lane_is_direct_jump():
    actions = [s.action for s in _startup().steps]
    assert actions[:6] == [
        "set_throttle", "target_next_route",
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
