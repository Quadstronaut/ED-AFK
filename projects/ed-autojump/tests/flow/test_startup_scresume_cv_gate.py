"""Startup/sc_resume CV rewire (operator 2026-07-05): the clear-of-star CV
distance gate (star_distance_gate), the ORBITING-prompt orbit-acquire wait
(wait_sc_assist_orbiting), the row-0 distance parser, and the rewritten
startup.toml / sc_resume.toml shapes. Pure-Python — no game, no CV engine.
"""

from pathlib import Path

import ed_vision.navpanel_reader as npr
import ed_vision.navpanel_row0 as nr0
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

def _bright_row0(frame):
    """A confirmed-bright ROW-0 read (row_y carries the distance-crop anchor)."""
    return nr0.Row0Read(state="bright", header_y=401, orange_frac=0.75,
                        thumb_at_top=True, row0_rect=(490, 463, 410, 23), row_y=474)


def _gate_ctx(sender, monkeypatch, ls, logs=None):
    # Row 0 confirmed bright -> the distance path runs (the gate now gates the
    # read on a POSITIONAL row-0 confirm; unconfirmed forces CLOSE regardless).
    monkeypatch.setattr(nr0, "read_row0_selected", _bright_row0)
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


def test_gate_distance_read_anchored_on_confirmed_row0_y(monkeypatch):
    """The distance OCR crop is anchored on the CONFIRMED row-0 y (row_y from the
    row0 read) — NOT the fixed top-of-panel crop that can capture the LOCATION
    summary distance one row above row 0."""
    monkeypatch.setattr(nr0, "read_row0_selected", _bright_row0)
    seen = {}

    def spy(frame, **kw):
        seen.update(kw)
        return 12.0

    monkeypatch.setattr(npr, "read_first_row_distance_ls", spy)
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      navpanel_frame_grabber=lambda: object())
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True   # CLOSE
    assert seen.get("row_y") == 474                           # == _bright_row0 row0_rect y+11


def test_gate_row0_unconfirmed_forces_close(monkeypatch):
    """Row-0 UNCONFIRMED -> CLOSE (True), reason row0_unconfirmed, and a would-be
    FAR (504 Ls) read never produces False. Panel opened+closed exactly once."""
    monkeypatch.setattr(nr0, "read_row0_selected",
                        lambda frame: nr0.Row0Read("dark", 401, 0.08, None,
                                                   (490, 463, 410, 23), 474))
    monkeypatch.setattr(npr, "read_first_row_distance_ls", lambda frame, **kw: 504.0)
    sender = FakeSender()
    logs = []
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      navpanel_frame_grabber=lambda: object(),
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True   # CLOSE, not False
    assert any(n == "StarDistanceGate" and p.get("reason") == "row0_unconfirmed"
               for n, p in logs)
    assert sender.actions() == ["FocusLeftPanel", "UI_Down", "UI_Up", "FocusLeftPanel"]


def test_gate_anchor_comes_from_gates_own_grab_not_the_confirm(monkeypatch):
    """LIVE G8 FIX (session 090913, 2026-07-11): the confirm's row_y was applied
    to the gate's LATER grab while the panel floated 30 px between them — the
    crop landed a row down and read 474 Ls for a 1.60 Ls star (false FAR ->
    burn lane). The gate must re-read row 0 on its OWN frame and anchor there."""
    reads = {"n": 0}

    def floating_row0(frame):
        # confirm's read(s) see row_y=474; the gate's own grab sees 451 (float).
        reads["n"] += 1
        return nr0.Row0Read("bright", 401 if reads["n"] == 1 else 378, 0.75,
                            True, (490, 463, 410, 23),
                            474 if reads["n"] == 1 else 451)

    monkeypatch.setattr(nr0, "read_row0_selected", floating_row0)
    seen = {}

    def spy(frame, **kw):
        seen.update(kw)
        return 12.0

    monkeypatch.setattr(npr, "read_first_row_distance_ls", spy)
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      navpanel_frame_grabber=lambda: object())
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True
    assert seen.get("row_y") == 451          # the gate frame's OWN anchor


