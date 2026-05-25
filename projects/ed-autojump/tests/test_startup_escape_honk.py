"""Startup honk + pitch-FIRST escape + engage-gate wiring.

The maneuver: HONK, then PITCH the star off-screen FIRST (always), THEN engage
supercruise — and the engage is SKIPPED if we're already in SC. Pitch-first
means the star is dodged regardless of the SC-vs-realspace branch. The engage
gate must NOT fire the jump until the escape clears `_startup_escape_pending`.

Clock note: a CONSTANT clock is used so the pitch loop (sun_avoid) is driven by
the frame content, not a timeout. Frames that go dark "clear" the pitch; frames
that stay bright never clear -> the escape bails (and must NOT throttle).
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


def _realspace_status():
    return SimpleNamespace(
        docked=False, in_supercruise=False, overheating=False, is_in_danger=False,
        heat=0.2, fsd_charging=False, fsd_cooldown=False, fsd_mass_locked=False,
    )


def _sc_status():
    s = _realspace_status()
    s.in_supercruise = True
    return s


class _EntersSupercruiseReader:
    """Realspace at the first poll (tick_status's), then flips in_supercruise=True
    on later polls so the escape's SC-entry wait succeeds."""

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
    def __init__(self, status):
        self.current = status
        self._status = status

    def poll(self):
        return self._status


def _bright_frame():
    """Always bright -> star never clears -> the pitch bails (must not throttle)."""
    import numpy as np
    return np.full((10, 10, 3), 255, dtype=np.uint8)


def _dark_frame():
    """Dark -> no star -> unobstructed (no pitch, no throttle)."""
    import numpy as np
    return np.zeros((10, 10, 3), dtype=np.uint8)


def _clearing_sun_grab():
    """A sun grab that reads bright for the first several calls — enough to cover
    the probe's sampling (sun_detect_samples grabs) AND the escape's CHECK
    sampling AND a couple of sun_avoid pitches — then dark, so the pitch CLEARS.
    With Config().escape.sun_detect_samples=3 that's 3 (probe) + 3 (check) = 6
    grabs before sun_avoid starts, so stay bright through ~8."""
    import numpy as np
    bright = np.full((10, 10, 3), 255, dtype=np.uint8)
    dark = np.zeros((10, 10, 3), dtype=np.uint8)
    n = [0]

    def _grab():
        n[0] += 1
        return bright if n[0] <= 8 else dark

    return _grab


def _orch(status_reader, sun_grab):
    # CONSTANT clock: sun_avoid is frame-driven, not timeout-driven.
    # escape_mode='brightness': these tests cover the OPT-IN brightness startup
    # path (perform_realspace_escape). The DEFAULT is now 'compass' (covered by
    # test_compass_escape.py); brightness stays for users who choose it.
    cfg = Config()
    cfg.escape.escape_mode = "brightness"
    return Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=None,
        state=GameState(),
        config=cfg,
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        status_reader=status_reader,
        sun_grab=sun_grab,
    )


def _a_target() -> FSDTarget:
    return FSDTarget(
        timestamp="2026-05-25T00:00:00Z", event="FSDTarget",
        Name="Somewhere", SystemAddress=1, StarClass="M",
    )


def test_startup_honks():
    """The honk (a plain key hold) fires on the startup tick."""
    orch = _orch(_StuckReader(_realspace_status()), _dark_frame)
    orch.tick_status()
    assert "ExplorationFSSDiscoveryScan" in orch.sender.actions()


def test_engage_gate_blocked_when_pitch_cannot_clear():
    """THE REGRESSION GUARD: star present but the pitch can't clear it -> the
    escape bails WITHOUT throttling, keeps pending set, and the engage gate must
    NOT fire the jump. (This is the 'flew into the star' path; it must not throttle
    or jump.)"""
    orch = _orch(_StuckReader(_realspace_status()), _bright_frame)  # never clears
    orch.state.next_target = _a_target()  # a jump WOULD be available if not gated
    orch.tick_status()
    acts = orch.sender.actions()
    assert "ExplorationFSSDiscoveryScan" in acts        # honked
    assert "PitchUpButton" in acts                       # tried to pitch
    assert "SetSpeed100" not in acts                     # NEVER threw throttle
    assert "HyperSuperCombination" not in acts           # gate held: no jump
    assert orch._startup_escape_pending is True          # re-armed to retry


def test_realspace_star_pitches_first_then_engages():
    """Realspace + star that clears: PITCH first, THEN engage SC (SetSpeed100 +
    Supercruise), then fly. Pitch precedes all throttle."""
    orch = _orch(_EntersSupercruiseReader(_realspace_status()), _clearing_sun_grab())
    orch.tick_status()
    acts = orch.sender.actions()
    assert "ExplorationFSSDiscoveryScan" in acts
    assert "Supercruise" in acts                          # engaged (was in realspace)
    assert acts.count("SetSpeed100") == 2                 # engage throttle + fly throttle
    assert acts.index("PitchUpButton") < acts.index("SetSpeed100")  # PITCH FIRST
    assert acts.index("PitchUpButton") < acts.index("Supercruise")
    assert orch._startup_escape_pending is False


def test_in_supercruise_star_pitches_first_no_engage():
    """Already in SC + star that clears: PITCH first, then NO engage (we're in SC),
    then ONE throttle to fly clear."""
    orch = _orch(_StuckReader(_sc_status()), _clearing_sun_grab())
    orch.tick_status()
    acts = orch.sender.actions()
    assert "ExplorationFSSDiscoveryScan" in acts
    assert "Supercruise" not in acts                      # already in SC -> no engage
    assert "PitchUpButton" in acts                        # pitched first
    assert acts.count("SetSpeed100") == 1                 # fly-clear only
    assert acts.index("PitchUpButton") < acts.index("SetSpeed100")
    assert orch._startup_escape_pending is False


def test_no_star_no_throttle():
    """No star -> unobstructed: honk + target + orient, but NO pitch and NO
    throttle (the blind-throttle-into-star path is gone)."""
    orch = _orch(_StuckReader(_sc_status()), _dark_frame)
    orch.tick_status()
    acts = orch.sender.actions()
    assert "ExplorationFSSDiscoveryScan" in acts
    assert "SetSpeed100" not in acts
    assert "Supercruise" not in acts
    assert "PitchUpButton" not in acts
    assert "TargetNextRouteSystem" in acts
    assert orch._startup_escape_pending is False


def test_honk_fires_once_across_escape_retries():
    """If the pitch can't clear and the escape bails+retries, the honk does NOT
    re-fire — it's a one-shot up front."""
    orch = _orch(_StuckReader(_realspace_status()), _bright_frame)
    orch.tick_status()  # retry #1 (honk fires)
    orch.tick_status()  # retry #2 (no second honk)
    assert orch.sender.actions().count("ExplorationFSSDiscoveryScan") == 1
    assert orch._startup_escape_pending is True
