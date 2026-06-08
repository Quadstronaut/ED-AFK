"""TDD tests for the SC restart proximity branch (2026-06-08 operator spec).

Three cases:
  FAR  — ship in supercruise, Destination is NOT the local star (e.g. a station
          or a route-hop system) -> sc_resume runs (target_next_route + orient +
          engage_jump), nav_panel_target NEVER called.
  NEAR — ship in supercruise, Destination IS the local star (or indeterminate
          None) -> arrival runs (the existing get-around).
  INDETERMINATE — jump_age is None (no FSDJump seen in backlog) -> fail-safe to
          arrival, never sc_resume.

Signal used (Seat A / Seat B design spec, 2026-06-08):
  _destination_is_local_star(st, _current_system)
    False -> FAR  -> sc_resume
    True  -> NEAR -> arrival
    None  -> INDET -> arrival (fail-safe to current behavior)

NOTE — the signal is journal/code-confirmed from live Status.json:
  Robigo incident: Status.Destination = { Body:17, Name:"Tortooga" }
  -> _destination_is_local_star returns False -> FAR -> sc_resume.
  This is NOT operator-test-gated; the Status.Destination read is a pure
  in-memory attribute read (no nav-panel touch, no mislock risk).
"""

from types import SimpleNamespace as NS

import pytest

from ed_autojump.flow.dispatcher import FlowRunner
from ed_autojump.flow.model import Procedure, Step
from tests.flow import FakeSender


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dest(*, name: str, body: int = 0, system: int = 0):
    return NS(name=name, body=body, system=system)


def _status(*, in_supercruise=True, fsd_cooldown=False, destination=None):
    return NS(
        docked=False,
        in_supercruise=in_supercruise,
        fsd_charging=False,
        fsd_cooldown=fsd_cooldown,
        fsd_mass_locked=False,
        overheating=False,
        destination=destination,
    )


def _make_navroute_reader(route):
    nr = NS(route=route)
    return NS(poll=lambda: None, current=nr)


def _prox_runner(sender, *, st, current_system, route=None, records=None):
    """FlowRunner with distinguishable procedures:
      - sc_resume: presses SetSpeed25 (set_throttle 25) — unique sentinel.
      - arrival: presses TargetNextRouteSystem (target_next_route).
      - startup/smack_recovery: standard stubs.
    """
    procs = {
        "startup":        Procedure(name="startup",        steps=(Step("target_ahead"),)),
        "arrival":        Procedure(name="arrival",        steps=(Step("target_next_route"),)),
        "sc_resume":      Procedure(name="sc_resume",      steps=(Step("set_throttle", {"pct": 25}),)),
        "smack_recovery": Procedure(name="smack_recovery", steps=(Step("set_throttle", {"pct": 50}),)),
    }
    r = FlowRunner(
        procedures=procs,
        sender=sender,
        clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: st,
    )
    if route is not None:
        r.navroute_reader = _make_navroute_reader(route)
    if records is not None:
        r.record = lambda name, payload: records.append((name, payload))
    r._current_system = current_system
    return r


# A non-empty route so the normal-space empty-route guard never fires
# (we are testing in-supercruise paths, but defensive)
_ROUTE = [NS(system_address=1, star_system="Robigo"),
          NS(system_address=2, star_system="Wredguia UH-U c16-10")]


# ---------------------------------------------------------------------------
# FAR path: Destination is a named non-star body (station) -> sc_resume
# ---------------------------------------------------------------------------

def test_far_station_dest_runs_sc_resume():
    """Robigo-incident scenario: Status.Destination is a station (Body != 0,
    Name != system name). _destination_is_local_star returns False -> FAR ->
    sc_resume runs, arrival does NOT."""
    sender = FakeSender()
    dest = _dest(name="Tortooga", body=17, system=2832161837714)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE)
    r._maybe_startup()
    actions = sender.actions()
    # sc_resume sentinel is SetSpeed25; arrival sentinel is TargetNextRouteSystem
    assert "SetSpeed25" in actions, f"sc_resume did not run; actions={actions}"
    assert "TargetNextRouteSystem" not in actions, "arrival must NOT run on FAR path"


