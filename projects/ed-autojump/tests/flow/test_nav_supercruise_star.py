"""Tests for step_nav_supercruise_star: ASSIST-BY-DEFAULT row-0 + #8 label-confirm.

D1/B2 REVISION (2026-07-07, never-strand council — supersedes the 2026-07-06
run-010444 STAR-required gate): row 0 IS the arrival star by GAME TRUTH. The
step now ASSISTS row 0 unless CV POSITIVELY identifies a confident
station/beacon/POI glyph (selected_row_kind_confirmed's registry dock-kind
match) — STAR / a bare NON_STAR / NONE / unreadable / a CV exception all
ASSIST.

LABEL layer (OPERATOR LAW, live 2026-07-11 22:34 — supersedes D1/B2's
blind-assist-on-unreadable-label): the bar layout is not fixed; a blind press
on an unverified slot opened the SYSTEM MAP twice live. The step WALKS the
button bar (UI_Right between bounded label reads) and presses ONLY a label
positively read as SC ASSIST; nothing verified across the bar -> close +
refuse with zero presses (never-strand re-dispatch owns the retry). The
grabber-UNWIRED path keeps the legacy blind macro (no CV to verify with).
"""

from types import SimpleNamespace

import pytest

import ed_vision.navpanel_detail as navdetail
import ed_vision.navpanel_icons as navicons
import ed_vision.navpanel_row0 as nr0
from ed_core.flow.context import StepContext
from ed_vision.navpanel_detail import DetailButton, DetailLabelRead

from ed_autojump.flow.steps import step_nav_supercruise_star

from . import FakeSender

OPEN, SEL, RIGHT = "FocusLeftPanel", "UI_Select", "UI_Right"


@pytest.fixture(autouse=True)
def _row0_bright(monkeypatch):
    """Row-0 brightness confirm STACKS in front of the icon confirm (council-v2).
    These tests target the icon/label layer, so default row 0 to confirmed-bright;
    the row0-unconfirmed ASSIST path is covered in test_confirm_row0.py. Tests
    with no navpanel_frame_grabber never invoke this, so the patch is inert."""
    monkeypatch.setattr(
        nr0, "read_row0_selected",
        lambda frame: nr0.Row0Read("bright", 401, 0.75, True,
                                   (490, 463, 410, 23), 474))


def _read(button, text=""):
    return DetailLabelRead(button, text, button is not DetailButton.UNKNOWN)


def test_blind_when_no_grabber():
    """No detail grabber wired -> pure blind macro (zero regression vs today):
    open -> detail -> right -> ENGAGE -> close, no CV gate."""
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]


def test_confirms_then_presses(monkeypatch):
    """Grabber wired + label IS the SC-assist button -> engage press fires."""
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.SC_ASSIST,
                                            "SUPERCRUISE ASSIST AND ORBIT"))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]


def test_wrong_label_walks_the_bar_then_refuses_without_press(monkeypatch):
    """OPERATOR LAW (live 2026-07-11 22:34): never press a button the CV
    didn't POSITIVELY read as SC ASSIST. A bar that never shows it (here:
    LOCK DESTINATION at every position) is WALKED (UI_Right between reads,
    bounded) and then refused with NO engage press; the refusal carries the
    raw OCR text."""
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.LOCK, "LOCK DESTINATION"))
    s = FakeSender()
    logs = []
    ctx = StepContext(sender=s, sleeper=lambda _x: None,
                      record=lambda k, p: logs.append((k, p)))
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is False
    # open -> detail -> right, then 4 more walk-rights (5 positions), CLOSE.
    # ZERO further UI_Select — nothing verified, nothing pressed.
    assert s.actions() == [OPEN, SEL, RIGHT] + [RIGHT] * 4 + [OPEN]
    refusals = [p for k, p in logs if k == "NavSupercruiseStarRefused"]
    assert refusals and refusals[0]["last_label"] == "LOCK DESTINATION"
    assert refusals[0]["reason"] == "sc_assist_button_not_found"


def test_system_map_slot_is_walked_past_never_pressed(monkeypatch):
    """THE LIVE BUG (2026-07-11 22:34): cursor landed on SYSTEM MAP, the
    label read UNKNOWN, and the old blind press OPENED THE MAP twice. Now the
    walk steps past the unverified slot and presses only the position that
    reads SC ASSIST."""
    reads = [_read(DetailButton.UNKNOWN, "SYSTEM MAp"),    # pos 1, read 1
             _read(DetailButton.UNKNOWN, "SYSTEM MAp"),    # pos 1, read 2
             _read(DetailButton.SC_ASSIST, "SUPERCRUISE ASSIST")]  # pos 2
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: reads.pop(0) if reads else _read(DetailButton.SC_ASSIST))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    # one extra RIGHT (the walk), engage fires ONLY at the verified slot
    assert s.actions() == [OPEN, SEL, RIGHT, RIGHT, SEL, OPEN]


def test_transient_bad_read_recovers_in_step(monkeypatch):
    """Live findings 3/5 fix: a transient UNKNOWN read must NOT fail the step —
    the label is re-read in place (label_reads per position) and the engage
    still fires at the same slot. No procedure retry, no repeated panel opens."""
    reads = [_read(DetailButton.UNKNOWN),
             _read(DetailButton.SC_ASSIST, "SUPERCRUISE ASSIST")]
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: reads.pop(0) if reads else _read(DetailButton.SC_ASSIST))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]   # ONE panel pass, no walk


def test_already_on_is_success_without_press(monkeypatch):
    """DEACTIVATE label = the assist is ALREADY engaged = the goal state.
    Pressing would turn it OFF — close and succeed with no engage press."""
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.SC_DEACTIVATE,
                                            "DEACTIVATE SUPERCRUISE ASSIST"))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, OPEN]        # no second UI_Select


