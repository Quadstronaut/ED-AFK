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

from ed_autojump.executor.navpanel import engage_supercruise_assist
from ed_autojump.keys import parse_binds
from ed_autojump.keys.sender import RecordingSender


BINDS_PATH = (
    Path(__file__).parent.parent
    / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"
)


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