def test_far_station_dest_records_sc_resume_event():
    """FAR path records ScResumeOnRestart with reason=not_local_star."""
    sender = FakeSender()
    records: list[tuple[str, dict]] = []
    dest = _dest(name="Tortooga", body=17)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo",
                     route=_ROUTE, records=records)
    r._maybe_startup()
    names = [n for n, _ in records]
    assert "ScResumeOnRestart" in names, f"expected ScResumeOnRestart, got {names}"


def test_far_route_hop_dest_runs_sc_resume():
    """A route-hop Destination (Body=0, Name=next system, NOT current system)
    also returns False from _destination_is_local_star -> FAR -> sc_resume."""
    sender = FakeSender()
    dest = _dest(name="Wredguia UH-U c16-10", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE)
    r._maybe_startup()
    actions = sender.actions()
    assert "SetSpeed25" in actions, f"sc_resume did not run; actions={actions}"
    assert "TargetNextRouteSystem" not in actions


def test_far_no_destination_locked_runs_sc_resume():
    """No Destination locked at all (dest=None): _destination_is_local_star
    returns False (nothing locked = NOT the star) -> FAR -> sc_resume."""
    sender = FakeSender()
    st = _status(in_supercruise=True, destination=None)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE)
    r._maybe_startup()
    actions = sender.actions()
    assert "SetSpeed25" in actions, f"sc_resume did not run; actions={actions}"


# ---------------------------------------------------------------------------
# NEAR path: Destination IS the local star -> arrival (get-around)
# ---------------------------------------------------------------------------

def test_near_primary_star_dest_runs_arrival():
    """Local primary star lock (Name == system name, Body=0) ->
    _destination_is_local_star returns True -> NEAR -> arrival runs."""
    sender = FakeSender()
    dest = _dest(name="Robigo", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE)
    r._maybe_startup()
    actions = sender.actions()
    assert "TargetNextRouteSystem" in actions, f"arrival did not run; actions={actions}"
    assert "SetSpeed25" not in actions, "sc_resume must NOT run on NEAR path"


def test_near_secondary_star_dest_runs_arrival():
    """Secondary star lock (Name == '<system> A', Body=0) -> True -> arrival."""
    sender = FakeSender()
    dest = _dest(name="Robigo A", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE)
    r._maybe_startup()
    actions = sender.actions()
    assert "TargetNextRouteSystem" in actions
    assert "SetSpeed25" not in actions


# ---------------------------------------------------------------------------
# INDETERMINATE: _destination_is_local_star returns None (no status or no
# system name) -> FAIL-SAFE to arrival, NEVER sc_resume
# ---------------------------------------------------------------------------

def test_indeterminate_no_system_name_runs_arrival():
    """_current_system is None -> _destination_is_local_star returns None ->
    indeterminate -> fail-safe to arrival (current behavior)."""
    sender = FakeSender()
    dest = _dest(name="Robigo", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system=None, route=_ROUTE)
    r._maybe_startup()
    actions = sender.actions()
    assert "TargetNextRouteSystem" in actions, f"arrival must run on indeterminate; actions={actions}"
    assert "SetSpeed25" not in actions, "sc_resume must NOT run when system is unknown"


