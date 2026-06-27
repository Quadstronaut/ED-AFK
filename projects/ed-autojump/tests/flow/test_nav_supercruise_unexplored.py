"""Tests for step_nav_supercruise_unexplored (#6): read the list -> walk to the
first UNEXPLORED row -> SC-assist it (with the #8 confirm).

The perception (find_first_unexplored) is tested against real frames in
tests/vision/test_navpanel_column0.py; here it is monkeypatched so these tests
pin the FLIGHT contract: the keystroke sequence, the terminator/unreadable
branches (False + ctx.explore_terminated), and the fail-closed CV refusal.
"""

import ed_vision.navpanel_column0 as c0
import ed_vision.navpanel_detail as navdetail
from ed_core.flow.context import StepContext

from ed_autojump.flow.steps import step_nav_supercruise_unexplored

from . import FakeSender

OPEN, SEL, RIGHT = "FocusLeftPanel", "UI_Select", "UI_Right"
DOWN, UP = "UI_Down", "UI_Up"

# pin-to-top + walk to row 2 = UI_Down, UI_Up(held), UI_Down, UI_Down
WALK_ROW2 = [DOWN, UP, DOWN, DOWN]


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
    """Row found + detail label confirms SC-assist -> walk there + engage."""
    monkeypatch.setattr(c0, "find_first_unexplored", lambda frame: _found(2))
    monkeypatch.setattr(navdetail, "confirm_button", lambda frame, expected: True)
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_unexplored(ctx) is True
    assert s.actions() == [OPEN, *WALK_ROW2, SEL, RIGHT, SEL, OPEN]


def test_blind_when_no_detail_grabber(monkeypatch):
    """Row found, no detail grabber -> blind engage (today's behaviour), still True."""
    monkeypatch.setattr(c0, "find_first_unexplored", lambda frame: _found(2))
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    assert step_nav_supercruise_unexplored(ctx) is True
    assert s.actions() == [OPEN, *WALK_ROW2, SEL, RIGHT, SEL, OPEN]


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


def test_refuses_on_wrong_label(monkeypatch):
    """Walked onto the row but the label is NOT the SC-assist button -> do NOT
    press engage; close + False."""
    monkeypatch.setattr(c0, "find_first_unexplored", lambda frame: _found(2))
    monkeypatch.setattr(navdetail, "confirm_button", lambda frame, expected: False)
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_unexplored(ctx) is False
    # no second UI_Select (no engage); panel closed
    assert s.actions() == [OPEN, *WALK_ROW2, SEL, RIGHT, OPEN]


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
