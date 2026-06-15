"""2026-06-11 dock-lane improvements — the 2026-06-09 parked work, wired.

Covers:
  - dock_blind_maneuver: ship-size pitch (ship_sizes latch finally consumed),
    7s burn, SC-scene refusal, override knob
  - auto_launch CV seek-and-confirm (undock safety gate) on the REAL menu
    fixtures; blind legacy path untouched when the grabber is unwired
  - station_services_macro: operator 2s materialize settle + menu-up re-read
  - FlowRunner plumbing: station_menu_grabber + ship_supplier into StepContext
  - typed Location journal event + the respawn world-state repair (GAP#1)
  - dock.toml shape: blind maneuver + orient before SC-assist; operator
    services macro replaces the council verify-each step
"""

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from ed_core.flow.context import StepContext
from ed_autojump.flow.dispatcher import FlowRunner
from ed_autojump.flow.steps import INPUT_EXCLUSIVE_ACTIONS, STEP_REGISTRY
from ed_core.journal.events import Location, parse_event
from tests.flow import FakeSender

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _frame(name: str) -> np.ndarray:
    img = cv2.imread(str(FIXTURES / name))
    assert img is not None, f"fixture {name} missing"
    return img


_SERVICES_F = "station_menu_starport_services_live.png"
_AUTOLAUNCH_F = "station_menu_autolaunch_live.png"
_DISEMBARK_F = "station_menu_disembark_live.png"


def _seq_grabber(*names):
    """Grabber returning each named fixture in turn (last one repeats) — fakes
    the cursor moving between reads."""
    frames = [_frame(n) for n in names]

    def grab():
        return frames.pop(0) if len(frames) > 1 else frames[0]
    return grab


def _sc_status(*, in_supercruise=True, docked=False):
    return SimpleNamespace(in_supercruise=in_supercruise, docked=docked,
                           fsd_charging=False, fsd_cooldown=False,
                           fsd_mass_locked=False, overheating=False)


# ===================== dock_blind_maneuver =====================

def test_blind_maneuver_medium_ship_pitch_4s_then_burn():
    sender = FakeSender()
    sleeps = []
    ctx = StepContext(sender=sender, sleeper=sleeps.append, clock=lambda: 0.0,
                      status_supplier=lambda: _sc_status(),
                      ship_supplier=lambda: "mandalay")
    assert STEP_REGISTRY["dock_blind_maneuver"](ctx) is True
    assert sender.actions() == ["PitchDownButton", "SetSpeed100"]
    assert ("PitchDownButton", 4.0) in sender.holds      # MEDIUM -> 4s
    assert 7.0 in sleeps                                  # the operator burn leg


def test_blind_maneuver_large_ship_pitch_7s():
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _sc_status(),
                      ship_supplier=lambda: "type9")
    assert STEP_REGISTRY["dock_blind_maneuver"](ctx) is True
    assert ("PitchDownButton", 7.0) in sender.holds


def test_blind_maneuver_unknown_ship_defaults_medium_and_logs():
    sender = FakeSender()
    logs = []
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _sc_status(),
                      ship_supplier=lambda: "weird_new_hull",
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["dock_blind_maneuver"](ctx) is True
    assert ("PitchDownButton", 4.0) in sender.holds
    assert any(n == "ShipSizeUnknown" for n, _ in logs)   # table miss is loud


def test_blind_maneuver_override_knob_beats_table():
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _sc_status(),
                      ship_supplier=lambda: "type9")
    assert STEP_REGISTRY["dock_blind_maneuver"](ctx, pitch_override_s=2.5) is True
    assert ("PitchDownButton", 2.5) in sender.holds


def test_blind_maneuver_refuses_outside_supercruise():
    sender = FakeSender()
    logs = []
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _sc_status(in_supercruise=False),
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["dock_blind_maneuver"](ctx) is False
    assert sender.actions() == []                         # fail closed, no keys
    assert any(n == "DockBlindManeuverRefused" for n, _ in logs)


def test_dock_approach_refuses_supercruise_scene():
    """The retry lane can re-enter dock_approach while the ship is still in SC
    (a required fail BEFORE dock_sc_assist). This is a NORMAL-SPACE closing
    leg — it must refuse the SC scene with zero keypresses, not burn 25% at
    nothing until the watchdog (2026-06-11 adversarial-review fix)."""
    sender = FakeSender()
    logs = []
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _sc_status(in_supercruise=True),
                      event_waiter=lambda n, t: False,
                      no_fire_zone_supplier=lambda: True,   # even "in range" lies
                      clear_no_fire_zone=lambda: None,
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["dock_approach"](ctx) is False
    assert sender.actions() == []
    assert any(n == "DockApproachRefused" for n, _ in logs)


