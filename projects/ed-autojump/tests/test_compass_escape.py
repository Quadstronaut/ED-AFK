"""Compass-driven star escape — the DEFAULT maneuver.

The operator's spec, verbatim: "target the star, use the goddamn compass to get
it under us, fly the fuck away from it, target next jump, orient, jump." NO
brightness. These tests drive the real maneuver (perform_compass_escape) and the
orchestrator wiring, and assert the one thing that actually matters: the KEYPRESS
ORDER. Specifically:

  * SelectTarget (target the star) comes FIRST.
  * PitchUpButton (get the star under us) comes BEFORE any throttle.
  * Throttle / Supercruise / TargetNextRouteSystem / jump come AFTER the star is
    confirmed under us — never before.
  * On a missed target-lock or a star that won't go under, the maneuver BAILS
    WITHOUT THROTTLING (it can never drive into the star) and the engage gate
    stays closed.

A `_SimCompass` stands in for the nav-compass: it reads the RecordingSender's
own press history so it behaves like a real ship — each PitchUpButton drives the
star down until it is behind us; after TargetNextRouteSystem it reports the next
system centred so the align step converges.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ed_autojump.config import Config
from ed_autojump.executor.escape import perform_compass_escape
from ed_autojump.journal.events import FSDJump, FSDTarget
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.state import GameState
from ed_autojump.vision.compass import CompassRead


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


class _SimCompass:
    """Compass reader driven by the sender's press history (a fake ship).

    Phase STAR (before TargetNextRouteSystem is pressed) — the target is the
    arrival star: each PitchUpButton press drives the star toward the bottom of
    the compass; after ``pitches_to_under`` presses the star is BEHIND us
    (hollow, in_front=False) => "under us".

    Phase NEXT (after TargetNextRouteSystem) — the target is the next system,
    reported centred + in front so align_to_target converges with no presses.

    ``found=False`` makes every read miss — models a target-lock that did not
    take (the maneuver must bail before throttling)."""

    def __init__(self, sender: RecordingSender, *, pitches_to_under: int = 3,
                 found: bool = True):
        self._sender = sender
        self._pitches_to_under = pitches_to_under
        self._found = found

    def read(self, frame) -> CompassRead:  # frame ignored — we read key history
        acts = self._sender.actions()
        if "TargetNextRouteSystem" in acts:
            # Next-system phase: centred + in front -> align succeeds immediately.
            return CompassRead(found=True, offset_x=0.0, offset_y=0.0,
                               in_front=True, confidence=1.0)
        if not self._found:
            return CompassRead.not_found()
        if acts.count("PitchUpButton") >= self._pitches_to_under:
            # Star has gone hollow/behind — it is under us now.
            return CompassRead(found=True, offset_x=0.0, offset_y=-1.0,
                               in_front=False, confidence=1.0)
        # Still ahead, near centre — keep pitching.
        return CompassRead(found=True, offset_x=0.0, offset_y=0.0,
                           in_front=True, confidence=1.0)


def _const_clock():
    return 0.0


def _no_sleep(_t):
    return None


def _advancing_clock(step: float = 1.0):
    """A clock that advances by ``step`` each call. Needed wherever a real
    timeout must actually trip — e.g. wait_for_supercruise loops `while clock()
    < deadline`, which the constant clock (always 0.0) can never exit."""
    t = {"v": 0.0}

    def _c() -> float:
        t["v"] += step
        return t["v"]

    return _c


# ---------------------------------------------------------------------------
# perform_compass_escape — the maneuver in isolation
# ---------------------------------------------------------------------------

def test_in_sc_targets_then_pitches_then_throttles_then_targets_next():
    """Already in SC: target star -> pitch under -> fly (one throttle) -> target
    next -> orient. Pitch precedes throttle; no SC engage (already in SC)."""
    sender = RecordingSender(_binds())
    compass = _SimCompass(sender, pitches_to_under=3)
    out = perform_compass_escape(
        sender,
        compass_reader=compass,
        compass_capture=lambda: None,
        already_in_supercruise=True,
        in_supercruise=lambda: True,
        clear_offset_y=0.6,
        max_iters=10,
        clock=_const_clock,
        sleeper=_no_sleep,
    )
    acts = sender.actions()
    assert out.targeted_star and out.star_seen and out.star_under
    assert out.engaged_sc is False                 # already in SC -> no engage
    assert out.sc_entered is True
    assert out.targeted_next is True
    assert out.aligned is True
    # ORDER: target -> pitch -> throttle -> target-next.
    assert acts.index("SelectTarget") < acts.index("PitchUpButton")
    assert acts.index("PitchUpButton") < acts.index("SetSpeed100")   # pitch BEFORE throttle
    assert acts.index("SetSpeed100") < acts.index("TargetNextRouteSystem")
    assert "Supercruise" not in acts               # in SC -> never re-engage
    assert acts.count("PitchUpButton") == 3        # pitched exactly until under


def test_realspace_engages_supercruise_after_pitch():
    """Realspace: after the star is under us, engage SC (throttle+Supercruise),
    confirm entry, then fly clear. Pitch still precedes every throttle."""
    sender = RecordingSender(_binds())
    compass = _SimCompass(sender, pitches_to_under=2)
    out = perform_compass_escape(
        sender,
        compass_reader=compass,
        compass_capture=lambda: None,
        already_in_supercruise=False,
        in_supercruise=lambda: True,   # SC entry confirmed by the log
        clear_offset_y=0.6,
        max_iters=10,
        clock=_const_clock,
        sleeper=_no_sleep,
    )
    acts = sender.actions()
    assert out.star_under is True
    assert out.engaged_sc is True and out.sc_entered is True
    assert "Supercruise" in acts
    # Pitch happened before the FIRST throttle (the SC-engage throttle).
    last_pitch = max(i for i, a in enumerate(acts) if a == "PitchUpButton")
    assert last_pitch < acts.index("SetSpeed100")
    assert acts.index("SelectTarget") < acts.index("Supercruise")


def test_bails_without_throttle_when_lock_misses():
    """Target-lock didn't take (compass shows no dot): BAIL before pitching, and
    NEVER throttle. star_seen False, no PitchUpButton, no SetSpeed100."""
    sender = RecordingSender(_binds())
    compass = _SimCompass(sender, found=False)
    out = perform_compass_escape(
        sender,
        compass_reader=compass,
        compass_capture=lambda: None,
        already_in_supercruise=True,
        in_supercruise=lambda: True,
        clock=_const_clock,
        sleeper=_no_sleep,
    )
    acts = sender.actions()
    assert out.targeted_star is True and out.star_seen is False
    assert out.star_under is None
    assert "SelectTarget" in acts
    assert "PitchUpButton" not in acts             # bailed before pitching
    assert "SetSpeed100" not in acts               # NEVER throttled
    assert "Supercruise" not in acts
    assert "TargetNextRouteSystem" not in acts


def test_bails_without_throttle_when_star_wont_go_under():
    """Star never goes under within budget: pitch up to the cap, then BAIL
    WITHOUT THROTTLING (the slam guard) and report star_under False."""
    sender = RecordingSender(_binds())
    compass = _SimCompass(sender, pitches_to_under=9999)  # never satisfied
    out = perform_compass_escape(
        sender,
        compass_reader=compass,
        compass_capture=lambda: None,
        already_in_supercruise=True,
        in_supercruise=lambda: True,
        clear_offset_y=0.6,
        max_iters=4,                 # small cap so the test is quick
        clock=_const_clock,
        sleeper=_no_sleep,
    )
    acts = sender.actions()
    assert out.star_seen is True and out.star_under is False
    assert acts.count("PitchUpButton") == 4        # pitched up to the cap
    assert "SetSpeed100" not in acts               # NEVER threw throttle
    assert "Supercruise" not in acts
    assert "TargetNextRouteSystem" not in acts


def test_bails_without_throttle_when_sc_entry_never_logs():
    """Realspace but supercruise entry never logs: bail after the engage, do not
    fly clear or target next."""
    sender = RecordingSender(_binds())
    compass = _SimCompass(sender, pitches_to_under=2)
    out = perform_compass_escape(
        sender,
        compass_reader=compass,
        compass_capture=lambda: None,
        already_in_supercruise=False,
        in_supercruise=lambda: False,   # never enters SC
        sc_entry_timeout_s=1.0,
        sc_entry_poll_s=0.5,
        max_iters=10,
        clock=_advancing_clock(),       # advancing so wait_for_supercruise can time out
        sleeper=_no_sleep,
    )
    assert out.star_under is True
    assert out.engaged_sc is True and out.sc_entered is False
    assert out.flew_clear is False and out.targeted_next is False


def test_noop_without_compass_wiring():
    """No compass reader/capture -> the maneuver is impossible: no action, and
    crucially no throttle."""
    sender = RecordingSender(_binds())
    out = perform_compass_escape(
        sender,
        compass_reader=None,
        compass_capture=None,
        clock=_const_clock,
        sleeper=_no_sleep,
    )
    assert out.targeted_star is False and out.star_seen is None
    assert sender.actions() == []


# ---------------------------------------------------------------------------
# Orchestrator wiring — startup + arrival, default compass mode
# ---------------------------------------------------------------------------

def _sc_status():
    return SimpleNamespace(
        docked=False, in_supercruise=True, overheating=False, is_in_danger=False,
        heat=0.2, fsd_charging=False, fsd_cooldown=False, fsd_mass_locked=False,
    )


def _realspace_status():
    s = _sc_status()
    s.in_supercruise = False
    return s


class _StuckReader:
    def __init__(self, status):
        self.current = status
        self._status = status

    def poll(self):
        return self._status


def _a_target() -> FSDTarget:
    return FSDTarget(
        timestamp="2026-05-25T00:00:00Z", event="FSDTarget",
        Name="Somewhere", SystemAddress=1, StarClass="M",
    )


def _compass_orch(status, compass, *, max_iters=10, honk=True):
    cfg = Config()
    cfg.escape.escape_mode = "compass"
    cfg.escape.compass_max_iters = max_iters
    cfg.vision.enabled = True          # compass wired => vision on (matches build_vision)
    cfg.vision.align_samples = 1       # deterministic SimCompass; no median needed
    cfg.exploration.honk = honk
    return Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=None,
        state=GameState(),
        config=cfg,
        clock=_const_clock,
        sleeper=_no_sleep,
        status_reader=status if isinstance(status, _StuckReader) else _StuckReader(status),
        compass_reader=compass,
        frame_grabber=lambda: None,
    )


def test_startup_compass_escape_then_engage_jumps():
    """Startup in SC: honk -> target star -> pitch under -> fly -> target next ->
    orient -> the engage gate fires the jump. Asserts the full safety order."""
    orch = _compass_orch(_sc_status(), None)
    orch.compass_reader = _SimCompass(orch.sender, pitches_to_under=3)
    orch.state.next_target = _a_target()
    orch.tick_status()
    acts = orch.sender.actions()
    assert "ExplorationFSSDiscoveryScan" in acts                     # honked
    assert "SelectTarget" in acts                                    # targeted the star
    assert acts.index("SelectTarget") < acts.index("PitchUpButton")  # target FIRST
    assert acts.index("PitchUpButton") < acts.index("SetSpeed100")   # pitch BEFORE throttle
    assert "Supercruise" not in acts                                 # already in SC
    assert "HyperSuperCombination" in acts                           # jump fired
    assert acts.index("PitchUpButton") < acts.index("HyperSuperCombination")
    assert orch._startup_escape_pending is False                     # gate cleared on success


def test_startup_compass_bail_keeps_gate_closed():
    """Startup, star won't go under: bail without throttle, the jump gate stays
    closed (pending True), and no HyperSuperCombination fires."""
    orch = _compass_orch(_sc_status(), None, max_iters=3)
    orch.compass_reader = _SimCompass(orch.sender, pitches_to_under=9999)
    orch.state.next_target = _a_target()
    orch.tick_status()
    acts = orch.sender.actions()
    assert "SelectTarget" in acts
    assert "PitchUpButton" in acts                # tried to pitch
    assert "SetSpeed100" not in acts              # NEVER throttled
    assert "HyperSuperCombination" not in acts    # no jump while stuck at the star
    assert orch._startup_escape_pending is True   # re-armed to retry


def test_arrival_compass_escape_not_refuel_macro():
    """On FSDJump arrival in compass mode: run the compass maneuver (SelectTarget
    + PitchUpButton, pitch before throttle), and do NOT run the deprecated refuel
    nav-panel macro (FocusLeftPanel / UI_Right)."""
    orch = _compass_orch(_sc_status(), None, honk=False)
    orch.compass_reader = _SimCompass(orch.sender, pitches_to_under=2)
    orch.state.status = _sc_status()   # in SC at arrival -> no SC re-engage
    ev = FSDJump(
        timestamp="2026-05-25T00:00:00Z", event="FSDJump",
        StarSystem="Somewhere", SystemAddress=1,
        StarPos=[1.0, 2.0, 3.0],
        FuelLevel=32.0, FuelUsed=1.0, JumpDist=10.0,
    )
    orch._on_fsd_jump(ev, follow_stream=None)
    acts = orch.sender.actions()
    assert "SelectTarget" in acts
    assert acts.index("SelectTarget") < acts.index("PitchUpButton")
    assert acts.index("PitchUpButton") < acts.index("SetSpeed100")
    # The deprecated refuel SC-Assist macro must NOT run.
    assert "FocusLeftPanel" not in acts
    assert "UI_Right" not in acts