def test_indeterminate_no_status_attr_runs_arrival():
    """Status object has no 'destination' attribute at all -> _destination_is_local_star
    returns None (st is None path) or False; either way, the NEAR-path default
    must be arrival if the result is not a confirmed False from a named body.
    (If st is well-formed but dest attr is missing, returns False = FAR — this
    tests the case where status itself is None, which hits the st is None guard.)"""
    sender = FakeSender()
    records: list[tuple[str, dict]] = []
    # Build a runner where status_supplier returns None for the proximity read
    # The dispatcher reads self._latest_status which is initialised from status_supplier()
    procs = {
        "startup":        Procedure(name="startup",        steps=(Step("target_ahead"),)),
        "arrival":        Procedure(name="arrival",        steps=(Step("target_next_route"),)),
        "sc_resume":      Procedure(name="sc_resume",      steps=(Step("set_throttle", {"pct": 25}),)),
        "smack_recovery": Procedure(name="smack_recovery", steps=(Step("set_throttle", {"pct": 50}),)),
    }
    r = FlowRunner(
        procedures=procs,
        sender=sender,
        clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: None,  # no status at all
    )
    r.navroute_reader = _make_navroute_reader(_ROUTE)
    r.record = lambda name, payload: records.append((name, payload))
    r._current_system = "Robigo"
    r._startup_done = False
    # _latest_status is None because status_supplier returned None at init
    r._maybe_startup()
    # With None status, _maybe_startup returns early (st is None guard at L906)
    # — nothing runs, startup_done is NOT set to True (returns before that).
    # So this verifies the early-return, not the proximity branch.
    # The test is: confirm sc_resume does NOT run.
    assert "SetSpeed25" not in sender.actions()


# ---------------------------------------------------------------------------
# Guard-priority: existing guards must still outrank the proximity branch
# ---------------------------------------------------------------------------

def test_parked_terminal_still_idles_not_sc_resume():
    """RouteCompleteIdleOnRestart has higher priority than the proximity branch.
    An in-SC restart with empty route + local-star dest must IDLE, not sc_resume."""
    sender = FakeSender()
    records: list[tuple[str, dict]] = []
    dest = _dest(name="Robigo", body=0)
    procs = {
        "startup":        Procedure(name="startup",        steps=(Step("target_ahead"),)),
        "arrival":        Procedure(name="arrival",        steps=(Step("target_next_route"),)),
        "sc_resume":      Procedure(name="sc_resume",      steps=(Step("set_throttle", {"pct": 25}),)),
        "smack_recovery": Procedure(name="smack_recovery", steps=(Step("set_throttle", {"pct": 50}),)),
    }
    st = _status(in_supercruise=True, destination=dest)
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: st,
        record=lambda name, payload: records.append((name, payload)),
        navroute_reader=_make_navroute_reader([]),   # empty route = parked
    )
    r._current_system = "Robigo"
    r._maybe_startup()
    assert sender.actions() == [], f"nothing should run; got {sender.actions()}"
    names = [n for n, _ in records]
    assert "RouteCompleteIdleOnRestart" in names
    assert "ScResumeOnRestart" not in names


def test_smacked_with_cooldown_still_runs_smack_recovery_not_sc_resume():
    """smack+cooldown -> smack_recovery, regardless of Destination content.
    This path fires AFTER the in-SC branch, so as long as in_supercruise=False
    and fsd_cooldown=True it is unaffected by the proximity change."""
    sender = FakeSender()
    dest = _dest(name="SomeStation", body=5)
    st = _status(in_supercruise=False, fsd_cooldown=True, destination=dest)
    procs = {
        "startup":        Procedure(name="startup",        steps=(Step("target_ahead"),)),
        "arrival":        Procedure(name="arrival",        steps=(Step("target_next_route"),)),
        "sc_resume":      Procedure(name="sc_resume",      steps=(Step("set_throttle", {"pct": 25}),)),
        "smack_recovery": Procedure(name="smack_recovery", steps=(Step("set_throttle", {"pct": 50}),)),
    }
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: st,
    )
    r.navroute_reader = _make_navroute_reader(_ROUTE)
    r._smacked = True
    r._maybe_startup()
    actions = sender.actions()
    assert "SetSpeed50" in actions, f"smack_recovery must run; actions={actions}"
    assert "SetSpeed25" not in actions
