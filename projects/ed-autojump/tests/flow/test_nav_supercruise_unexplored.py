"""Tests for step_nav_supercruise_unexplored (#6): confirm row 0 -> read the
list -> walk to the first UNEXPLORED row -> SC-assist it via the verified
button-bar walk.

G3/G4 (operator 2026-07-11): row 0 is VISUALLY confirmed before any walk
(_confirm_row0_selected — the blind pin is gone), both CV reads dump frames,
and the detail press follows the BUTTON-BAR WALK law (press ONLY a positively
read SC ASSIST; nothing verified -> refuse, zero presses).

The perception (find_first_unexplored) is tested against real frames in
tests/vision/test_navpanel_column0.py; here it is monkeypatched so these tests
pin the FLIGHT contract.
"""

import ed_vision.navpanel_column0 as c0
import ed_vision.navpanel_detail as navdetail
import ed_vision.navpanel_row0 as nr0
import pytest
from ed_core.flow.context import StepContext
from ed_vision.navpanel_detail import DetailButton, DetailLabelRead

from ed_autojump.flow.steps import step_nav_supercruise_unexplored

from . import FakeSender

OPEN, SEL, RIGHT = "FocusLeftPanel", "UI_Select", "UI_Right"
DOWN, UP = "UI_Down", "UI_Up"

# row 0 visually confirmed (zero pin presses) -> walk to row 2 = 2x UI_Down
WALK_ROW2 = [DOWN, DOWN]


@pytest.fixture(autouse=True)
def _row0_bright(monkeypatch):
    """G3: the step confirms row 0 visually before walking. Default it to
    confirmed-bright (zero presses); the unconfirmed branch has its own test."""
    monkeypatch.setattr(
        nr0, "read_row0_selected",
        lambda frame: nr0.Row0Read("bright", 401, 0.75, True,
                                   (490, 463, 410, 23), 474))


def _read(button, text=""):
    return DetailLabelRead(button, text, button is not DetailButton.UNKNOWN)


def _found(row=2):
    return {"row": row, "terminated": False, "scan": [], "anchors": []}


def _ctx(sender, *, navpanel=False, **kw):
    """A StepContext; navpanel=True wires the full-frame nav grabber (runtime attr,
    not a declared field — same pattern as navpanel_detail_grabber)."""
    ctx = StepContext(sender=sender, sleeper=lambda _x: None, **kw)
    if navpanel:
        ctx.navpanel_frame_grabber = lambda: "frame"
    return ctx


def test_no_navpanel_grabber_returns_false():
    """No way to read the list -> False, no keypress (never blind-walk)."""
    s = FakeSender()
    ctx = _ctx(s)   # navpanel_frame_grabber unset
    assert step_nav_supercruise_unexplored(ctx) is False
    assert s.actions() == []
    assert ctx.explore_terminated is False


def test_engages_on_found_row(monkeypatch):
    """Row found + label reads SC ASSIST at the first slot -> walk + engage."""
    monkeypatch.setattr(c0, "find_first_unexplored", lambda frame: _found(2))
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.SC_ASSIST,
                                            "SUPERCRUISE ASSIST"))
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_unexplored(ctx) is True
    assert s.actions() == [OPEN, *WALK_ROW2, SEL, RIGHT, SEL, OPEN]


def test_blind_when_no_detail_grabber(monkeypatch):
    """Row found, no detail grabber -> blind engage (legacy), still True."""
    monkeypatch.setattr(c0, "find_first_unexplored", lambda frame: _found(2))
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    assert step_nav_supercruise_unexplored(ctx) is True
    assert s.actions() == [OPEN, *WALK_ROW2, SEL, RIGHT, SEL, OPEN]


def test_row0_unconfirmed_fails_closed_without_walk(monkeypatch):
    """G3: row 0 not visually confirmable (even after the one-shot recovery)
    -> close + False, ZERO walk presses — a blind walk from an unknown cursor
    is the run-102104/104612 smack class."""
    monkeypatch.setattr(
        nr0, "read_row0_selected",
        lambda frame: nr0.Row0Read("dark", 401, 0.08, None,
                                   (490, 463, 410, 23), 474))
    monkeypatch.setattr(c0, "find_first_unexplored", lambda frame: _found(2))
    s = FakeSender()
    logs = []
    ctx = _ctx(s, navpanel=True, record=lambda k, p: logs.append((k, p)))
    assert step_nav_supercruise_unexplored(ctx) is False
    assert ctx.explore_terminated is False
    # open -> confirm's one-shot recovery (DOWN + held UP) -> close. No walk.
    assert s.actions() == [OPEN, DOWN, UP, OPEN]
    assert any(k == "NavSupercruiseUnexploredUnreadable"
               and p.get("reason") == "row0_unconfirmed" for k, p in logs)


