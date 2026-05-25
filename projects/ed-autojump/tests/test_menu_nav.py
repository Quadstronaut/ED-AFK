"""
Main-menu navigator — Continue → PG → (Select Group) → Launch.

Tests verify the key sequence + counts for both branches:
- Member commander (e.g. Duvrazh): full flow including select-group
- Owner commander (e.g. Quadstronaut): skips select-group step

Sender is a recording fake; sleep is recorded too so the timing schedule
is asserted without burning wall-clock seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from ed_autojump.config import MenuNavConfig
from ed_autojump.launcher.menu_nav import (
    DOWN_ARROW_SCANCODE,
    ENTER_SCANCODE,
    ESCAPE_SCANCODE,
    LEFT_ARROW_SCANCODE,
    RIGHT_ARROW_SCANCODE,
    SPACE_SCANCODE,
    UP_ARROW_SCANCODE,
    MenuNavigator,
    MenuNavError,
)


@dataclass
class _RecordedKey:
    scancode: int
    extended: bool
    hold_s: float


class _FakeSender:
    def __init__(self):
        self.events: list[Any] = []  # alternating "press" and "sleep" entries

    def press_raw(self, scancode: int, *, extended: bool = False, hold: float = 0.05):
        self.events.append(_RecordedKey(scancode, extended, hold))


def _recorded_press_scancodes(sender: _FakeSender):
    return [e.scancode for e in sender.events if isinstance(e, _RecordedKey)]


def _recorded_extended_flags(sender: _FakeSender):
    return [e.extended for e in sender.events if isinstance(e, _RecordedKey)]


def _calibration_for_member():
    """Calibration dict for a non-owner commander (legacy down-only schema)."""
    return {
        "continue_key": "space",
        "post_continue_wait_s": 0.5,
        "down_to_pg_count": 1,
        "post_pg_wait_s": 0.5,
        "down_to_group_in_list": 2,
        "post_group_wait_s": 0.5,
    }


def _calibration_for_owner():
    """Owner skips the select-group step entirely (legacy schema)."""
    return {
        "continue_key": "space",
        "post_continue_wait_s": 0.5,
        "down_to_pg_count": 1,
        "post_pg_wait_s": 0.5,
        # No down_to_group_in_list — owner lands directly in their lobby.
    }


def _calibration_horizontal_pg():
    """New direction-aware schema with HORIZONTAL mode-select (Right arrow)
    and VERTICAL group list (Down arrow). Matches ED 2026 layout."""
    return {
        "continue_key": "space",
        "pg_nav_direction": "right",
        "pg_nav_count": 2,
        "group_nav_direction": "down",
        "group_nav_count": 1,
    }


def _cfg(calibration: dict):
    return MenuNavConfig(
        enabled=True,
        key_delay_ms=0,
        dismiss_dialogs=False,
        group_owner_commander="Quadstronaut",
        calibration=calibration,
    )


def test_member_commander_sends_full_sequence():
    """Duvrazh: Continue → 1×Down → Enter (PG) → 2×Down → Enter (Quad) → Enter (Launch)."""
    cfg = _cfg({"Duvrazh": _calibration_for_member()})
    sender = _FakeSender()
    sleeps: list[float] = []
    nav = MenuNavigator(sender=sender, config=cfg, sleep=sleeps.append)
    nav.navigate(commander="Duvrazh")
    sc = _recorded_press_scancodes(sender)
    # Expected: Space, Down, Enter, Down, Down, Enter, Enter
    assert sc == [
        SPACE_SCANCODE,
        DOWN_ARROW_SCANCODE,
        ENTER_SCANCODE,
        DOWN_ARROW_SCANCODE, DOWN_ARROW_SCANCODE,
        ENTER_SCANCODE,
        ENTER_SCANCODE,
    ]


def test_member_arrow_keys_use_extended_scancode():
    cfg = _cfg({"Duvrazh": _calibration_for_member()})
    sender = _FakeSender()
    nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
    nav.navigate(commander="Duvrazh")
    sc = _recorded_press_scancodes(sender)
    ext = _recorded_extended_flags(sender)
    # Down-arrow presses (positions 1, 3, 4) must be extended; Enter/Space (0,2,5,6) not.
    for i, code in enumerate(sc):
        if code == DOWN_ARROW_SCANCODE:
            assert ext[i] is True, f"Down at position {i} not extended"
        else:
            assert ext[i] is False, f"Non-arrow at position {i} marked extended"


def test_owner_commander_skips_select_group_step():
    """Quadstronaut: Continue → 1×Down → Enter (PG) → Enter (Launch) — 4 keys, not 7."""
    cfg = _cfg({"Quadstronaut": _calibration_for_owner()})
    sender = _FakeSender()
    nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
    nav.navigate(commander="Quadstronaut")
    sc = _recorded_press_scancodes(sender)
    assert sc == [
        SPACE_SCANCODE,
        DOWN_ARROW_SCANCODE,
        ENTER_SCANCODE,
        ENTER_SCANCODE,
    ]


def test_continue_key_enter_variant():
    """Some menus use Enter for Continue, others Space — config-driven."""
    cal = _calibration_for_owner()
    cal["continue_key"] = "enter"
    cfg = _cfg({"Quadstronaut": cal})
    sender = _FakeSender()
    nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
    nav.navigate(commander="Quadstronaut")
    assert _recorded_press_scancodes(sender)[0] == ENTER_SCANCODE


def test_missing_calibration_raises():
    """A commander not in the calibration dict cannot be driven safely."""
    cfg = _cfg({})  # empty calibration
    sender = _FakeSender()
    nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
    with pytest.raises(MenuNavError, match="no calibration"):
        nav.navigate(commander="Duvrazh")


def test_member_without_group_press_count_raises():
    """Member commander must have group navigation calibrated — otherwise
    we'd silently launch into the WRONG group (the default-highlighted one).
    """
    incomplete = _calibration_for_member()
    del incomplete["down_to_group_in_list"]
    cfg = _cfg({"Duvrazh": incomplete})
    sender = _FakeSender()
    nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
    with pytest.raises(MenuNavError, match="group_nav_direction|down_to_group_in_list"):
        nav.navigate(commander="Duvrazh")


# --- new direction-aware schema ----------------------------------------


def test_horizontal_pg_nav_uses_right_arrow():
    """ED 2026 mode-select is horizontal — calibration says 'right', 2 → bot
    sends RIGHT_ARROW twice, then Enter, then group nav, then Launch."""
    cfg = _cfg({"Duvrazh": _calibration_horizontal_pg()})
    sender = _FakeSender()
    nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
    nav.navigate(commander="Duvrazh")
    sc = _recorded_press_scancodes(sender)
    # Expect: Space (Continue), Right×2 (to PG), Enter, Down×1 (group), Enter, Enter (Launch)
    assert sc == [
        SPACE_SCANCODE,
        RIGHT_ARROW_SCANCODE, RIGHT_ARROW_SCANCODE,
        ENTER_SCANCODE,
        DOWN_ARROW_SCANCODE,
        ENTER_SCANCODE,
        ENTER_SCANCODE,
    ]


def test_horizontal_pg_nav_owner_skips_group():
    """Owner uses horizontal PG nav but skips select-group."""
    cal = {
        "continue_key": "space",
        "pg_nav_direction": "right",
        "pg_nav_count": 2,
    }
    cfg = _cfg({"Quadstronaut": cal})
    sender = _FakeSender()
    nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
    nav.navigate(commander="Quadstronaut")
    sc = _recorded_press_scancodes(sender)
    # Space → Right×2 → Enter → Enter (Launch). No group nav.
    assert sc == [
        SPACE_SCANCODE,
        RIGHT_ARROW_SCANCODE, RIGHT_ARROW_SCANCODE,
        ENTER_SCANCODE,
        ENTER_SCANCODE,
    ]


def test_invalid_direction_in_calibration_raises():
    cal = {"continue_key": "space", "pg_nav_direction": "diagonal", "pg_nav_count": 1}
    cfg = _cfg({"Quadstronaut": cal})
    sender = _FakeSender()
    nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
    with pytest.raises(MenuNavError, match="pg_nav_direction"):
        nav.navigate(commander="Quadstronaut")


def test_all_four_directions_dispatch_correct_scancode():
    """left/right/up/down all map to the correct extended scancode."""
    expected = {
        "left": LEFT_ARROW_SCANCODE,
        "right": RIGHT_ARROW_SCANCODE,
        "up": UP_ARROW_SCANCODE,
        "down": DOWN_ARROW_SCANCODE,
    }
    for direction, sc_expected in expected.items():
        cal = {
            "continue_key": "space",
            "pg_nav_direction": direction,
            "pg_nav_count": 1,
        }
        cfg = _cfg({"Quadstronaut": cal})
        sender = _FakeSender()
        nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
        nav.navigate(commander="Quadstronaut")
        sc = _recorded_press_scancodes(sender)
        # Position 1 is the arrow press (0 is Space continue).
        assert sc[1] == sc_expected, f"direction={direction}"


def test_disabled_navigator_refuses_to_run():
    """When menu_nav.enabled = False, the bot must not silently navigate;
    the user has to flip the flag explicitly after calibration."""
    cfg = MenuNavConfig(enabled=False, calibration={"Duvrazh": _calibration_for_member()})
    sender = _FakeSender()
    nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
    with pytest.raises(MenuNavError, match="disabled"):
        nav.navigate(commander="Duvrazh")


def test_navigator_respects_key_delay_ms():
    """Between presses the navigator sleeps key_delay_ms — needed because
    ED's menu input throttles on rapid key spam."""
    cfg = MenuNavConfig(
        enabled=True,
        key_delay_ms=100,  # 0.1s per inter-key
        dismiss_dialogs=False,
        group_owner_commander="Quadstronaut",
        calibration={"Quadstronaut": _calibration_for_owner()},
    )
    sender = _FakeSender()
    sleeps: list[float] = []
    nav = MenuNavigator(sender=sender, config=cfg, sleep=sleeps.append)
    nav.navigate(commander="Quadstronaut")
    # We pressed 4 keys; need at least 3 inter-key sleeps of ≥0.1s.
    inter_key_sleeps = [s for s in sleeps if s == 0.1]
    assert len(inter_key_sleeps) >= 3


def test_dismiss_dialogs_sends_escape_first():
    """When dismiss_dialogs=True, send Escape before the main sequence to
    clear any patch-notes / cosmetics nag screen left over from launch."""
    cfg = MenuNavConfig(
        enabled=True, key_delay_ms=0, dismiss_dialogs=True,
        group_owner_commander="Quadstronaut",
        calibration={"Quadstronaut": _calibration_for_owner()},
    )
    sender = _FakeSender()
    nav = MenuNavigator(sender=sender, config=cfg, sleep=lambda _: None)
    nav.navigate(commander="Quadstronaut")
    sc = _recorded_press_scancodes(sender)
    assert sc[0] == ESCAPE_SCANCODE
