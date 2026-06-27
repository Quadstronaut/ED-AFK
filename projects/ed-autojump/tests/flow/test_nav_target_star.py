"""Tests for step_nav_target_star (#3): blind row-0 lock + #8 label-confirm.

The arrival star is always nav-panel row 0 (memory arrival-star-row0-blind-sc-assist),
so the step opens the star's detail page (cursor on LOCK DESTINATION — the first
control) and, IF a detail grabber is wired, READS the label to decide:
  - LOCK   -> press once (lock it),
  - UNLOCK -> already locked, no-op (kills the blind double-toggle unlock bug),
  - anything else -> refuse (fail closed).
This is target_via_navpanel minus the unlock hazard.
"""

import ed_vision.navpanel_detail as navdetail
from ed_vision.navpanel_detail import DetailButton, DetailLabelRead
from ed_core.flow.context import StepContext

from ed_autojump.flow.steps import step_nav_target_star

from . import FakeSender

OPEN, SEL = "FocusLeftPanel", "UI_Select"


def _read(button):
    """A DetailLabelRead carrying `button` (confident unless UNKNOWN)."""
    return DetailLabelRead(button, button.value, button is not DetailButton.UNKNOWN)


def test_blind_when_no_grabber():
    """No detail grabber wired -> pure blind lock toggle (zero regression vs
    target_via_navpanel): open -> detail -> SELECT(lock) -> close, no CV gate."""
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    assert step_nav_target_star(ctx) is True
    assert s.actions() == [OPEN, SEL, SEL, OPEN]


def test_locks_when_label_is_lock(monkeypatch):
    """Grabber wired + label is LOCK DESTINATION (not yet locked) -> press once."""
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.LOCK))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_target_star(ctx) is True
    assert s.actions() == [OPEN, SEL, SEL, OPEN]


def test_noop_when_already_locked(monkeypatch):
    """Grabber wired + label is UNLOCK DESTINATION (already locked) -> NO second
    UI_Select (a press would UNLOCK the star — the 2026-06-06 toggle bug). Close
    and report success (the star IS locked)."""
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.UNLOCK))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_target_star(ctx) is True
    assert s.actions() == [OPEN, SEL, OPEN]   # no lock press


def test_refuses_on_unknown_label(monkeypatch):
    """Unreadable / wrong control -> never blind-press an unconfirmed lock."""
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.UNKNOWN))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_target_star(ctx) is False
    assert s.actions() == [OPEN, SEL, OPEN]


def test_refuses_on_sc_assist_label(monkeypatch):
    """A non-lock control (e.g. SC_ASSIST) is NOT a lock -> refuse, never press."""
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.SC_ASSIST))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_target_star(ctx) is False
    assert s.actions() == [OPEN, SEL, OPEN]


def test_cv_error_fails_closed(monkeypatch):
    """A grabber/CV exception is swallowed and treated as not-confirmed -> refuse."""
    def boom(frame):
        raise RuntimeError("ocr blew up")
    monkeypatch.setattr(navdetail, "read_detail_button_label", boom)
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_target_star(ctx) is False
    assert s.actions() == [OPEN, SEL, OPEN]


def test_bind_missing_returns_false():
    s = FakeSender(unbound={"FocusLeftPanel"})
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    assert step_nav_target_star(ctx) is False


def test_registered_input_exclusive():
    """The new action is registered and owns input (heat watchdog pauses for it)."""
    from ed_core.flow import input_exclusive_actions, merged_step_registry
    assert "nav_target_star" in merged_step_registry()
    assert "nav_target_star" in input_exclusive_actions()