def test_gate_far_requires_two_agreeing_reads(monkeypatch):
    """FAR CONFIRMATION (live 2026-07-11 23:43, session 234324): OCR ate the
    leading "5." of "5.82Ls" -> a single 82.0 read verdicted FAR and
    full-throttled into a star 5.82 Ls off the nose. A FAR first read whose
    confirmation read disagrees (82.0 vs 5.82) must verdict CLOSE."""
    monkeypatch.setattr(nr0, "read_row0_selected", _bright_row0)
    vals = [82.0, 5.82]
    monkeypatch.setattr(npr, "read_first_row_distance_ls",
                        lambda frame, **kw: vals.pop(0))
    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      navpanel_frame_grabber=lambda: object(),
                      record=lambda n, p: logs.append((n, p)))
    # threshold 10 = the live toml value; 82.0 reads FAR, 5.82 refutes it.
    assert STEP_REGISTRY["star_distance_gate"](ctx, threshold_ls=10.0) is True
    assert any(n == "StarDistanceGate" and p.get("reason") == "far_unconfirmed"
               for n, p in logs)


def test_gate_far_confirmed_by_agreeing_second_read(monkeypatch):
    """Two independent FAR reads that agree -> FAR stands (False)."""
    monkeypatch.setattr(nr0, "read_row0_selected", _bright_row0)
    vals = [504.0, 496.0]
    monkeypatch.setattr(npr, "read_first_row_distance_ls",
                        lambda frame, **kw: vals.pop(0))
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      navpanel_frame_grabber=lambda: object())
    assert STEP_REGISTRY["star_distance_gate"](ctx) is False


def test_gate_far_with_unreadable_confirmation_forces_close(monkeypatch):
    """FAR first read + unreadable second read -> CLOSE (fail-closed: the
    dangerous verdict never stands on one read)."""
    monkeypatch.setattr(nr0, "read_row0_selected", _bright_row0)
    vals = [504.0, None]
    monkeypatch.setattr(npr, "read_first_row_distance_ls",
                        lambda frame, **kw: vals.pop(0))
    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      navpanel_frame_grabber=lambda: object(),
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True
    assert any(p.get("reason") == "far_unconfirmed" for _, p in logs)


def test_gate_close_verdict_needs_only_one_read(monkeypatch):
    """CLOSE is the safe verdict — no confirmation read is spent on it."""
    monkeypatch.setattr(nr0, "read_row0_selected", _bright_row0)
    calls = {"n": 0}

    def counting(frame, **kw):
        calls["n"] += 1
        return 3.0
    monkeypatch.setattr(npr, "read_first_row_distance_ls", counting)
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      navpanel_frame_grabber=lambda: object())
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True
    assert calls["n"] == 1


