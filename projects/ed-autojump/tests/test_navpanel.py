"""
Nav-panel Supercruise-Assist macro tests.

Supercruise Assist has no keybind; it's toggled through the left nav
panel's detail pane. engage_supercruise_assist is a blind keystroke macro
that relies on the arrival star being the default-selected top row. We
assert the EXACT press sequence and that a settle sleep is injected after
every press, using the RecordingSender (records presses, raises KeyError
on unbound actions) and the real bundled binds preset.
"""

from __future__ import annotations

from pathlib import Path

from ed_autojump.executor.navpanel import (
    engage_supercruise_assist,
    grab_navpanel_frame,
    target_via_navpanel,
)
from ed_core.keys import parse_binds
from ed_core.keys.sender import RecordingSender


BINDS_PATH = Path(__file__).parent / "fixtures" / "ED-AFK.legacy.binds"


def _sender() -> RecordingSender:
    return RecordingSender(parse_binds(BINDS_PATH))


class _RecordingSleeper:
    """Records every duration passed to it instead of sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def test_exact_press_sequence():
    """focus -> UI_Select -> UI_Right -> UI_Select -> focus."""
    sender = _sender()
    engage_supercruise_assist(sender, sleeper=lambda _s: None)
    assert sender.actions() == [
        "FocusLeftPanel",
        "UI_Select",
        "UI_Right",
        "UI_Select",
        "FocusLeftPanel",
    ]


def test_settle_sleeps_injected_after_every_press():
    """One settle sleep per press, all equal to settle_s; nothing real slept."""
    sender = _sender()
    sleeper = _RecordingSleeper()
    engage_supercruise_assist(sender, sleeper=sleeper, settle_s=0.4)
    # Five presses -> five settle sleeps.
    assert len(sleeper.calls) == len(sender.events) == 5
    assert all(d == 0.4 for d in sleeper.calls)


def test_custom_panel_focus_action_is_used():
    """The panel-focus action is parameterised; a custom one is honoured at
    both ends of the macro (open and close)."""
    sender = _sender()
    engage_supercruise_assist(
        sender, sleeper=lambda _s: None, panel_focus_action="UIFocus"
    )
    acts = sender.actions()
    assert acts[0] == "UIFocus"
    assert acts[-1] == "UIFocus"
    assert acts[1:-1] == ["UI_Select", "UI_Right", "UI_Select"]


def test_all_macro_actions_are_bound():
    """RecordingSender raises KeyError on unbound actions; reaching the end
    of the default macro without raising proves every action is bound."""
    sender = _sender()
    # Would raise KeyError mid-run if any action were unbound.
    engage_supercruise_assist(sender, sleeper=lambda _s: None)
    assert len(sender.events) == 5


# ── target_via_navpanel: SAME mechanic as the SC-assist macro, MINUS the
# UI_Right step. "Lock Destination" is the first item in the row's target
# submenu, so the second UI_Select activates it. Four presses, not five.

def test_target_via_navpanel_exact_press_sequence():
    """focus -> UI_Select -> UI_Select -> focus. Four presses (no UI_Right)."""
    sender = _sender()
    target_via_navpanel(sender, sleeper=lambda _s: None)
    assert sender.actions() == [
        "FocusLeftPanel",
        "UI_Select",
        "UI_Select",
        "FocusLeftPanel",
    ]


def test_target_via_navpanel_settle_after_every_press():
    sender = _sender()
    sleeper = _RecordingSleeper()
    target_via_navpanel(sender, sleeper=sleeper, settle_s=0.4)
    assert len(sleeper.calls) == len(sender.events) == 4
    assert all(d == 0.4 for d in sleeper.calls)


def test_target_via_navpanel_custom_panel_focus_action():
    sender = _sender()
    target_via_navpanel(
        sender, sleeper=lambda _s: None, panel_focus_action="UIFocus"
    )
    acts = sender.actions()
    assert acts[0] == "UIFocus"
    assert acts[-1] == "UIFocus"
    assert acts[1:-1] == ["UI_Select", "UI_Select"]


def test_target_via_navpanel_all_actions_bound():
    sender = _sender()
    target_via_navpanel(sender, sleeper=lambda _s: None)
    assert len(sender.events) == 4


# ── grab_navpanel_frame: open panel -> ONE full-frame grab (panel open,
# destination highlighted) -> close panel. The route-complete icon classifier's
# frame source wrapper. Fail-soft: grab/bind errors return None, panel still closes.

def test_grab_navpanel_frame_opens_grabs_closes():
    """FocusLeftPanel (open) -> grab -> FocusLeftPanel (close); the grab fires
    AFTER the open and BEFORE the close, and the frame is returned."""
    sender = _sender()
    sleeper = _RecordingSleeper()
    sentinel = object()
    seen = {}

    def grab():
        seen["presses_at_grab"] = len(sender.actions())   # 1 -> open done, close pending
        return sentinel

    frame = grab_navpanel_frame(sender, grab, sleeper=sleeper, settle_s=0.4)
    assert frame is sentinel
    assert sender.actions() == ["FocusLeftPanel", "FocusLeftPanel"]
    assert seen["presses_at_grab"] == 1
    assert sleeper.calls == [0.4, 0.4]


def test_grab_navpanel_frame_closes_even_if_grab_raises():
    """A grab that raises -> None, but the panel is STILL closed (no stuck-open
    panel) and the terminal handler never sees the exception."""
    sender = _sender()

    def grab():
        raise RuntimeError("capture backend died")

    frame = grab_navpanel_frame(sender, grab, sleeper=lambda _s: None)
    assert frame is None
    assert sender.actions() == ["FocusLeftPanel", "FocusLeftPanel"]   # opened AND closed


def test_grab_navpanel_frame_open_failure_returns_none():
    """If even opening the panel fails (e.g., unbound key) -> None, no crash,
    nothing to close."""
    class _BoomSender:
        def press(self, *a, **k):
            raise KeyError("FocusLeftPanel")

    assert grab_navpanel_frame(_BoomSender(), lambda: object(),
                               sleeper=lambda _s: None) is None
