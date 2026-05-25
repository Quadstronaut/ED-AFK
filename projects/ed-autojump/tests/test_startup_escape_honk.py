"""Startup honk-first -> escape wiring.

Per spec, on a fresh load we: detect realspace/SC -> HONK (it takes a few
seconds) -> look for a star -> escape (only if a star is in the way). The honk
must fire BEFORE the escape, but it can't run inside tick_status (it consumes
the live-loop journal stream). So the first startup tick arms a pending flag and
returns WITHOUT escaping; run_live fires the honk and sets `_startup_honk_done`;
a later tick then runs the get-off-star escape.
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


class _StuckReader:
    """Never flips to supercruise — the SC-entry wait always times out."""

    def __init__(self, status):
        self.current = status
        self._status = status

    def poll(self):
        return self._status


def _bright_frame():
    """A bright frame -> star_present True -> SC path (must maneuver clear)."""
    import numpy as np
    return np.full((10, 10, 3), 255, dtype=np.uint8)


def _dark_frame():
    """A dark frame -> no star -> unobstructed, skip supercruise."""
    import numpy as np
    return np.zeros((10, 10, 3), dtype=np.uint8)


def _orch(status_reader, sun_grab, *, tick_span=100000, step=1000):
    ticks = iter(range(0, tick_span, step))
    return Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=None,
        state=GameState(),
        config=Config(),
        clock=lambda: float(next(ticks, tick_span)),
        sleeper=lambda _t: None,
        status_reader=status_reader,
        sun_grab=sun_grab,
    )


def test_startup_first_tick_arms_honk_not_escape():
    """Honk-first: the very first startup tick arms the honk and runs NO escape.
    The escape-pending flag stays set for a later tick."""
    status = _flyable_realspace_status()
    orch = _orch(_EntersSupercruiseReader(status), _bright_frame)
    assert orch._startup_honk_pending is False
    orch.tick_status()
    # Honk armed; escape NOT run yet; still pending for the next tick.
    assert orch._startup_honk_pending is True
    assert orch._startup_honk_done is False
    assert orch._startup_escape_pending is True
    assert "Supercruise" not in orch.sender.actions()
    assert "SetSpeed100" not in orch.sender.actions()


def test_startup_escape_runs_after_honk_done():
    """Once the honk has fired (honk_done=True), a tick runs the realspace
    escape: a star is present, so it engages SC and flies clear (2x SetSpeed100),
    then clears the escape-pending flag."""
    status = _flyable_realspace_status()
    orch = _orch(_EntersSupercruiseReader(status), _bright_frame)
    orch._startup_honk_done = True  # honk already fired
    orch.tick_status()
    assert "Supercruise" in orch.sender.actions()
    # Star path throttles up TWICE (engage + fly-clear under SC).
    assert orch.sender.actions().count("SetSpeed100") == 2
    assert orch._startup_escape_pending is False  # success: not re-armed


def test_startup_escape_failure_keeps_pending_for_retry():
    """If SC entry never logs, the escape bails: keep escape-pending set so the
    next tick retries. (Honk already fired before the escape — unaffected.)"""
    status = _flyable_realspace_status()  # stays realspace -> SC entry never logs
    orch = _orch(_StuckReader(status), _bright_frame, tick_span=1000000)
    orch._startup_honk_done = True
    orch.tick_status()
    # Bailed: re-armed to retry next tick.
    assert orch._startup_escape_pending is True
    # Only the engage throttle fired; no fly-away throttle, no route target.
    assert orch.sender.actions().count("SetSpeed100") == 1
    assert "TargetNextRouteSystem" not in orch.sender.actions()


def test_startup_no_star_skips_supercruise():
    """No star ahead: the route is unobstructed, so the escape skips supercruise
    entirely — just targets + orients. No Supercruise, no throttle."""
    status = _flyable_realspace_status()
    orch = _orch(_EntersSupercruiseReader(status), _dark_frame)
    orch._startup_honk_done = True
    orch.tick_status()
    assert "Supercruise" not in orch.sender.actions()
    assert "SetSpeed100" not in orch.sender.actions()
    assert "TargetNextRouteSystem" in orch.sender.actions()
    assert orch._startup_escape_pending is False  # one-shot done


def test_run_live_fires_pending_startup_honk(tmp_path: Path):
    """With the flag armed, the live loop presses the honk key once and marks
    the startup honk done (so a later tick may run the escape)."""

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
    # And the flag was consumed (one-shot) + the done-marker set.
    assert orch._startup_honk_pending is False
    assert orch._startup_honk_done is True
