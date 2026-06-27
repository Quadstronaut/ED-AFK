"""Tests for step_nav_supercruise_star (#4): blind row-0 SC-assist + #8 label-confirm.

The arrival star is always nav-panel row 0 (memory arrival-star-row0-blind-sc-assist),
so the step blind-walks onto the Supercruise Assist button and, IF a detail-page
grabber is wired, CONFIRMS the label reads SUPERCRUISE ASSIST before pressing.
"""

import ed_vision.navpanel_detail as navdetail
from ed_core.flow.context import StepContext

from ed_autojump.flow.steps import step_nav_supercruise_star

from . import FakeSender

OPEN, SEL, RIGHT = "FocusLeftPanel", "UI_Select", "UI_Right"


def test_blind_when_no_grabber():
    """No detail grabber wired -> pure blind macro (zero regression vs today):
    open -> detail -> right -> ENGAGE -> close, no CV gate."""
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]


def test_confirms_then_presses(monkeypatch):
    """Grabber wired + label IS the SC-assist button -> engage press fires."""
    monkeypatch.setattr(navdetail, "confirm_button", lambda frame, expected: True)
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]


def test_refuses_on_wrong_label(monkeypatch):
    """Grabber wired + label NOT the SC-assist OFF button -> DO NOT press engage;
    close the panel and fail-closed (never fire a wrong/already-on control)."""
    monkeypatch.setattr(navdetail, "confirm_button", lambda frame, expected: False)
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is False
    # open -> detail -> right -> CLOSE.  No second UI_Select (no engage).
    assert s.actions() == [OPEN, SEL, RIGHT, OPEN]


def test_cv_error_fails_closed(monkeypatch):
    """A grabber/CV exception is swallowed and treated as not-confirmed -> refuse."""
    def boom(frame, expected):
        raise RuntimeError("ocr blew up")
    monkeypatch.setattr(navdetail, "confirm_button", boom)
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is False
    assert s.actions() == [OPEN, SEL, RIGHT, OPEN]


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
