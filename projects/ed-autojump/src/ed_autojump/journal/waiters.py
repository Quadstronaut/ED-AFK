"""
Pre-game journal waiters.

The launcher needs to block-and-poll for two specific journal events
between the moment MinEdLauncher.exe is invoked and the moment the AFK
loop takes over:

  1. `Music { MusicTrack: "MainMenu" }` — main menu is up and the user
     (or bot) can navigate it. This is the only reliable, journal-grounded
     signal that the renderer is ready for keyboard input. Process-up,
     window-present, or wall-clock heuristics all race.

  2. `LoadGame` — the player has selected a mode and the session is
     loading in. For Group mode it includes the `Group` name, which we
     verify is the one we intended to join.

Both waiters take an injectable clock + sleep so the tests don't burn
wall-clock seconds and we can drive them deterministically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from .events import Event, LoadGame, Music


class _StepProtocol(Protocol):
    """Minimal interface the waiters need from JournalTail.

    Any object with a `.step() -> list[Event]` method works, which lets
    the tests pass a tiny FakeTail without subclassing the real one.
    """

    def step(self) -> list: ...


class LoadGameMismatch(RuntimeError):
    """LoadGame fired but the commander/group didn't match what we asked for.

    Raised instead of returning a failed result because this means the bot
    just entered the WRONG game session — something the operator needs to
    notice immediately, not the kind of thing to soft-fail past.
    """


@dataclass
class WaitResult:
    """Outcome of a wait call. `event` is the triggering event, or None on timeout."""

    found: bool
    timeout: bool
    event: Optional[Event] = None


def _poll_until(
    tail: _StepProtocol,
    *,
    predicate: Callable[[Event], bool],
    timeout_s: float,
    poll_interval_s: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> Optional[Event]:
    """Drive tail.step() in a loop until predicate matches or timeout.

    Returns the matching event, or None on timeout. Pure-loop helper so
    both public waiters share the same timing/poll semantics.
    """
    deadline = clock() + timeout_s
    while True:
        for ev in tail.step():
            if predicate(ev):
                return ev
        if clock() >= deadline:
            return None
        sleep(poll_interval_s)


def wait_for_main_menu(
    tail: _StepProtocol,
    *,
    timeout_s: float = 60.0,
    post_buffer_s: float = 3.0,
    poll_interval_s: float = 0.5,
    post_fileheader_wait_s: float = 10.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> WaitResult:
    """Block until ED's main menu is ready.

    Signal: `Fileheader` event (always fires on session start, regardless
    of in-game music or audio settings) + `post_fileheader_wait_s` elapsed
    wall-clock for the menu UI to become interactive.

    NB: Listening for the in-game audio output that begins when the menu
    is usable would require WASAPI loopback capture — a whole subsystem
    beyond journal parsing. Fileheader + delay is the journal-only proxy.

    Returns WaitResult(found=True) on match, WaitResult(timeout=True) if
    no Fileheader arrives within `timeout_s`.
    """
    deadline = clock() + timeout_s
    fileheader_seen_at: Optional[float] = None

    while True:
        for ev in tail.step():
            # Fileheader is a generic Event (no specialized model); match
            # by name. Frontier writes "Fileheader" (lowercase 'h').
            if getattr(ev, "event", None) == "Fileheader" and fileheader_seen_at is None:
                fileheader_seen_at = clock()

        if fileheader_seen_at is not None and (clock() - fileheader_seen_at) >= post_fileheader_wait_s:
            if post_buffer_s > 0:
                sleep(post_buffer_s)
            return WaitResult(found=True, timeout=False, event=None)

        if clock() >= deadline:
            return WaitResult(found=False, timeout=True)
        sleep(poll_interval_s)


def wait_for_load_game(
    tail: _StepProtocol,
    *,
    timeout_s: float = 120.0,
    expected_group: Optional[str] = None,
    expected_commander: Optional[str] = None,
    poll_interval_s: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> WaitResult:
    """Block until `LoadGame` fires, signalling the session loaded.

    If `expected_group` is set and the LoadGame event has a different
    `Group` field, raises LoadGameMismatch — we entered the wrong session
    and the bot should NOT proceed (otherwise we'd AFK-jump in a stranger's
    instance). Same for `expected_commander`.

    Returns WaitResult(found=True, event=ev) on match.
    """

    def is_load_game(ev: Event) -> bool:
        return isinstance(ev, LoadGame)

    found = _poll_until(
        tail,
        predicate=is_load_game,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
        clock=clock,
        sleep=sleep,
    )
    if found is None:
        return WaitResult(found=False, timeout=True)
    # Type-narrow for the post-checks (we know it's LoadGame from the predicate).
    assert isinstance(found, LoadGame)
    if expected_commander is not None and found.commander != expected_commander:
        raise LoadGameMismatch(
            f"LoadGame fired for commander {found.commander!r}, expected {expected_commander!r}"
        )
    if expected_group is not None and found.group != expected_group:
        raise LoadGameMismatch(
            f"LoadGame fired for group {found.group!r}, expected {expected_group!r}"
        )
    return WaitResult(found=True, timeout=False, event=found)
