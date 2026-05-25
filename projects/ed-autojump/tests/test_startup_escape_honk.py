"""Startup honk + escape + engage-gate wiring.

The honk is just a key hold (ExplorationFSSDiscoveryScan ~6 s) — independent of
the ship's motion and the FSD, so it's fired directly, no journal stream, no
deferral. The critical safety property: the engage gate (_maybe_engage_next_jump)
must NOT fire the hyperspace jump while the startup get-off-star escape is still
pending — otherwise the ship hyperspaces straight into the arrival star.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ed_autojump.config import Config
from ed_autojump.journal.events import Event, FSDTarget
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
    """Never flips to supercruise — the SC-entry wait always times out (bail)."""

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


def _a_target() -> FSDTarget:
    """A safe next-hop target so the engage gate WOULD fire if not blocked."""
    return FSDTarget(
        timestamp="2026-05-25T00:00:00Z", event="FSDTarget",
        Name="Col 285 Sector ABC", SystemAddress=123, StarClass="M",
    )


def _orch(status_reader, sun_grab, *, tick_span=1000000, step=1000):
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


def test_startup_honks_then_escapes():
    """A star is present: one startup tick honks (key hold) AND runs the escape,
    which engages SC and flies clear (2x SetSpeed100), clearing the pending flag.
    The honk key is pressed exactly once."""
    status = _flyable_realspace_status()
    orch = _orch(_EntersSupercruiseReader(status), _bright_frame)
    orch.tick_status()
    acts = orch.sender.actions()
    assert acts.count("ExplorationFSSDiscoveryScan") == 1  # honked, once
    assert "Supercruise" in acts                            # escaped via SC
    assert acts.count("SetSpeed100") == 2                   # engage + fly-clear
    assert orch._startup_honked is True
    assert orch._startup_escape_pending is False            # success: gate released


def test_engage_gate_blocked_while_escape_pending_no_jump_into_star():
    """The regression: SC entry never logs, so the escape bails and the pending
    flag stays set. Even with a valid next-hop target, the engage gate must NOT
    fire the hyperspace jump — that's what flew the ship into the star before.
    The honk still fires (it's independent)."""
    status = _flyable_realspace_status()  # stays realspace -> SC never logs
    orch = _orch(_StuckReader(status), _bright_frame)
    orch.state.next_target = _a_target()  # a jump WOULD be available
    orch.tick_status()
    assert orch._startup_escape_pending is True             # bailed -> still pending
    assert "ExplorationFSSDiscoveryScan" in orch.sender.actions()  # honked anyway
    # The gate held: no hyperspace engage, no jump.
    assert "HyperSuperCombination" not in orch.sender.actions()


def test_startup_no_star_skips_supercruise():
    """No star ahead: the route is unobstructed, so the escape skips supercruise
    entirely — just targets + orients. Honk still fires; pending clears."""
    status = _flyable_realspace_status()
    orch = _orch(_EntersSupercruiseReader(status), _dark_frame)
    orch.tick_status()
    acts = orch.sender.actions()
    assert "ExplorationFSSDiscoveryScan" in acts
    assert "Supercruise" not in acts
    assert "SetSpeed100" not in acts
    assert "TargetNextRouteSystem" in acts
    assert orch._startup_escape_pending is False  # one-shot done, gate released


def test_engage_proceeds_once_escape_clears_the_flag():
    """Sanity: with the escape done (pending cleared) and a target + clear flags,
    the engage gate fires the jump. Proves the gate only blocks while pending."""
    status = _flyable_realspace_status()
    orch = _orch(_EntersSupercruiseReader(status), _dark_frame)  # no-star -> quick clear
    orch.state.next_target = _a_target()
    orch.tick_status()
    assert orch._startup_escape_pending is False
    # Engage gate ran in the same tick after the escape cleared the flag.
    assert "HyperSuperCombination" in orch.sender.actions()


def _sc_status():
    s = _flyable_realspace_status()
    s.in_supercruise = True
    return s


def test_startup_in_supercruise_orients_without_accelerating():
    """Already in supercruise at startup: honk + orient ONLY. No pitch, no
    SetSpeed100 fly-clear — that accelerate is what ran the ship straight at the
    star. In SC you leave by pointing at the next system and jumping; the engage
    gate does that. (No next_target here, so the gate stays idle.)"""
    status = _sc_status()
    orch = _orch(_StuckReader(status), _bright_frame)  # bright = star dead ahead
    orch.tick_status()
    acts = orch.sender.actions()
    assert "ExplorationFSSDiscoveryScan" in acts  # honked
    assert "SetSpeed100" not in acts              # did NOT accelerate
    assert "Supercruise" not in acts              # already in SC, no re-engage
    assert "PitchUpButton" not in acts            # no pitch into/around the star
    assert orch._startup_escape_pending is False  # one-shot done


def test_honk_fires_once_across_escape_retries():
    """If the escape bails and retries on a later tick, the honk does NOT fire
    again — it's a one-shot up front."""
    status = _flyable_realspace_status()
    orch = _orch(_StuckReader(status), _bright_frame)
    orch.tick_status()  # bail #1 (honk fires)
    orch.tick_status()  # bail #2 (retry, no second honk)
    assert orch.sender.actions().count("ExplorationFSSDiscoveryScan") == 1
    assert orch._startup_escape_pending is True
