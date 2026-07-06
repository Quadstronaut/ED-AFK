"""Startup/sc_resume CV rewire (operator 2026-07-05): the clear-of-star CV
distance gate (star_distance_gate), the ORBITING-prompt orbit-acquire wait
(wait_sc_assist_orbiting), the row-0 distance parser, and the rewritten
startup.toml / sc_resume.toml shapes. Pure-Python — no game, no CV engine.
"""

from pathlib import Path

import ed_vision.navpanel_reader as npr
import ed_vision.hud_sc_indicators as hud
from ed_core.flow.context import StepContext
from ed_core.flow.loader import load_procedures
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


# ---- parse_first_row_distance_ls (pure) --------------------------------------

def test_parse_first_row_ls():
    assert npr.parse_first_row_distance_ls(["SOL 504 Ls"]) == 504.0


def test_parse_first_row_comma_and_nospace():
    assert npr.parse_first_row_distance_ls(["ACHENAR 1,234LS"]) == 1234.0


def test_parse_first_row_km_and_mm_convert_to_ls():
    km = npr.parse_first_row_distance_ls(["SOL 2,998 km"])
    assert km is not None and km < 1.0          # parked-at-star close range
    mm = npr.parse_first_row_distance_ls(["SOL 3.0 Mm"])
    assert mm is not None and 0.001 < mm < 1.0


def test_parse_first_row_unreadable_top_line_is_none_never_row1():
    """FAIL-CLOSED CORE: a garbled row 0 must return None even when row 1 has a
    clean FAR distance — a farther row's reading must never stand in for the
    star's (reporting FAR while the star is close skips the get-around)."""
    assert npr.parse_first_row_distance_ls(["S0L garbled", "WOLF 359 7.8 LY"]) is None
    assert npr.parse_first_row_distance_ls(["S0L garbled", "SOL A 1 504 Ls"]) is None


def test_parse_first_row_blank_lines_skipped_empty_is_none():
    assert npr.parse_first_row_distance_ls(["", "  ", "SOL 42 Ls"]) == 42.0
    assert npr.parse_first_row_distance_ls([]) is None


def test_parse_first_row_ly_token_is_none():
    """A Ly token on the top line is not an in-system distance (and row 0 can
    never be a nearby system) -> unreadable, fail closed."""
    assert npr.parse_first_row_distance_ls(["WOLF 359 7.8 LY"]) is None


def test_parse_first_row_ocr_comma_space_split_rejoined():
    """WinRT splits a thousands comma with a space ("79, 420Ls" — live frame
    lawd26_sc_distance_1080.png, 2026-07-05). Must parse the WHOLE number,
    never the trailing group alone (420 Ls for a 79,420 Ls star)."""
    assert npr.parse_first_row_distance_ls(["79, 420Ls"]) == 79420.0


def test_parse_first_row_comma_split_garbled_unit_stays_none():
    """Same live frame, tighter crop: "Ls" misread as "1.5" leaves no unit
    token after the rejoin -> unreadable, fail closed."""
    assert npr.parse_first_row_distance_ls(["79, 4201.5"]) is None


# ---- step_star_distance_gate --------------------------------------------------

def _gate_ctx(sender, monkeypatch, ls, logs=None):
    monkeypatch.setattr(npr, "read_first_row_distance_ls",
                        lambda frame, **kw: ls)
    return StepContext(sender=sender, sleeper=lambda s: None,
                       navpanel_frame_grabber=lambda: object(),
                       record=(lambda n, p: logs.append((n, p))) if logs is not None else None)


def test_gate_no_grabber_fails_closed_to_close(caplog=None):
    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True
    assert any(n == "StarDistanceGate" and p["reason"] == "no_grabber"
               for n, p in logs)


def test_gate_close_star_returns_true(monkeypatch):
    sender = FakeSender()
    ctx = _gate_ctx(sender, monkeypatch, 12.0)
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True


def test_gate_far_star_returns_false_and_panel_closed(monkeypatch):
    """FAR is the ONLY False. The panel is opened then ALWAYS closed — exactly
    two FocusLeftPanel presses, nothing else."""
    sender = FakeSender()
    ctx = _gate_ctx(sender, monkeypatch, 504.0)
    assert STEP_REGISTRY["star_distance_gate"](ctx) is False
    assert sender.actions() == ["FocusLeftPanel", "FocusLeftPanel"]


def test_gate_threshold_boundary(monkeypatch):
    sender = FakeSender()
    assert STEP_REGISTRY["star_distance_gate"](
        _gate_ctx(sender, monkeypatch, 99.9)) is True     # just inside -> close
    assert STEP_REGISTRY["star_distance_gate"](
        _gate_ctx(sender, monkeypatch, 100.0)) is False   # at floor -> far


