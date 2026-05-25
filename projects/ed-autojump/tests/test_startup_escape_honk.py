"""Startup-escape -> honk wiring.

On a fresh load we get off the star (realspace escape) and then honk the
system, exactly like a jump arrival. The honk can't fire inside tick_status
(it would re-enter the live-loop generator), so the escape sets a pending
flag and run_live fires the honk once, with the live journal stream.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ed_autojump.config import Config
from ed_autojump.journal.events import Event
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.state import GameState


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


def _flyable_realspace_status():
    """Minimal Status stand-in: undocked, in NORMAL space, all-clear flags."""
    return SimpleNamespace(
        docked=False,
        in_supercruise=False,
        overheating=False,
        is_in_danger=False,
        heat=0.2,
        fsd_charging=False,
        fsd_cooldown=False,
        fsd_mass_locked=False,
    )


class _EntersSupercruiseReader:
    """Realspace at the first poll (so tick_status takes the realspace branch),
    then flips in_supercruise=True on later polls so the escape's SC-entry wait
    succeeds — mimicking the ship actually entering supercruise."""

    def __init__(self, status, *, flip_after: int = 1):
        self._status = status
        self.current = status
        self._n = 0
        self._flip_after = flip_after

    def poll(self):
        self._n += 1
        if self._n > self._flip_after:
            self._status.in_supercruise = True
        return self._status


def _dark_frame():
    import numpy as np
    return np.zeros((10, 10, 3), dtype=np.uint8)  # no star -> escape engages fast


def test_startup_escape_sets_honk_pending(tmp_path: Path):
    """Realspace startup escape that successfully enters SC arms the system honk."""
    status = _flyable_realspace_status()
    ticks = iter(range(0, 100000, 1000))
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=None,
        state=GameState(),
        config=Config(),
        clock=lambda: float(next(ticks, 100000)),
        sleeper=lambda _t: None,
        status_reader=_EntersSupercruiseReader(status),
        sun_grab=_dark_frame,
    )
    assert orch._startup_honk_pending is False
    orch.tick_status()
    # Escape entered SC and armed the honk.
    assert orch._startup_honk_pending is True
    assert orch._startup_escape_pending is False  # success: not re-armed
    assert "Supercruise" in orch.sender.actions()
    # Realspace escape throttles up TWICE (engage + fly-clear).
    assert orch.sender.actions().count("SetSpeed100") == 2


def test_startup_escape_failure_rearms_and_does_not_honk(tmp_path: Path):
    """If SC entry never logs, the escape bails: re-arm for retry, do NOT honk."""
    status = _flyable_realspace_status()  # stays realspace -> SC entry never logs
    ticks = iter(range(0, 1000000, 1000))

    class _StuckReader:
        def __init__(self, st):
            self.current = st
            self._st = st

        def poll(self):
            return self._st  # never flips to supercruise

    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=None,
        state=GameState(),
        config=Config(),
        clock=lambda: float(next(ticks, 1000000)),
        sleeper=lambda _t: None,
        status_reader=_StuckReader(status),
        sun_grab=_dark_frame,
    )
    orch.tick_status()
    # Bailed: re-armed to retry next tick, honk NOT armed.
    assert orch._startup_escape_pending is True
    assert orch._startup_honk_pending is False
    # Only the engage throttle fired; no fly-away throttle, no route target.
    assert orch.sender.actions().count("SetSpeed100") == 1
    assert "TargetNextRouteSystem" not in orch.sender.actions()


def test_run_live_fires_pending_startup_honk(tmp_path: Path):
    """With the flag armed, the live loop presses the honk key once."""

    class _OneEventTail:
        """Yields a single benign event, then nothing (drives loop to deadline)."""
        def __init__(self):
            self._served = False

        def step(self) -> list[Event]:
            if self._served:
                return []
            self._served = True
            # A generic event the dispatcher ignores; just wakes the loop.
            return [Event(timestamp="2026-05-25T00:00:00Z", event="Music")]

    times = iter([0.0, 0.0, 0.0, 0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
    cfg = Config()
    assert cfg.exploration.honk is True  # default; the honk path is enabled
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=None,
        state=GameState(),
        config=cfg,
        clock=lambda: next(times, 200.0),
        sleeper=lambda _t: None,
    )
    orch._startup_honk_pending = True
    orch.run_live(_OneEventTail(), duration_s=50.0, poll_interval_s=0.1)
    orch.shutdown()
    # The honk key was pressed (retry_on_timeout fallback may press it twice).
    assert "ExplorationFSSDiscoveryScan" in orch.sender.actions()
    # And the flag was consumed (one-shot).
    assert orch._startup_honk_pending is False