def test_blind_maneuver_registered_and_exclusive():
    assert "dock_blind_maneuver" in STEP_REGISTRY
    assert "dock_blind_maneuver" in INPUT_EXCLUSIVE_ACTIONS
    assert "station_services_macro" in INPUT_EXCLUSIVE_ACTIONS  # 06-09 must-fix
    assert "confirm_menu_item" not in INPUT_EXCLUSIVE_ACTIONS   # reads only


# ===================== auto_launch CV seek-and-confirm =====================

def _launch_ctx(sender, grabber, logs=None):
    return StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                       status_supplier=lambda: _sc_status(in_supercruise=False,
                                                          docked=True),
                       event_waiter=lambda n, t: n == "Undocked",
                       station_menu_grabber=grabber,
                       record=(lambda n, p: logs.append((n, p))) if logs is not None
                       else None)


def test_auto_launch_cv_already_on_autolaunch_selects_directly():
    sender = FakeSender()
    logs = []
    ctx = _launch_ctx(sender, _seq_grabber(_AUTOLAUNCH_F), logs)
    assert STEP_REGISTRY["auto_launch"](ctx) is True
    assert sender.actions() == ["UI_Select"]              # no blind S,S
    assert any(n == "AutoLaunchConfirmed" for n, _ in logs)


def test_auto_launch_cv_seeks_down_from_services():
    sender = FakeSender()
    ctx = _launch_ctx(sender, _seq_grabber(_SERVICES_F, _AUTOLAUNCH_F))
    assert STEP_REGISTRY["auto_launch"](ctx) is True
    assert sender.actions() == ["UI_Down", "UI_Select"]


def test_auto_launch_cv_seeks_up_from_disembark():
    sender = FakeSender()
    ctx = _launch_ctx(sender, _seq_grabber(_DISEMBARK_F, _AUTOLAUNCH_F))
    assert STEP_REGISTRY["auto_launch"](ctx) is True
    assert sender.actions() == ["UI_Up", "UI_Select"]


def test_auto_launch_cv_menu_not_up_fails_closed():
    sender = FakeSender()
    logs = []
    dark = np.zeros((1080, 1920, 3), dtype=np.uint8)
    ctx = _launch_ctx(sender, lambda: dark, logs)
    assert STEP_REGISTRY["auto_launch"](ctx) is False
    assert sender.actions() == []                         # select NEVER pressed
    assert any(n == "AutoLaunchRefused" and p["reason"] == "menu_not_confirmed"
               for n, p in logs)


def test_auto_launch_cv_seek_exhausted_never_selects():
    sender = FakeSender()
    logs = []
    # Cursor never reaches AUTO LAUNCH (detector keeps reading SERVICES).
    ctx = _launch_ctx(sender, _seq_grabber(_SERVICES_F), logs)
    assert STEP_REGISTRY["auto_launch"](ctx) is False
    assert "UI_Select" not in sender.actions()
    assert any(n == "AutoLaunchRefused" and p["reason"] == "seek_exhausted"
               for n, p in logs)


def test_auto_launch_unwired_grabber_keeps_blind_legacy_macro():
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _sc_status(in_supercruise=False,
                                                         docked=True),
                      event_waiter=lambda n, t: n == "Undocked")
    assert STEP_REGISTRY["auto_launch"](ctx) is True
    assert sender.actions() == ["UI_Down", "UI_Down", "UI_Select"]


# ===================== station_services_macro settle =====================

def test_services_macro_settles_2s_then_fires_blind_sequence():
    sender = FakeSender()
    sleeps = []
    ctx = StepContext(sender=sender, sleeper=sleeps.append, clock=lambda: 0.0,
                      status_supplier=lambda: _sc_status(in_supercruise=False,
                                                         docked=True),
                      station_menu_grabber=_seq_grabber(_SERVICES_F))
    assert STEP_REGISTRY["station_services_macro"](ctx) is True
    assert sleeps[0] == 2.0                # operator's materialize wait FIRST
    # W, SPACE, D, SPACE, D, SPACE, S -> action names.
    assert sender.actions() == ["UI_Up", "UI_Select", "UI_Right", "UI_Select",
                                "UI_Right", "UI_Select", "UI_Down"]


def test_services_macro_menu_never_up_refuses_after_rereads():
    sender = FakeSender()
    logs = []
    dark = np.zeros((1080, 1920, 3), dtype=np.uint8)
    reads = []
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _sc_status(in_supercruise=False,
                                                         docked=True),
                      station_menu_grabber=lambda: (reads.append(1), dark)[1],
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["station_services_macro"](ctx) is False
    assert sender.actions() == []
    assert len(reads) == 3                 # bounded re-read, then refuse
    assert any(n == "StationServicesMacroRefused" for n, _ in logs)