def test_gate_unreadable_fails_closed_to_close(monkeypatch):
    logs = []
    sender = FakeSender()
    ctx = _gate_ctx(sender, monkeypatch, None, logs)
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True
    assert any(n == "StarDistanceGate" and p["reason"] == "unreadable"
               for n, p in logs)
    # panel still opened + closed around the failed read
    assert sender.actions() == ["FocusLeftPanel", "FocusLeftPanel"]


def test_gate_missing_bind_fails_closed_to_close():
    sender = FakeSender(unbound={"FocusLeftPanel"})
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      navpanel_frame_grabber=lambda: object())
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True


# ---- step_wait_sc_assist_orbiting ----------------------------------------------

def test_orbit_wait_no_grabber_immediate_true():
    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["wait_sc_assist_orbiting"](ctx) is True
    assert logs[-1][1]["result"] == "no_hud_grabber"


def test_orbit_wait_returns_when_orbiting_appears(monkeypatch):
    polls = {"n": 0}

    def fake_detect(frame, **kw):
        polls["n"] += 1
        return polls["n"] >= 3      # prompt appears on the 3rd read

    monkeypatch.setattr(hud, "detect_orbiting", fake_detect)
    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      hud_grabber=lambda: object(),
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["wait_sc_assist_orbiting"](ctx, poll_s=0.0) is True
    assert logs[-1][1]["result"] == "orbiting"


def test_orbit_wait_backstop_is_poll_count_bounded(monkeypatch):
    monkeypatch.setattr(hud, "detect_orbiting", lambda frame, **kw: False)
    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      hud_grabber=lambda: object(),
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["wait_sc_assist_orbiting"](
        ctx, poll_s=0.0, max_polls=4) is True
    assert logs[-1][1] == {"result": "backstop", "polls": 4}


def test_orbit_wait_abort_exits_true(monkeypatch):
    monkeypatch.setattr(hud, "detect_orbiting", lambda frame, **kw: False)
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      hud_grabber=lambda: object(),
                      should_abort=lambda: True)
    assert STEP_REGISTRY["wait_sc_assist_orbiting"](ctx, poll_s=0.0) is True


# ---- rewired toml shapes -------------------------------------------------------

def _shape(name):
    proc = load_procedures(PROC_DIR)[name]
    return proc, [s.action for s in proc.steps]


def test_startup_rewired_shape():
    """Operator LIVE reorder 2026-07-06 (session 000806): ED refuses SC entry
    at zero throttle (THROTTLE UP hang), so set_throttle(100) now leads the
    scene, before the gate; the old shared-lane burn step is gone (the jump
    leg re-asserts throttle itself)."""
    proc, actions = _shape("startup")
    assert actions == [
        "set_throttle", "star_distance_gate", "engage_supercruise",
        "nav_supercruise_star", "wait_sc_assist_orbiting", "target_next_route",
        "orient_compass", "orient_widget_ring", "engage_jump_clearance",
    ]
    # The blind flow is DEAD (#18/#27/#28): no nav_panel_target, no
    # sc_assist_orbit, no bare wait, no engage_jump/hold_alignment tail.
    for gone in ("nav_panel_target", "sc_assist_orbit", "wait",
                 "engage_jump", "hold_alignment", "target_ahead"):
        assert gone not in actions
    gate = proc.steps[1]
    assert gate.skip_to == "target_next_route"      # FAR lane vault
    assert proc.parallel_tracks == ("honk",)
    assert proc.on_required_fail.retry_from == "target_next_route"


def test_sc_resume_rewired_shape():
    proc, actions = _shape("sc_resume")
    assert actions == [
        "star_distance_gate", "nav_supercruise_star", "wait_sc_assist_orbiting",
        "target_next_route", "set_throttle", "orient_compass",
        "orient_widget_ring", "engage_jump_clearance",
    ]
    for gone in ("nav_panel_target", "sc_assist_orbit", "wait",
                 "engage_jump", "hold_alignment", "engage_supercruise"):
        assert gone not in actions
    assert proc.steps[0].skip_to == "target_next_route"
    assert proc.parallel_tracks == ("honk",)


def test_gate_throttle_order_per_scene():
    """The 2026-06-08 star-ram law, LIVE-REVISED 2026-07-06: ED refuses SC
    entry at zero throttle (run 000806 hung at the THROTTLE UP prompt), so in
    STARTUP the operator moved set_throttle(100) ahead of the gate — the gate
    still precedes SC entry, and there is exactly ONE throttle step (the old
    shared-lane burn is gone). sc_resume starts ALREADY in supercruise (no SC
    entry), so the original law holds there: no throttle before the gate."""
    _, actions = _shape("startup")
    assert actions[0] == "set_throttle"            # SC entry needs live throttle
    assert actions[1] == "star_distance_gate"      # gate still precedes SC entry
    assert actions.index("star_distance_gate") < actions.index("engage_supercruise")
    assert actions.count("set_throttle") == 1
    _, actions = _shape("sc_resume")
    assert actions[0] == "star_distance_gate"
    assert actions.index("star_distance_gate") < actions.index("set_throttle")