def test_label_persistently_unknown_refuses_without_press(monkeypatch):
    """OPERATOR LAW (2026-07-11, supersedes D1/B2 blind-assist-on-unreadable):
    an UNKNOWN label at EVERY bar position = nothing verified = NOTHING
    pressed. Walk the bar, close, refuse — never-strand owns the retry. (The
    blind press this replaces opened the SYSTEM MAP live, twice.)"""
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.UNKNOWN, "SYSTEM MAp"))
    s = FakeSender()
    logs = []
    ctx = StepContext(sender=s, sleeper=lambda _x: None,
                      record=lambda k, p: logs.append((k, p)))
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is False
    assert s.actions() == [OPEN, SEL, RIGHT] + [RIGHT] * 4 + [OPEN]
    refusals = [p for k, p in logs if k == "NavSupercruiseStarRefused"]
    assert refusals and refusals[0]["reason"] == "sc_assist_button_not_found"


def test_label_cv_error_refuses_without_press(monkeypatch):
    """A grabber/CV exception at every position verifies nothing -> walk,
    close, refuse, zero presses (operator law 2026-07-11; the unwired-grabber
    path below keeps the legacy blind macro)."""
    def boom(frame):
        raise RuntimeError("ocr blew up")
    monkeypatch.setattr(navdetail, "read_detail_button_label", boom)
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is False
    assert s.actions() == [OPEN, SEL, RIGHT] + [RIGHT] * 4 + [OPEN]


# ---- row-0 positive-POI veto (D1/B2, 2026-07-07) -----------------------------

def test_row0_positive_poi_refuses_before_opening_detail(monkeypatch):
    """THE ONLY row-0 refuse case: a CONFIDENT registry dock-kind match (a
    nav beacon / signal row dressed as a station icon) -> refuse BEFORE
    UI_Select — the row's detail page is never opened, nothing is pressed at
    it. All in-place re-reads happen within the ONE panel open (operator: "we
    do shit once") and the refusal payload carries the kind + read count."""
    calls = []
    monkeypatch.setattr(
        navicons, "selected_row_kind_confirmed",
        lambda frame: calls.append(1) or
        {"action": "dock", "kind": "station-outpost", "score": 0.83})
    s = FakeSender()
    logs = []
    ctx = StepContext(sender=s, sleeper=lambda _x: None,
                      record=lambda k, p: logs.append((k, p)))
    ctx.navpanel_frame_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is False
    assert s.actions() == [OPEN, OPEN]                    # open -> refuse -> close
    assert len(calls) == 3                                # bounded in-place re-reads
    refusals = [p for k, p in logs if k == "NavSupercruiseStarRefused"]
    assert refusals and refusals[0]["reason"] == "row0_positive_poi"
    assert refusals[0]["kind"] == "station-outpost"
    assert refusals[0]["reads"] == 3


def test_row0_non_star_without_poi_assists(monkeypatch):
    """D1/B2: a bare NON_STAR read (no confident registry kind -- e.g. the
    UNEXPLORED reticle) is NOT a positive POI -> the macro proceeds (ASSIST),
    it does not refuse."""
    monkeypatch.setattr(
        navicons, "selected_row_kind_confirmed",
        lambda frame: {"action": "park", "kind": "", "score": 0.19})
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_frame_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]


def test_transient_positive_poi_read_recovers_to_assist_in_step(monkeypatch):
    """A transient POSITIVE-POI misread (bad frame / mid-fade) that resolves
    to non-POI on a re-read must NOT refuse — the row is re-read in place
    within the SAME panel open and the macro proceeds. No procedure retry, no
    repeated panel opens."""
    verdicts = [{"action": "dock", "kind": "station-outpost", "score": 0.55},
                {"action": "park", "kind": "", "score": 0.0}]
    monkeypatch.setattr(
        navicons, "selected_row_kind_confirmed",
        lambda frame: verdicts.pop(0) if verdicts else {"action": "park", "kind": "", "score": 0.0})
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_frame_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]   # ONE panel pass


def test_row0_star_proceeds_to_label_confirm(monkeypatch):
    """A confident STAR read (not a POI) -> the macro proceeds (blind label
    path); the step no longer requires this confirmation to proceed."""
    monkeypatch.setattr(
        navicons, "selected_row_kind_confirmed",
        lambda frame: {"action": "park", "kind": "star", "score": 0.79})
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_frame_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]


def test_row_cv_error_assists(monkeypatch):
    """D1/B2 (was test_row_cv_error_fails_closed under the old REFUSE-on-miss
    contract): a row-icon CV exception is unreadable, not a positive
    contra-ID -- assist, never refuse."""
    def boom(frame):
        raise RuntimeError("icon cv blew up")
    monkeypatch.setattr(navicons, "selected_row_kind_confirmed", boom)
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_frame_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]


def test_emergency_drop_returns_false():
    """Out of supercruise after the macro (mid-press smack drop) -> False."""
    class _St:
        in_supercruise = False
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None, status_supplier=lambda: _St())
    assert step_nav_supercruise_star(ctx) is False


def test_bind_missing_returns_false():
    s = FakeSender(unbound={"FocusLeftPanel"})
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    assert step_nav_supercruise_star(ctx) is False


def test_registered_input_exclusive():
    """The new action is registered and owns input (heat watchdog pauses for it)."""
    from ed_core.flow import input_exclusive_actions, merged_step_registry
    assert "nav_supercruise_star" in merged_step_registry()
    assert "nav_supercruise_star" in input_exclusive_actions()