def test_services_macro_slow_menu_caught_by_reread():
    sender = FakeSender()
    dark = np.zeros((1080, 1920, 3), dtype=np.uint8)
    frames = [dark, _frame(_SERVICES_F)]
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _sc_status(in_supercruise=False,
                                                         docked=True),
                      station_menu_grabber=lambda: frames.pop(0) if len(frames) > 1
                      else frames[0])
    assert STEP_REGISTRY["station_services_macro"](ctx) is True
    assert len(sender.actions()) == 7      # full sequence fired


# ===================== FlowRunner plumbing =====================

def _minimal_runner(**kw):
    return FlowRunner(procedures={}, sender=FakeSender(), clock=lambda: 0.0,
                      sleeper=lambda s: None, status_supplier=lambda: None, **kw)


def test_make_context_wires_station_menu_grabber_and_ship_supplier():
    sentinel = lambda: "frame"  # noqa: E731
    r = _minimal_runner(station_menu_grabber=sentinel)
    r._current_ship = "type9"
    ctx = r._make_context()
    assert ctx.station_menu_grabber is sentinel
    assert ctx.ship_supplier() == "type9"
    r._current_ship = "mandalay"           # supplier is live, not a snapshot
    assert ctx.ship_supplier() == "mandalay"


def test_make_context_defaults_unwired():
    ctx = _minimal_runner()._make_context()
    assert ctx.station_menu_grabber is None
    assert ctx.ship_supplier() is None


# ===================== typed Location + respawn repair =====================

# Field shape from a REAL journal line (Operator's journal, 2026-06-11 05:50Z),
# trimmed to the fields the model declares + Docked/StationName for the
# respawn variant.
_REAL_LOCATION = {
    "timestamp": "2026-06-11T05:50:22Z", "event": "Location",
    "Docked": False, "StarSystem": "Cephei Sector EG-Y c13",
    "SystemAddress": 3656996885194, "Body": "Cephei Sector EG-Y c13",
    "BodyID": 0, "BodyType": "Star",
}


def test_location_parses_typed_from_real_shape():
    ev = parse_event(dict(_REAL_LOCATION))
    assert isinstance(ev, Location)
    assert ev.star_system == "Cephei Sector EG-Y c13"     # snake_case WORKS now
    assert ev.docked is False


def test_location_updates_current_system_through_parse_event():
    """The latent bug: generic-Event extras keep alias keys, so the dispatcher
    branch read None forever. End-to-end through parse_event must work now."""
    r = _minimal_runner()
    r._on_tail_event(parse_event(dict(_REAL_LOCATION)))
    assert r._current_system == "Cephei Sector EG-Y c13"


def test_location_docked_respawn_repairs_world_state():
    """GAP#1 (the Tortooga incident): Location(Docked=true) must set _docked,
    capture the station, and clear a stale _smacked latch."""
    r = _minimal_runner()
    r._smacked = True                       # pre-death star drop, stale
    line = dict(_REAL_LOCATION, Docked=True, StationName="Tortooga",
                StationType="Outpost", BodyType="Station")
    r._on_tail_event(parse_event(line))
    assert r._docked is True
    assert r._docked_station == "Tortooga"
    assert r._smacked is False


def test_location_undocked_does_not_touch_docked_or_smack():
    r = _minimal_runner()
    r._smacked = True
    r._docked = False
    r._on_tail_event(parse_event(dict(_REAL_LOCATION)))   # Docked=False
    assert r._docked is False
    assert r._smacked is True               # says nothing about the smack scene


# ===================== dock.toml shape =====================

def test_dock_toml_blind_maneuver_then_orient_before_sc_assist():
    from ed_core.flow.loader import load_procedures
    proc_dir = Path(__file__).resolve().parents[2] / "procedures"
    dock = load_procedures(proc_dir)["dock"]
    actions = [s.action for s in dock.steps]
    assert actions.index("dock_target_station") \
        < actions.index("dock_blind_maneuver") \
        < actions.index("orient_compass") \
        < actions.index("dock_sc_assist")
    # Operator pit-stop macro replaced the council verify-each step.
    assert "station_services_macro" in actions
    assert "station_services" not in actions
    required = {s.action for s in dock.steps if s.required}
    assert {"dock_blind_maneuver", "orient_compass"} <= required
    assert "station_services_macro" not in required       # best-effort
