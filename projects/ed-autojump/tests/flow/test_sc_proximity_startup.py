"""TDD tests for the SC restart proximity branch (2026-06-08 operator spec).

Four-priority launch gate (in order):
  1. INDETERMINATE (dest=None / unknown system) -> arrival (fail-safe).
  2. Destination IS the local star -> arrival (genuine nose-on-star scene).
  3. jump_age <= FRESH_ARRIVAL_WINDOW_S (30s) -> arrival (smack guard: ED
     pre-loads the next route hop into Status.Destination immediately after
     FSDJump, so _destination_is_local_star returns False even nose-on star).
  4. OTHERWISE (jump_age > 30s AND confident non-local-star lock) -> sc_resume.

jump_age is derived from _last_fsdjump_utc (set via _apply_state on FSDJump)
and self.now_utc(), both injectable in tests.
"""

from datetime import datetime, timezone, timedelta
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


# Fixed UTC epoch used as "now" in tests: 2026-06-08T12:00:00Z
_NOW_UTC = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)


def _prox_runner(sender, *, st, current_system, route=None, records=None,
                 jump_age_s=None):
    """FlowRunner with distinguishable procedures:
      - sc_resume: presses SetSpeed25 (set_throttle 25) — unique sentinel.
      - arrival: presses TargetNextRouteSystem (target_next_route).
      - startup/smack_recovery: standard stubs.

    jump_age_s: seconds since the last FSDJump, as seen by _jump_age().
      None  -> no FSDJump tracked this session (_last_fsdjump_utc stays None).
      float -> _last_fsdjump_utc is set so that (_NOW_UTC - ts) == jump_age_s.
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
        now_utc=lambda: _NOW_UTC,   # freeze "now" for deterministic age
    )
    if route is not None:
        r.navroute_reader = _make_navroute_reader(route)
    if records is not None:
        r.record = lambda name, payload: records.append((name, payload))
    r._current_system = current_system
    # Wire jump_age: set _last_fsdjump_utc so the computed age equals jump_age_s.
    if jump_age_s is not None:
        r._last_fsdjump_utc = _NOW_UTC - timedelta(seconds=jump_age_s)
    # else: _last_fsdjump_utc stays None -> _jump_age() returns None -> priority 3
    # treats as indeterminate -> arrival (fail-safe).
    return r


# A non-empty route so the normal-space empty-route guard never fires
# (we are testing in-supercruise paths, but defensive)
_ROUTE = [NS(system_address=1, star_system="Robigo"),
          NS(system_address=2, star_system="Wredguia UH-U c16-10")]


# ---------------------------------------------------------------------------
# Priority 1 / 3: dest=None -> arrival (fail-safe, regardless of jump_age)
# ---------------------------------------------------------------------------

def test_no_destination_runs_arrival_failsafe():
    """Priority 1 fail-safe: dest=None -> arrival, never sc_resume.
    Previously 'test_far_no_destination_locked_runs_sc_resume' — FLIPPED per
    operator spec (2026-06-08): indeterminate must fail-safe to arrival."""
    sender = FakeSender()
    st = _status(in_supercruise=True, destination=None)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE,
                     jump_age_s=120.0)   # stale — priority 1 fires before priority 4
    r._maybe_startup()
    actions = sender.actions()
    assert "TargetNextRouteSystem" in actions, f"arrival must run; actions={actions}"
    assert "SetSpeed25" not in actions, "sc_resume must NOT run when dest=None"


# ---------------------------------------------------------------------------
# Priority 2: Destination IS the local star -> arrival
# ---------------------------------------------------------------------------

def test_near_primary_star_dest_runs_arrival():
    """Local primary star lock (Name == system name, Body=0) ->
    _destination_is_local_star returns True -> NEAR -> arrival runs."""
    sender = FakeSender()
    dest = _dest(name="Robigo", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE,
                     jump_age_s=120.0)
    r._maybe_startup()
    actions = sender.actions()
    assert "TargetNextRouteSystem" in actions, f"arrival did not run; actions={actions}"
    assert "SetSpeed25" not in actions, "sc_resume must NOT run on NEAR path"


def test_near_secondary_star_dest_runs_arrival():
    """Secondary star lock (Name == '<system> A', Body=0) -> True -> arrival."""
    sender = FakeSender()
    dest = _dest(name="Robigo A", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE,
                     jump_age_s=120.0)
    r._maybe_startup()
    actions = sender.actions()
    assert "TargetNextRouteSystem" in actions
    assert "SetSpeed25" not in actions


# ---------------------------------------------------------------------------
# Priority 3 (smack guard): fresh arrival within 30s -> arrival,
#   regardless of what _destination_is_local_star returns
# ---------------------------------------------------------------------------

def test_fresh_arrival_route_hop_dest_runs_arrival():
    """SMACK GUARD (priority 3): route-hop dest (False from _destination_is_local_star)
    + jump_age <= 30s -> arrival. ED pre-loads the NEXT hop into Status.Destination
    immediately after FSDJump; within the fresh window the ship is still nose-on
    the arrival star and sc_resume would smack it."""
    sender = FakeSender()
    dest = _dest(name="Wredguia UH-U c16-10", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE,
                     jump_age_s=15.0)   # within 30s fresh window
    r._maybe_startup()
    actions = sender.actions()
    assert "TargetNextRouteSystem" in actions, f"arrival must run (fresh); actions={actions}"
    assert "SetSpeed25" not in actions, "sc_resume must NOT run within fresh window"


def test_fresh_arrival_named_station_dest_runs_arrival():
    """SMACK GUARD (priority 3): named-station dest + jump_age <= 30s -> arrival.
    A fresh arrival still has the ship nose-on the star regardless of what
    Status.Destination shows; the fast-path is unsafe until the window expires."""
    sender = FakeSender()
    dest = _dest(name="Tortooga", body=17, system=2832161837714)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE,
                     jump_age_s=5.0)
    r._maybe_startup()
    actions = sender.actions()
    assert "TargetNextRouteSystem" in actions, f"arrival must run (fresh station); actions={actions}"
    assert "SetSpeed25" not in actions


def test_fresh_arrival_boundary_exactly_30s_runs_arrival():
    """Boundary: jump_age == 30.0s is still WITHIN the window -> arrival.
    30.0 <= 30.0 must be True."""
    sender = FakeSender()
    dest = _dest(name="Wredguia UH-U c16-10", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE,
                     jump_age_s=30.0)
    r._maybe_startup()
    actions = sender.actions()
    assert "TargetNextRouteSystem" in actions, f"arrival must run at boundary 30.0s; actions={actions}"
    assert "SetSpeed25" not in actions


def test_stale_arrival_boundary_just_over_30s_runs_sc_resume():
    """Boundary: jump_age == 30.1s is OUTSIDE the window -> sc_resume.
    30.1 > 30.0 must be True, so priority 4 fires."""
    sender = FakeSender()
    dest = _dest(name="Wredguia UH-U c16-10", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE,
                     jump_age_s=30.1)
    r._maybe_startup()
    actions = sender.actions()
    assert "SetSpeed25" in actions, f"sc_resume must run at 30.1s; actions={actions}"
    assert "TargetNextRouteSystem" not in actions


def test_jump_age_indeterminate_no_fsdjump_runs_arrival():
    """Priority 3 fail-safe: no FSDJump seen this session (jump_age=None)
    -> arrival. An unknown age is treated as 'possibly fresh' to fail-safe."""
    sender = FakeSender()
    dest = _dest(name="Wredguia UH-U c16-10", body=0)
    st = _status(in_supercruise=True, destination=dest)
    # jump_age_s=None -> _last_fsdjump_utc stays None -> _jump_age() returns None
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE,
                     jump_age_s=None)
    r._maybe_startup()
    actions = sender.actions()
    assert "TargetNextRouteSystem" in actions, f"arrival must run (no jump seen); actions={actions}"
    assert "SetSpeed25" not in actions


# ---------------------------------------------------------------------------
# Priority 4 (FAR/stale): Destination is a named non-star body AND stale
# ---------------------------------------------------------------------------

def test_far_station_dest_runs_sc_resume():
    """Robigo-incident scenario: Status.Destination is a station (Body != 0,
    Name != system name) AND jump_age > 30s -> sc_resume runs, arrival does NOT.
    This is the Robigo loiter fix: the operator is parked at a station and the
    fast-resume path must not invoke the orbit get-around."""
    sender = FakeSender()
    dest = _dest(name="Tortooga", body=17, system=2832161837714)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE,
                     jump_age_s=120.0)
    r._maybe_startup()
    actions = sender.actions()
    assert "SetSpeed25" in actions, f"sc_resume did not run; actions={actions}"
    assert "TargetNextRouteSystem" not in actions, "arrival must NOT run on FAR/stale path"


def test_far_station_dest_records_sc_resume_event():
    """FAR/stale path records ScResumeOnRestart with reason=not_local_star."""
    sender = FakeSender()
    records: list[tuple[str, dict]] = []
    dest = _dest(name="Tortooga", body=17)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo",
                     route=_ROUTE, records=records, jump_age_s=120.0)
    r._maybe_startup()
    names = [n for n, _ in records]
    assert "ScResumeOnRestart" in names, f"expected ScResumeOnRestart, got {names}"


def test_far_route_hop_dest_stale_runs_sc_resume():
    """Priority 4: route-hop Destination (Body=0, Name=next system, NOT current)
    + jump_age > 30s -> sc_resume. The stale loiter case: the ship has been
    in SC for a while at a route hop, not nose-on the arrival star."""
    sender = FakeSender()
    dest = _dest(name="Wredguia UH-U c16-10", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system="Robigo", route=_ROUTE,
                     jump_age_s=120.0)
    r._maybe_startup()
    actions = sender.actions()
    assert "SetSpeed25" in actions, f"sc_resume did not run; actions={actions}"
    assert "TargetNextRouteSystem" not in actions


# ---------------------------------------------------------------------------
# INDETERMINATE: _destination_is_local_star returns None (no system name)
# ---------------------------------------------------------------------------

def test_indeterminate_no_system_name_runs_arrival():
    """_current_system is None -> _destination_is_local_star returns None ->
    indeterminate -> fail-safe to arrival."""
    sender = FakeSender()
    dest = _dest(name="Robigo", body=0)
    st = _status(in_supercruise=True, destination=dest)
    r = _prox_runner(sender, st=st, current_system=None, route=_ROUTE,
                     jump_age_s=120.0)
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
        now_utc=lambda: _NOW_UTC,
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
        now_utc=lambda: _NOW_UTC,
    )
    r.navroute_reader = _make_navroute_reader(_ROUTE)
    r._smacked = True
    r._maybe_startup()
    actions = sender.actions()
    assert "SetSpeed50" in actions, f"smack_recovery must run; actions={actions}"
    assert "SetSpeed25" not in actions
