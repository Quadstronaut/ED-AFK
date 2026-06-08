"""Wiring tests for startup.toml structure.

2026-06-08 council (normal-space star-ram fix). Two defects fixed and pinned:

(a) The first lane had NO clear-of-star protection: it ran set_throttle(100) ->
    target_next_route -> orient -> engage_jump while a fresh load can sit nose-on
    a nearby star. Stars do NOT mass-lock and ED exposes no star-proximity Status
    flag, so engage_jump's gate gave zero protection — the throttle drove into the
    star. The first lane now opens with the same nav_panel_target(max_rows=3,
    required=false, skip_to=...) distance gate sc_resume / arrival use: CLOSE ->
    SC-entry + orbit get-around BEFORE any throttle; FAR -> skip to the direct
    jump. A close star never gets set_throttle(100) pointed at it.

(b) on_required_fail.retry_from was "sc_assist_orbit", which resolves to the FIRST
    step of that name — PAST engage_supercruise in the recovery lane. The recovery
    therefore tried sc_assist_orbit in NORMAL space, where it guards on
    in_supercruise and refuses, so the get-around never ran. retry_from is now
    "target_ahead" (the recovery lane's true entry) so the recovery re-runs
    engage_supercruise BEFORE sc_assist_orbit.

The pip steps (pips_engines / reset_power_distribution) were RIPPED from the bot
earlier (operator: "we're scrapping it"); they are absent from both lanes here.

pitch_compass(until=behind) is also absent (the Wolf 359 "pitched 180° away"
incident, already removed from arrival) — see test_startup_recovery_has_no_pitch_astern."""

from pathlib import Path

from ed_autojump.flow.loader import load_procedures

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


# ---- startup.toml wiring ----------------------------------------------------

def _startup():
    return load_procedures(PROC_DIR)["startup"]


def test_first_lane_opens_with_clear_of_star_gate():
    """Defect (a): the first lane's FIRST step is the clear-of-star distance gate
    — a non-required, bounded (max_rows=3) nav_panel_target that skips to the
    direct jump when the star is FAR. It runs BEFORE any set_throttle, so a CLOSE
    star never gets the throttle pointed at it."""
    steps = _startup().steps
    gate = steps[0]
    assert gate.action == "nav_panel_target"
    assert gate.required is False
    assert gate.skip_to == "target_next_route"
    assert gate.params.get("max_rows") == 3
    # No throttle before the gate, and the CLOSE path enters supercruise before
    # the first set_throttle (a close star is never throttled at in normal space).
    actions = [s.action for s in steps]
    first_throttle = actions.index("set_throttle")
    first_sc_entry = actions.index("engage_supercruise")
    assert first_sc_entry < first_throttle


def test_first_lane_close_path_is_sc_entry_then_orbit_then_jump():
    """Defect (a): when the gate FINDS a close star it falls through to the
    SC-entry + SC-assist orbit get-around, THEN the shared continuation
    (target_next_route -> set_throttle -> orient -> jump). The FAR star skips
    the get-around straight to target_next_route (index 5)."""
    actions = [s.action for s in _startup().steps]
    assert actions[:11] == [
        "nav_panel_target",            # 0 clear-of-star gate (skip_to=target_next_route)
        "engage_supercruise",          # 1 CLOSE: enter SC (no throttle at the star)
        "nav_panel_target",            # 2 re-lock the star
        "sc_assist_orbit",             # 3 orbit get-around
        "wait",                        # 4 let orbit acquire
        "target_next_route",           # 5 FAR skip lands HERE; lock next hop
        "set_throttle",                # 6 burn (off the star now)
        "orient_compass",              # 7
        "orient_widget_ring",          # 8
        "engage_jump",                 # 9
        "hold_alignment",              # 10
    ]


def test_recovery_lane_clears_the_star_before_the_hop():
    """The recovery lane (retry_from target) clears the star: deselect, throttle,
    engage_supercruise, re-lock, sc_assist_orbit, then lock the hop, 13s clearance
    burn, orient, jump. pitch_compass(until=behind) is absent (Wolf 359 incident).
    Pip steps are absent (ripped). engage_supercruise comes BEFORE sc_assist_orbit
    — the defect (b) fix."""
    actions = [s.action for s in _startup().steps]
    assert actions[11:] == [
        "target_ahead", "set_throttle", "engage_supercruise",
        "nav_panel_target",            # re-lock the star before the assist
        "sc_assist_orbit",
        "wait",                        # let orbit acquire (not an anchor)
        "target_next_route", "set_throttle",
        "wait",                        # clearance burn (the SOLE retry anchor)
        "orient_compass", "orient_widget_ring", "engage_jump",
        "hold_alignment",
    ]


def test_recovery_runs_engage_supercruise_before_sc_assist_orbit():
    """Defect (b): retry_from must land at the recovery lane's TRUE entry so the
    get-around runs engage_supercruise (gain supercruise) BEFORE sc_assist_orbit
    (which guards on in_supercruise and refuses in normal space). retry_from
    resolves to the FIRST step of its name, so it must be target_ahead, NOT
    sc_assist_orbit."""
    proc = _startup()
    assert proc.on_required_fail.retry_from == "target_ahead"
    entry = proc.index_of_action(proc.on_required_fail.retry_from)
    actions = [s.action for s in proc.steps]
    # From the retry entry onward, engage_supercruise precedes sc_assist_orbit.
    tail = actions[entry:]
    assert tail.index("engage_supercruise") < tail.index("sc_assist_orbit")


def test_clearance_wait_is_the_only_retry_anchor():
    proc = _startup()
    anchors = [i for i, s in enumerate(proc.steps) if s.retry_anchor]
    assert len(anchors) == 1
    assert proc.steps[anchors[0]].action == "wait"
    # The anchor is the CLEARANCE BURN wait — the one immediately before the
    # recovery lane's final orient/jump, NOT the post-orbit "let orbit acquire"
    # wait. Failures at/after it return to the burn; failures before it restart
    # from target_ahead. Three total retries (operator-confirmed 2026-06-07).
    anchor_idx = anchors[0]
    actions = [s.action for s in proc.steps]
    assert actions[anchor_idx + 1] == "orient_compass"
    assert proc.on_required_fail.retry_from == "target_ahead"
    assert proc.on_required_fail.max_retries == 3


def test_startup_recovery_has_no_pitch_astern():
    """REGRESSION GUARD: pitch_compass must not re-appear in startup.toml.
    The 'pitch star astern' step caused the Wolf 359 180°-away incident and was
    already removed from arrival. Explicit guard against re-introduction."""
    proc = _startup()
    assert "pitch_compass" not in [s.action for s in proc.steps]
