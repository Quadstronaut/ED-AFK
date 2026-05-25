"""
Phase 13 — pre-game waiters.

`wait_for_main_menu(tail)` blocks until the journal shows Music{MainMenu}
plus a buffer; `wait_for_load_game(tail)` blocks until a session is loaded
in (optionally verifying group + commander). Both used by the launcher to
bracket the main-menu navigation phase.

Tests use a FakeTail that returns pre-staged event batches per step(), so
no filesystem or wall-clock is touched.
"""

from __future__ import annotations

from typing import Optional

import pytest

from ed_autojump.journal.events import LoadGame, Music, parse_event
from ed_autojump.journal.waiters import (
    LoadGameMismatch,
    WaitResult,
    wait_for_load_game,
    wait_for_main_menu,
)


class FakeTail:
    """Stand-in for JournalTail.step() — returns the next staged batch."""

    def __init__(self, batches: list[list]):
        self._batches = list(batches)

    def step(self):
        if not self._batches:
            return []
        return self._batches.pop(0)


class FakeClock:
    """Monotonic clock that advances on read; sleep() jumps it forward."""

    def __init__(self, start: float = 0.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


def _fileheader():
    return parse_event({"timestamp": "2026-05-23T00:00:00Z", "event": "Fileheader",
                        "part": 1, "language": "English/UK", "Odyssey": True,
                        "gameversion": "4.3.3.0", "build": "r327343/r0 "})


def _loadgame(group: Optional[str] = "Quadstronaut", commander: str = "Duvrazh",
              game_mode: str = "Group"):
    payload = {
        "timestamp": "2026-05-23T00:01:00Z",
        "event": "LoadGame",
        "Commander": commander,
        "GameMode": game_mode,
    }
    if group is not None:
        payload["Group"] = group
    return parse_event(payload)


# --- wait_for_main_menu (Fileheader + delay) ------------------------------


def test_wait_for_main_menu_returns_after_fileheader_plus_delay():
    """Fileheader fires → bot waits post_fileheader_wait_s → returns found."""
    tail = FakeTail([[_fileheader()], [], [], [], []])
    clock = FakeClock()
    r = wait_for_main_menu(
        tail, timeout_s=60.0, post_buffer_s=0.0, poll_interval_s=1.0,
        post_fileheader_wait_s=5.0,
        clock=clock.now, sleep=clock.sleep,
    )
    assert r.found is True
    assert r.timeout is False
    assert r.event is None  # No specific event passed back — Fileheader is generic
    assert clock.t >= 5.0


def test_wait_for_main_menu_applies_post_buffer_on_top_of_fileheader_wait():
    """post_buffer_s gives the UI an extra beat after the Fileheader window."""
    tail = FakeTail([[_fileheader()]])
    clock = FakeClock()
    r = wait_for_main_menu(
        tail, timeout_s=60.0, post_buffer_s=3.0, poll_interval_s=0.5,
        post_fileheader_wait_s=5.0,
        clock=clock.now, sleep=clock.sleep,
    )
    assert r.found is True
    assert clock.t >= 5.0 + 3.0


def test_wait_for_main_menu_times_out_with_no_fileheader():
    tail = FakeTail([[], [], [], [], []])
    clock = FakeClock()
    r = wait_for_main_menu(
        tail, timeout_s=2.0, post_buffer_s=0.0, poll_interval_s=0.5,
        post_fileheader_wait_s=10.0,
        clock=clock.now, sleep=clock.sleep,
    )
    assert r.found is False
    assert r.timeout is True
    assert clock.t >= 2.0


def test_wait_for_main_menu_polls_until_fileheader():
    """Until Fileheader arrives, the waiter polls. After it arrives, the
    fixed delay elapses, then it returns."""
    tail = FakeTail([[], [], [_fileheader()], [], [], [], [], [], [], []])
    clock = FakeClock()
    r = wait_for_main_menu(
        tail, timeout_s=60.0, post_buffer_s=0.0, poll_interval_s=0.5,
        post_fileheader_wait_s=3.0,
        clock=clock.now, sleep=clock.sleep,
    )
    assert r.found is True


# --- wait_for_load_game ---------------------------------------------------


def test_wait_for_load_game_returns_when_loadgame_fires():
    tail = FakeTail([[_loadgame()]])
    clock = FakeClock()
    r = wait_for_load_game(
        tail, timeout_s=120.0, poll_interval_s=0.5,
        clock=clock.now, sleep=clock.sleep,
    )
    assert r.found is True
    assert r.event.commander == "Duvrazh"


def test_wait_for_load_game_verifies_group_when_required():
    """If expected_group is given, the waiter raises if LoadGame fires
    with a different group — we entered the wrong session."""
    tail = FakeTail([[_loadgame(group="WrongGroup")]])
    clock = FakeClock()
    with pytest.raises(LoadGameMismatch, match="WrongGroup"):
        wait_for_load_game(
            tail, timeout_s=10.0, expected_group="Quadstronaut",
            poll_interval_s=0.1, clock=clock.now, sleep=clock.sleep,
        )


def test_wait_for_load_game_verifies_commander_when_required():
    tail = FakeTail([[_loadgame(commander="Bistronaut")]])
    clock = FakeClock()
    with pytest.raises(LoadGameMismatch, match="Bistronaut"):
        wait_for_load_game(
            tail, timeout_s=10.0, expected_commander="Duvrazh",
            poll_interval_s=0.1, clock=clock.now, sleep=clock.sleep,
        )


def test_wait_for_load_game_times_out_with_no_event():
    tail = FakeTail([[]] * 10)
    clock = FakeClock()
    r = wait_for_load_game(
        tail, timeout_s=2.0, poll_interval_s=0.5,
        clock=clock.now, sleep=clock.sleep,
    )
    assert r.found is False
    assert r.timeout is True
    assert clock.t >= 2.0


def test_wait_for_load_game_solo_mode_skips_group_check():
    """Solo mode has no Group field at all — that's not a mismatch."""
    tail = FakeTail([[_loadgame(group=None, game_mode="Solo")]])
    clock = FakeClock()
    r = wait_for_load_game(
        tail, timeout_s=10.0, expected_group=None,
        poll_interval_s=0.1, clock=clock.now, sleep=clock.sleep,
    )
    assert r.found is True