def test_frames_dumped_for_both_cv_reads(monkeypatch):
    """G4 (frame capture DEFAULT ON): the list read and every label read dump
    frames via ctx.frame_sink."""
    monkeypatch.setattr(c0, "find_first_unexplored", lambda frame: _found(2))
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.SC_ASSIST))
    dumps = []
    s = FakeSender()
    ctx = _ctx(s, navpanel=True, frame_sink=lambda name, f: dumps.append(name))
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_unexplored(ctx) is True
    assert any(n.startswith("navunexp_list_") for n in dumps)
    assert any(n.startswith("navunexp_label_") for n in dumps)


def test_terminated_sets_flag(monkeypatch):
    """SYSTEM glyph reached (no more unexplored) -> False + explore_terminated True;
    open then close, no walk."""
    monkeypatch.setattr(c0, "find_first_unexplored",
                        lambda frame: {"row": None, "terminated": True,
                                       "scan": [], "anchors": []})
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    assert step_nav_supercruise_unexplored(ctx) is False
    assert ctx.explore_terminated is True
    assert s.actions() == [OPEN, OPEN]


def test_unreadable_does_not_set_terminated(monkeypatch):
    """List unreadable (no row, not terminated) -> False, explore_terminated False."""
    monkeypatch.setattr(c0, "find_first_unexplored",
                        lambda frame: {"row": None, "terminated": False,
                                       "scan": [], "anchors": []})
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    assert step_nav_supercruise_unexplored(ctx) is False
    assert ctx.explore_terminated is False
    assert s.actions() == [OPEN, OPEN]


def test_cv_error_fails_closed(monkeypatch):
    """find_first_unexplored raising -> close the panel + False (no walk)."""
    def boom(frame):
        raise RuntimeError("ocr blew up")
    monkeypatch.setattr(c0, "find_first_unexplored", boom)
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    assert step_nav_supercruise_unexplored(ctx) is False
    assert s.actions() == [OPEN, OPEN]


def test_wrong_label_walks_bar_then_refuses_without_press(monkeypatch):
    """BUTTON-BAR WALK law: a bar that never reads SC ASSIST (here LOCK at
    every position) is walked then refused — no engage press ever."""
    monkeypatch.setattr(c0, "find_first_unexplored", lambda frame: _found(2))
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.LOCK, "LOCK DESTINATION"))
    s = FakeSender()
    logs = []
    ctx = _ctx(s, navpanel=True, record=lambda k, p: logs.append((k, p)))
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_unexplored(ctx) is False
    # walk to row, open detail, initial RIGHT + 4 walk-RIGHTs, close. No engage.
    assert s.actions() == [OPEN, *WALK_ROW2, SEL, RIGHT] + [RIGHT] * 4 + [OPEN]
    refusals = [p for k, p in logs if k == "NavSupercruiseUnexploredRefused"]
    assert refusals and refusals[-1]["reason"] == "sc_assist_button_not_found"


def test_deactivate_label_is_already_on_success(monkeypatch):
    """SC DEACTIVATE = assist already engaged toward the body = goal state."""
    monkeypatch.setattr(c0, "find_first_unexplored", lambda frame: _found(2))
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.SC_DEACTIVATE,
                                            "DEACTIVATE SUPERCRUISE ASSIST"))
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_unexplored(ctx) is True
    assert s.actions() == [OPEN, *WALK_ROW2, SEL, RIGHT, OPEN]   # no engage press


def test_emergency_drop_returns_false(monkeypatch):
    """Out of supercruise after the macro (mid-press smack drop) -> False."""
    monkeypatch.setattr(c0, "find_first_unexplored", lambda frame: _found(2))
    class _St:
        in_supercruise = False
    s = FakeSender()
    ctx = _ctx(s, navpanel=True, status_supplier=lambda: _St())
    assert step_nav_supercruise_unexplored(ctx) is False


def test_bind_missing_returns_false():
    """Unbound FocusLeftPanel -> KeyError caught -> False."""
    s = FakeSender(unbound={"FocusLeftPanel"})
    ctx = _ctx(s, navpanel=True)
    assert step_nav_supercruise_unexplored(ctx) is False


def test_registered_input_exclusive():
    from ed_core.flow import input_exclusive_actions, merged_step_registry
    assert "nav_supercruise_unexplored" in merged_step_registry()
    assert "nav_supercruise_unexplored" in input_exclusive_actions()
