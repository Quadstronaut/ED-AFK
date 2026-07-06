"""Tests for step_nav_supercruise_star: STAR-ROW confirm + #8 label-confirm.

LIVE REVISION 2026-07-06 (run 010444 starsmack): "row 0 is always the arrival
star" is REFUTED — a nav beacon / signal row can sort first and its detail page
also offers SUPERCRUISE ASSIST. The step now (a) classifies row 0's column-0
icon with the trained star oracle before touching it (fail-closed), and
(b) re-reads a transiently-unreadable button label IN-STEP (bounded) instead
of failing the whole procedure — the operator-hated triple panel-open.
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
    the row0-unconfirmed refusal is covered in test_confirm_row0.py. Tests with no
    navpanel_frame_grabber never invoke this, so the patch is inert for them."""
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


def test_refuses_on_wrong_label_and_logs_raw_text(monkeypatch):
    """Grabber wired + label NOT the SC-assist OFF button (all reads) -> DO NOT
    press engage; close the panel, fail-closed, and the refusal carries the
    RAW OCR text (live findings 3/5: refusals were undiagnosable without it)."""
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: _read(DetailButton.LOCK, "LOCK DESTINATION"))
    s = FakeSender()
    logs = []
    ctx = StepContext(sender=s, sleeper=lambda _x: None,
                      record=lambda k, p: logs.append((k, p)))
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is False
    # open -> detail -> right -> CLOSE.  No second UI_Select (no engage).
    assert s.actions() == [OPEN, SEL, RIGHT, OPEN]
    refusals = [p for k, p in logs if k == "NavSupercruiseStarRefused"]
    assert refusals and refusals[0]["label"] == "LOCK DESTINATION"


def test_transient_bad_read_recovers_in_step(monkeypatch):
    """Live findings 3/5 fix: a transient UNKNOWN read must NOT fail the step —
    the label is re-read in place and the engage still fires. No procedure
    retry, no repeated panel opens."""
    reads = [_read(DetailButton.UNKNOWN), _read(DetailButton.UNKNOWN),
             _read(DetailButton.SC_ASSIST, "SUPERCRUISE ASSIST")]
    monkeypatch.setattr(navdetail, "read_detail_button_label",
                        lambda frame: reads.pop(0) if reads else _read(DetailButton.SC_ASSIST))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]   # ONE panel pass


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


def test_cv_error_fails_closed(monkeypatch):
    """A grabber/CV exception is swallowed and treated as not-confirmed -> refuse."""
    def boom(frame):
        raise RuntimeError("ocr blew up")
    monkeypatch.setattr(navdetail, "read_detail_button_label", boom)
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_detail_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is False
    assert s.actions() == [OPEN, SEL, RIGHT, OPEN]


# ---- STAR-ROW confirm (live 2026-07-06 starsmack fix) ------------------------

def test_row0_not_star_refuses_before_opening_detail(monkeypatch):
    """THE STARSMACK FIX: the selected row classifies as anything but STAR
    (here: a nav beacon / signal row) -> refuse BEFORE UI_Select — the row's
    detail page is never opened, nothing is pressed at it. All in-place
    re-reads happen within the ONE panel open (operator: "we do shit once")
    and the refusal payload carries the read count."""
    calls = []
    monkeypatch.setattr(navicons, "detect_selected_row_star",
                        lambda frame: calls.append(1) or (navicons.NON_STAR, 0.07))
    s = FakeSender()
    logs = []
    ctx = StepContext(sender=s, sleeper=lambda _x: None,
                      record=lambda k, p: logs.append((k, p)))
    ctx.navpanel_frame_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is False
    assert s.actions() == [OPEN, OPEN]                    # open -> refuse -> close
    assert len(calls) == 3                                # bounded in-place re-reads
    refusals = [p for k, p in logs if k == "NavSupercruiseStarRefused"]
    assert refusals and refusals[0]["reason"] == "row0_not_star"
    assert refusals[0]["score"] == 0.07                   # confidence is logged
    assert refusals[0]["reads"] == 3


def test_transient_row_read_recovers_in_step(monkeypatch):
    """Run-085221 loop fix: a transiently-unreadable row (bad frame, mid-fade)
    must NOT fail the step — the row is re-read in place within the SAME panel
    open and the macro proceeds. No procedure retry, no repeated panel opens."""
    verdicts = [(navicons.NONE, 0.0), (navicons.STAR, 0.71)]
    monkeypatch.setattr(navicons, "detect_selected_row_star",
                        lambda frame: verdicts.pop(0) if verdicts else (navicons.STAR, 0.71))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_frame_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]   # ONE panel pass


def test_row0_star_proceeds_to_label_confirm(monkeypatch):
    """Selected row confirmed STAR -> the macro proceeds (blind label path)."""
    monkeypatch.setattr(navicons, "detect_selected_row_star",
                        lambda frame: (navicons.STAR, 0.79))
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_frame_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is True
    assert s.actions() == [OPEN, SEL, RIGHT, SEL, OPEN]


def test_row_cv_error_fails_closed(monkeypatch):
    """Row-icon CV exception -> refuse, press nothing at the row."""
    def boom(frame):
        raise RuntimeError("icon cv blew up")
    monkeypatch.setattr(navicons, "detect_selected_row_star", boom)
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.navpanel_frame_grabber = lambda: "frame"
    assert step_nav_supercruise_star(ctx) is False
    assert s.actions() == [OPEN, OPEN]


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