def test_gate_row0_lost_on_own_grab_forces_close(monkeypatch):
    """If the gate's own frame can no longer confirm a bright row 0 (float/wash
    between the confirm and the grab), NO anchor is trustworthy: CLOSE lane,
    never a FAR off a guessed crop."""
    reads = {"n": 0}

    def bright_then_dark(frame):
        reads["n"] += 1
        return nr0.Row0Read("bright" if reads["n"] == 1 else "dark",
                            401, 0.75 if reads["n"] == 1 else 0.08,
                            True, (490, 463, 410, 23), 474)

    monkeypatch.setattr(nr0, "read_row0_selected", bright_then_dark)
    monkeypatch.setattr(npr, "read_first_row_distance_ls",
                        lambda frame, **kw: 504.0)   # would-be FAR must not stand
    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      navpanel_frame_grabber=lambda: object(),
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True   # CLOSE
    assert any(n == "StarDistanceGate" and p.get("reason") == "row0_moved"
               for n, p in logs)


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
    """OPERATOR LAYOUT 2026-07-07 (his own toml reorg, 70c248e): throttle 0 ->
    gate (10 Ls) -> throttle 100 -> SC entry (escape-vector watch) -> star
    assist -> orbit wait -> pacing wait -> hop lock -> 75% orient -> 100%
    jump."""
    proc, actions = _shape("startup")
    assert actions == [
        "set_throttle", "star_distance_gate", "set_throttle",
        "engage_supercruise", "nav_supercruise_star", "wait_sc_assist_orbiting",
        "wait", "target_next_route", "set_throttle", "wait",
        "orient_compass", "orient_widget_ring", "set_throttle",
        "engage_jump_clearance",
    ]
    # The blind flow stays DEAD (#18/#27/#28). Bare pacing waits are the
    # operator's own 2026-07-07 additions ("some of these things gate too
    # quickly") and are allowed.
    for gone in ("nav_panel_target", "sc_assist_orbit",
                 "engage_jump", "hold_alignment", "target_ahead"):
        assert gone not in actions
    gate = proc.steps[1]
    assert gate.skip_to == "target_next_route"      # FAR lane vault
    assert gate.params["threshold_ls"] == 15.0      # operator retuned (100 -> 10 -> 15)
    # Boot-smack override watch rides SC entry (operator wire-in, run 233422).
    assert proc.steps[3].params.get("escape_vector_abort") is True
    assert proc.parallel_tracks == ("honk",)
    # LIVE FIX 2026-07-06 (run 010444 starsmack): retries re-run the
    # clear-of-star GATE — no retry may bypass it into the burn.
    assert proc.on_required_fail.retry_from == "star_distance_gate"


def test_sc_resume_rewired_shape():
    """OPERATOR LAYOUT 2026-07-07 + 2026-07-11 pacing wait: gate first
    (already in SC — no SC entry), star assist, orbit wait, 7s settle
    (operator: near-star resume needs the pace), hop lock, 75% orient,
    100% jump."""
    proc, actions = _shape("sc_resume")
    # OPERATOR 2026-07-12: a leading set_throttle 0 now reads the gate at ZERO
    # throttle (same no-drift-toward-a-maybe-near-star rationale as startup).
    assert actions == [
        "set_throttle", "star_distance_gate", "nav_supercruise_star",
        "wait_sc_assist_orbiting", "wait", "target_next_route", "set_throttle",
        "orient_compass", "orient_widget_ring", "set_throttle",
        "engage_jump_clearance",
    ]
    for gone in ("nav_panel_target", "sc_assist_orbit",
                 "engage_jump", "hold_alignment", "engage_supercruise"):
        assert gone not in actions
    assert proc.steps[1].skip_to == "target_next_route"     # gate now at index 1
    assert proc.parallel_tracks == ("honk",)
    # LIVE FIX 2026-07-06 (run 010444 starsmack): same gate re-anchor as
    # startup — no retry may bypass the clear-of-star gate into the burn.
    assert proc.on_required_fail.retry_from == "star_distance_gate"


def test_gate_throttle_order_per_scene():
    """The star-ram law under the operator's 2026-07-07 layout: STARTUP reads
    the gate at ZERO throttle (no drift toward a maybe-near star during the
    read); throttle hits 100 only AFTER the gate and BEFORE SC entry (ED
    refuses entry at zero throttle, run 000806). sc_resume starts already in
    SC: gate first, no throttle before it."""
    proc, actions = _shape("startup")
    assert actions[0] == "set_throttle" and proc.steps[0].params["pct"] == 0
    assert actions[1] == "star_distance_gate"
    hundred_i = next(i for i, s in enumerate(proc.steps)
                     if s.action == "set_throttle" and s.params["pct"] == 100)
    assert 1 < hundred_i < actions.index("engage_supercruise")
    # OPERATOR 2026-07-12: sc_resume now ALSO reads the gate at zero throttle --
    # set_throttle 0 first, THEN the gate (no drift toward a maybe-near star
    # during the read), matching startup. Supersedes the old "gate first, no
    # throttle before it" invariant.
    proc, actions = _shape("sc_resume")
    assert actions[0] == "set_throttle" and proc.steps[0].params["pct"] == 0
    assert actions[1] == "star_distance_gate"
