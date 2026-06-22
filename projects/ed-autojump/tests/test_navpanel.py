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
    grab_navpanel_destination,
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


# ── grab_navpanel_destination: open panel -> grab1 -> resolve dest row ->
# pin+walk the cursor ONTO it -> grab2 (returned, dest highlighted) -> close.
# Fail-soft: unresolved row / grab/bind errors -> None, panel still closes.

def test_grab_navpanel_destination_walks_then_grabs_second():
    """open -> grab1 -> resolve_row=2 -> pin (UI_Down + held UI_Up) + UI_Down x2 ->
    grab2 (RETURNED) -> close. The SECOND grab (dest selected) is returned."""
    sender = _sender()
    sleeper = _RecordingSleeper()
    frames = ["f1", "f2"]
    seen = {}

    def grab():
        return frames.pop(0)

    def resolve(f):
        seen["resolved_on"] = f          # resolve runs on grab1
        return 2

    out = grab_navpanel_destination(sender, grab, resolve, sleeper=sleeper,
                                    settle_s=0.4)
    assert out == "f2"                     # second grab returned
    assert seen["resolved_on"] == "f1"     # row resolved from the FIRST grab
    acts = sender.actions()
    assert acts[0] == "FocusLeftPanel" and acts[-1] == "FocusLeftPanel"
    assert acts.count("UI_Down") == 3      # 1 pin tap + 2 rows walked
    assert "UI_Up" in acts                 # pin hold


def test_grab_navpanel_destination_row0_pin_only():
    """row 0 -> the pin's own single UI_Down tap, no extra walk downs."""
    sender = _sender()
    frames = ["f1", "f2"]
    out = grab_navpanel_destination(sender, lambda: frames.pop(0),
                                    lambda f: 0, sleeper=lambda _s: None)
    assert out == "f2"
    assert sender.actions().count("UI_Down") == 1   # pin tap only (rows_down=0)


def test_grab_navpanel_destination_unresolved_row_aborts_without_walk():
    """resolve_row -> None (destination not on screen) -> NO walk, NO second grab,
    panel closed, returns None."""
    sender = _sender()
    grabs = []

    def grab():
        grabs.append(1)
        return "f1"

    out = grab_navpanel_destination(sender, grab, lambda f: None,
                                    sleeper=lambda _s: None)
    assert out is None
    assert "UI_Down" not in sender.actions()                       # no walk
    assert sender.actions() == ["FocusLeftPanel", "FocusLeftPanel"]  # open + close only
    assert len(grabs) == 1                                          # only the OCR grab


def test_grab_navpanel_destination_open_failure_returns_none():
    """Opening the panel fails (unbound key) -> None, no crash, nothing to close."""
    class _BoomSender:
        def press(self, *a, **k):
            raise KeyError("FocusLeftPanel")

    assert grab_navpanel_destination(_BoomSender(), lambda: object(),
                                     lambda f: 0, sleeper=lambda _s: None) is None
