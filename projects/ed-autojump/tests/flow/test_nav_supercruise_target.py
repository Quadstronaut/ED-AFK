"""Tests for step_nav_supercruise_target (#5): SC-assist the destination STATION by
NAME-matching its nav-list row.

_resolve_named_row (WinRT OCR + match_row_by_name) is monkeypatched here so the
tests pin the FLIGHT contract: the keystroke sequence, the no-name / no-grabber /
no-match fail-closed branches, and the #8 CV refusal. The name-match itself reuses
the route-complete path proven elsewhere.
"""

import ed_autojump.flow.steps as steps
import ed_vision.navpanel_detail as navdetail
from ed_core.flow.context import StepContext

from ed_autojump.flow.steps import step_nav_supercruise_target

from . import FakeSender

OPEN, SEL, RIGHT = "FocusLeftPanel", "UI_Select", "UI_Right"
DOWN, UP = "UI_Down", "UI_Up"

# pin-to-top + walk to row 3 = UI_Down, UI_Up(held), UI_Down x3
WALK_ROW3 = [DOWN, UP, DOWN, DOWN, DOWN]
STATION = "Jameson Memorial"


def _ctx(sender, *, navpanel=False, name=STATION, **kw):
    ctx = StepContext(sender=sender, sleeper=lambda _x: None,
                      dock_target_name_supplier=lambda: name, **kw)
    if navpanel:
        ctx.navpanel_frame_grabber = lambda: "frame"
    return ctx


def test_no_navpanel_grabber_returns_false():
    s = FakeSender()
    ctx = _ctx(s)   # no navpanel_frame_grabber
    assert step_nav_supercruise_target(ctx) is False
    assert s.actions() == []


def test_no_dest_name_returns_false():
    s = FakeSender()
    ctx = _ctx(s, navpanel=True, name=None)
    assert step_nav_supercruise_target(ctx) is False
    assert s.actions() == []   # never even opened the panel


def test_engages_on_name_match(monkeypatch):
    """Name matches a row + detail confirms SC-assist -> walk there + engage."""
    monkeypatch.setattr(steps, "_resolve_named_row", lambda frame, name: 3)
    monkeypatch.setattr(navdetail, "confirm_button", lambda frame, expected: True)
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_target(ctx) is True
    assert s.actions() == [OPEN, *WALK_ROW3, SEL, RIGHT, SEL, OPEN]


def test_blind_when_no_detail_grabber(monkeypatch):
    monkeypatch.setattr(steps, "_resolve_named_row", lambda frame, name: 3)
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    assert step_nav_supercruise_target(ctx) is True
    assert s.actions() == [OPEN, *WALK_ROW3, SEL, RIGHT, SEL, OPEN]


def test_no_match_returns_false(monkeypatch):
    """Station name not found on the list -> open then close, no walk, False."""
    monkeypatch.setattr(steps, "_resolve_named_row", lambda frame, name: None)
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    assert step_nav_supercruise_target(ctx) is False
    assert s.actions() == [OPEN, OPEN]


def test_cv_error_fails_closed(monkeypatch):
    def boom(frame, name):
        raise RuntimeError("ocr blew up")
    monkeypatch.setattr(steps, "_resolve_named_row", boom)
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    assert step_nav_supercruise_target(ctx) is False
    assert s.actions() == [OPEN, OPEN]


def test_refuses_on_wrong_label(monkeypatch):
    monkeypatch.setattr(steps, "_resolve_named_row", lambda frame, name: 3)
    monkeypatch.setattr(navdetail, "confirm_button", lambda frame, expected: False)
    s = FakeSender()
    ctx = _ctx(s, navpanel=True)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_target(ctx) is False
    assert s.actions() == [OPEN, *WALK_ROW3, SEL, RIGHT, OPEN]   # no engage


def test_emergency_drop_returns_false(monkeypatch):
    monkeypatch.setattr(steps, "_resolve_named_row", lambda frame, name: 3)
    class _St:
        in_supercruise = False
    s = FakeSender()
    ctx = _ctx(s, navpanel=True, status_supplier=lambda: _St())
    assert step_nav_supercruise_target(ctx) is False


def test_bind_missing_returns_false():
    s = FakeSender(unbound={"FocusLeftPanel"})
    ctx = _ctx(s, navpanel=True)
    assert step_nav_supercruise_target(ctx) is False


def test_registered_input_exclusive():
    from ed_core.flow import input_exclusive_actions, merged_step_registry
    assert "nav_supercruise_target" in merged_step_registry()
    assert "nav_supercruise_target" in input_exclusive_actions()
