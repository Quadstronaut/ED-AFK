"""Station-dock feature (station-dock): typed events, dock steps, the dock
terminus vs pit-stop dispatch, and the request_docking macro.

Real fakes only (FakeSender + scripted suppliers/waiters), no game, no sleeps.
"""

from types import SimpleNamespace

import pytest

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.dispatcher import FlowRunner
from ed_autojump.flow.model import Procedure, Step
from ed_autojump.flow.steps import INPUT_EXCLUSIVE_ACTIONS, STEP_REGISTRY
from ed_autojump.journal import (
    Docked,
    DockingDenied,
    DockingGranted,
    DockingRequested,
    SupercruiseDestinationDrop,
    Undocked,
    parse_event,
)
from tests.flow import FakeSender


# ============================ typed events parse ============================

def test_supercruise_destination_drop_parses():
    ev = parse_event(
        '{"timestamp":"2026-06-07T12:00:00Z","event":"SupercruiseDestinationDrop",'
        '"Type":"Jameson Memorial","Threat":0,"MarketID":128666762}')
    assert isinstance(ev, SupercruiseDestinationDrop)
    assert ev.type == "Jameson Memorial"
    assert ev.threat == 0
    assert ev.market_id == 128666762


def test_docking_requested_parses():
    ev = parse_event(
        '{"timestamp":"2026-06-07T12:00:00Z","event":"DockingRequested",'
        '"StationName":"Jameson Memorial","StationType":"Orbis","MarketID":1}')
    assert isinstance(ev, DockingRequested)
    assert ev.station_name == "Jameson Memorial"


def test_docking_granted_parses():
    ev = parse_event(
        '{"timestamp":"2026-06-07T12:00:00Z","event":"DockingGranted",'
        '"LandingPad":7,"StationName":"Jameson Memorial","MarketID":1}')
    assert isinstance(ev, DockingGranted)
    assert ev.landing_pad == 7


def test_docking_denied_parses_reason():
    ev = parse_event(
        '{"timestamp":"2026-06-07T12:00:00Z","event":"DockingDenied",'
        '"Reason":"Distance","StationName":"Jameson Memorial","MarketID":1}')
    assert isinstance(ev, DockingDenied)
    assert ev.reason == "Distance"


def test_docked_parses():
    ev = parse_event(
        '{"timestamp":"2026-06-07T12:00:00Z","event":"Docked",'
        '"StationName":"Jameson Memorial","StationType":"Orbis",'
        '"StarSystem":"Shinrarta Dezhra","SystemAddress":3932277478106,"MarketID":1}')
    assert isinstance(ev, Docked)
    assert ev.station_name == "Jameson Memorial"
    assert ev.system_address == 3932277478106


def test_undocked_parses():
    ev = parse_event(
        '{"timestamp":"2026-06-07T12:00:00Z","event":"Undocked",'
        '"StationName":"Jameson Memorial","MarketID":1}')
    assert isinstance(ev, Undocked)
    assert ev.station_name == "Jameson Memorial"


# ============================ test helpers ============================

def _status(*, in_supercruise=False, docked=False, dest_name=None,
            dest_body=0, fsd_mass_locked=False, gui_focus=0):
    dest = None
    if dest_name is not None or dest_body:
        dest = SimpleNamespace(name=dest_name or "", body=dest_body, system=0)
    return SimpleNamespace(
        in_supercruise=in_supercruise, docked=docked, destination=dest,
        fsd_mass_locked=fsd_mass_locked, gui_focus=gui_focus,
        fsd_charging=False, fsd_cooldown=False, overheating=False)


def _waiter_for(*events):
    """An event_waiter returning True the FIRST time it is polled for an event
    in `events`, False otherwise. Each event fires once (so a step that polls
    the same name twice doesn't loop forever)."""
    remaining = set(events)

    def waiter(name, _t):
        if name in remaining:
            remaining.discard(name)
            return True
        return False
    return waiter


# ============================ step_dock_target_station ============================

def test_dock_target_station_already_locked_no_press():
    """Route terminus: the station is ALREADY the locked Destination. The step
    must NOT press SelectTarget — T locks whatever is ahead of the reticle, and
    the ship faces the arrival STAR not the station, so a blind T would CLEAR
    the existing lock (2026-06-08 Robigo live test: 'first thing it did was
    untarget'). State-check FIRST, skip the press."""
    sender = FakeSender()
    st = _status(in_supercruise=False, dest_name="Robigo Mines", dest_body=4)
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      status_supplier=lambda: st)
    assert STEP_REGISTRY["dock_target_station"](ctx) is True
    assert sender.actions() == []   # NO press — station was already targeted


def test_dock_target_station_t_press_confirmed():
    """NOT already locked (Destination body=0); T lands the station and the
    verify read confirms it -> success, no nav-panel fallback."""
    sender = FakeSender()
    # st0 guard read + None-guard read see body=0 (not yet locked); the first
    # verify read after T sees the station locked.
    states = [_status(dest_body=0)] * 2 + [
        _status(dest_name="Jameson Memorial", dest_body=4)] * 4

    def supplier():
        return states.pop(0) if len(states) > 1 else states[0]

    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      status_supplier=supplier)
    assert STEP_REGISTRY["dock_target_station"](ctx) is True
    assert sender.actions() == ["SelectTarget"]   # T only, no fallback macro


def test_dock_target_station_falls_back_to_navpanel():
    """T does NOT lock the station (Body stays 0); the Contacts nav-panel macro
    runs and self-targets it -> success."""
    sender = FakeSender()
    # First reads (after T): no body locked (the None-guard read + 4 verify
    # reads + the cockpit-focus read all see body=0). After the macro runs,
    # the station is locked.
    states = [_status(dest_name="", dest_body=0)] * 6 + [
        _status(dest_name="Jameson Memorial", dest_body=4)] * 4

    def supplier():
        return states.pop(0) if len(states) > 1 else states[0]

    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      status_supplier=supplier)
    assert STEP_REGISTRY["dock_target_station"](ctx) is True
    # T, then the request_docking macro (E,E,...,FocusLeftPanel) ran.
    assert sender.actions()[0] == "SelectTarget"
    assert "CycleNextPanel" in sender.actions()


def test_dock_target_station_no_status_presses_t_only():
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None)  # no status wiring
    assert STEP_REGISTRY["dock_target_station"](ctx) is True
    assert sender.actions() == ["SelectTarget"]


# ============================ step_dock_sc_assist ============================

def test_dock_sc_assist_gates_on_supercruise_exit():
    """Engage SC-assist, then the SupercruiseExit (drop at station) event ends
    the wait -> success."""
    sender = FakeSender()
    st = _status(in_supercruise=True)
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: st,
                      event_waiter=_waiter_for("SupercruiseExit"))
    assert STEP_REGISTRY["dock_sc_assist"](ctx) is True


def test_dock_sc_assist_gates_on_in_supercruise_false_fallback():
    """No SupercruiseExit event, but in_supercruise flips False (the drop) ->
    state fallback ends the wait."""
    sender = FakeSender()
    states = [_status(in_supercruise=True)] * 1 + [_status(in_supercruise=False)] * 5

    def supplier():
        return states.pop(0) if len(states) > 1 else states[0]

    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=supplier,
                      event_waiter=lambda n, t: False)  # never an event
    assert STEP_REGISTRY["dock_sc_assist"](ctx) is True


def test_dock_sc_assist_refuses_when_not_in_supercruise():
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      status_supplier=lambda: _status(in_supercruise=False))
    assert STEP_REGISTRY["dock_sc_assist"](ctx) is False
    assert sender.actions() == []   # never engaged


# ============================ step_dock_request ============================

def test_dock_request_in_range_grant():
    """In range (state fallback: dropped to normal space, station targeted) ->
    request macro runs -> DockingGranted -> success."""
    sender = FakeSender()
    st = _status(in_supercruise=False, dest_name="Jameson Memorial", dest_body=4)
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: st,
                      event_waiter=_waiter_for("DockingGranted"))
    assert STEP_REGISTRY["dock_request"](ctx) is True
    assert "CycleNextPanel" in sender.actions()   # the request macro ran


def test_dock_request_out_of_range_denied_distance_retryable_fail():
    """Out of range request -> DockingDenied Reason=Distance -> the step FAILS
    (the procedure re-approaches). The denial reason is read off the dispatcher
    supplier."""
    sender = FakeSender()
    st = _status(in_supercruise=False, dest_name="Jameson Memorial", dest_body=4)
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
        status_supplier=lambda: st,
        # range signal fires (we may request), but no grant ever comes;
        # the denied reason supplier returns "Distance".
        event_waiter=_waiter_for("ReceiveText"),
        docking_denied_supplier=lambda: "Distance")
    assert STEP_REGISTRY["dock_request"](ctx) is False


def test_dock_request_denied_other_reason_aborts():
    """A non-Distance denial (e.g. NoSpace) also fails the step (the procedure's
    retry exhaustion then aborts to human). Distinguished only by the logged
    reason; the step return is False either way, but the abort log differs."""
    sender = FakeSender()
    logs = []
    st = _status(in_supercruise=False, dest_name="Jameson Memorial", dest_body=4)
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
        status_supplier=lambda: st,
        event_waiter=_waiter_for("ReceiveText"),
        docking_denied_supplier=lambda: "NoSpace",
        record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["dock_request"](ctx) is False
    assert any(n == "DockRequestAbort" and "NoSpace" in p["reason"]
               for n, p in logs)


def test_dock_request_clears_stale_distance_before_delayed_grant():
    """B1/D1 regression: a STALE DockingDenied(Distance) reason — left by
    step_dock_target_station's deliberate out-of-range probe — must NOT
    false-fail an in-range request whose grant is latency-delayed.

    Uses a real FlowRunner so docking_denied_supplier and clear_docking_denied
    share the runner's `_docking_denied_reason` (the actual wiring). The reason
    is PRE-SET to 'Distance' (the stale probe denial); the grant fires a couple
    of polls later. step_dock_request must clear the stale reason on arm and
    wait for the fresh grant -> True.

    FAILS on pre-fix code: the grant loop reads the stale 'Distance' on its
    first iteration (before the grant fires) and returns False.
    """
    sender = FakeSender()
    r = _dock_runner(sender, status=_station_status(docked=False))
    # The stale denial from the out-of-range Contacts-fallback probe.
    r._docking_denied_reason = "Distance"

    # Grant arrives only on the 3rd poll for DockingGranted (latency).
    grant_polls = {"n": 0}

    def waiter(name, _t):
        if name == "ReceiveText":
            return True                      # in range immediately
        if name == "DockingGranted":
            grant_polls["n"] += 1
            return grant_polls["n"] >= 3     # delayed grant
        return False

    ctx = r._make_context()
    # Swap in the scripted waiter (the runner's default waiter needs a hub).
    ctx.event_waiter = waiter
    assert STEP_REGISTRY["dock_request"](ctx) is True
    # The stale reason was cleared on arm (not poisoning the grant loop).
    assert r._docking_denied_reason is None


def test_dock_request_no_event_wiring_runs_macro():
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      status_supplier=lambda: _status())
    assert STEP_REGISTRY["dock_request"](ctx) is True
    assert "CycleNextPanel" in sender.actions()


# ============================ step_dock_await_docked ============================

def test_dock_await_gates_on_docked_event():
    sender = FakeSender()
    st = _status(docked=False)
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: st,
                      event_waiter=_waiter_for("Docked"))
    assert STEP_REGISTRY["dock_await_docked"](ctx) is True


def test_dock_await_state_check_already_docked_no_event():
    """event-gates-need-state-check: the Docked FLAG is true on entry with NO
    Docked event ever firing -> instant success."""
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _status(docked=True),
                      event_waiter=lambda n, t: False)   # no event ever
    assert STEP_REGISTRY["dock_await_docked"](ctx) is True


def test_dock_await_flag_fallback_during_wait():
    """No Docked event, but the docked flag flips True mid-wait -> success."""
    sender = FakeSender()
    states = [_status(docked=False)] * 2 + [_status(docked=True)] * 5

    def supplier():
        return states.pop(0) if len(states) > 1 else states[0]

    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=supplier, event_waiter=lambda n, t: False)
    assert STEP_REGISTRY["dock_await_docked"](ctx) is True


# ============================ step_station_services ============================

def test_station_services_runs_and_verifies_each():
    """The W/Space, D/Space, D/Space sequence runs and each service event is
    verified."""
    sender = FakeSender()
    logs = []
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _status(docked=True),
                      event_waiter=_waiter_for("RefuelAll", "RepairAll", "BuyAmmo"),
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["station_services"](ctx) is True
    acts = sender.actions()
    # Refuel (UI_Up + Select), Repair (UI_Right + Select), Rearm (UI_Right + Select)
    assert acts == ["UI_Up", "UI_Select", "UI_Right", "UI_Select",
                    "UI_Right", "UI_Select"]
    oks = {p["service"] for n, p in logs if n == "StationServiceOk"}
    assert oks == {"RefuelAll", "RepairAll", "BuyAmmo"}


def test_station_services_no_event_is_noop_not_failure():
    """A service whose event never fires (full tank / pristine hull) is logged
    as a no-op and the sequence CONTINUES; the step still succeeds."""
    sender = FakeSender()
    logs = []
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _status(docked=True),
                      event_waiter=lambda n, t: False,   # nothing fires
                      record=lambda n, p: logs.append((n, p)))
    assert STEP_REGISTRY["station_services"](ctx) is True
    noevents = {p["service"] for n, p in logs if n == "StationServiceNoEvent"}
    assert noevents == {"RefuelAll", "RepairAll", "BuyAmmo"}


# ============================ pit-stop steps ============================

def test_auto_launch_gates_on_undocked():
    sender = FakeSender()
    st = _status(docked=True)
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: st,
                      event_waiter=_waiter_for("Undocked"))
    assert STEP_REGISTRY["auto_launch"](ctx) is True
    # S, S, Space.
    assert sender.actions() == ["UI_Down", "UI_Down", "UI_Select"]


def test_auto_launch_already_undocked_no_keys():
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _status(docked=False),
                      event_waiter=lambda n, t: False)
    assert STEP_REGISTRY["auto_launch"](ctx) is True
    assert sender.actions() == []   # nothing to launch


def test_wait_masslock_clear_blocks_until_flag_clears():
    states = [_status(fsd_mass_locked=True)] * 2 + [_status(fsd_mass_locked=False)] * 3

    def supplier():
        return states.pop(0) if len(states) > 1 else states[0]

    sleeps = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: sleeps.append(s),
                      clock=lambda: 0.0, status_supplier=supplier)
    assert STEP_REGISTRY["wait_masslock_clear"](ctx) is True
    assert len(sleeps) >= 1


def test_wait_masslock_clear_instant_when_clear():
    sleeps = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: sleeps.append(s),
                      clock=lambda: 0.0,
                      status_supplier=lambda: _status(fsd_mass_locked=False))
    assert STEP_REGISTRY["wait_masslock_clear"](ctx) is True
    assert sleeps == []


def test_wait_masslock_clear_fails_closed_without_status():
    ctx = StepContext(sender=FakeSender())   # default supplier -> None
    assert STEP_REGISTRY["wait_masslock_clear"](ctx) is False


# ============================ registry / exclusivity contract ============================

def test_dock_steps_registered():
    for name in ("dock_target_station", "dock_sc_assist", "dock_request",
                 "dock_await_docked", "station_services", "auto_launch",
                 "wait_masslock_clear"):
        assert name in STEP_REGISTRY


def test_dock_ui_macros_are_input_exclusive():
    for name in ("dock_target_station", "dock_sc_assist", "dock_request",
                 "station_services", "auto_launch"):
        assert name in INPUT_EXCLUSIVE_ACTIONS
    # dock_await_docked sends no keys -> NOT exclusive.
    assert "dock_await_docked" not in INPUT_EXCLUSIVE_ACTIONS


# ============================ dispatch: terminus vs pit-stop ============================

def _ev(name, **fields):
    return SimpleNamespace(event=name, **fields)


def _full_procs():
    """Single-keypress stand-ins for every procedure the dock dispatch can run,
    so the dispatched target is legible from sender output:
      dock              -> RequestDocking sentinel via a unique action
      route_complete_park -> SetSpeedZero
      dock_resume       -> SetSpeed75
    """
    return {
        "arrival": Procedure(name="arrival", steps=(Step("target_next_route"),)),
        "route_complete_park": Procedure(
            name="route_complete_park", steps=(Step("set_throttle", {"pct": 0}),)),
        "dock": Procedure(name="dock", steps=(Step("set_throttle", {"pct": 50}),)),
        "dock_resume": Procedure(
            name="dock_resume", steps=(Step("set_throttle", {"pct": 75}),)),
        "smack_recovery": Procedure(
            name="smack_recovery", steps=(Step("set_throttle", {"pct": 25}),)),
    }


def _station_status(*, docked=False):
    dest = SimpleNamespace(name="Jameson Memorial", body=4, system=12345)
    return SimpleNamespace(destination=dest, in_supercruise=True, docked=docked,
                           fsd_charging=False, fsd_cooldown=False,
                           fsd_mass_locked=False, overheating=False)


def _dock_runner(sender, *, status, record=None):
    r = FlowRunner(
        procedures=_full_procs(), sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: status, record=record)
    return r


def test_station_terminus_runs_dock_and_records_docked():
    """Route ends at a station -> dock runs (SetSpeed50). The Docked flag is set
    (the dock procedure 'docked' it), so RouteCompleteDocked is recorded and the
    bot stays docked — no auto-launch."""
    sender = FakeSender()
    records = []
    st = _station_status()
    r = _dock_runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    r._final_waypoint = (12345, "Destination Sys")
    # Mark the ship as docked the way _apply_state would on a Docked event,
    # since the stub dock procedure doesn't emit one.
    r._docked = True
    r._docked_station = "Jameson Memorial"
    r._on_tail_event(_ev("NavRouteClear", timestamp="2026-06-07T12:00:00Z"))
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp="2026-06-07T12:00:10Z"))
    assert "SetSpeed50" in sender.actions()                  # dock ran
    assert "SetSpeedZero" not in sender.actions()            # NOT the park path
    assert any(n == "RouteCompleteStation" for n, _ in records)
    assert any(n == "RouteCompleteDocked" and p["station"] == "Jameson Memorial"
               for n, p in records)


def test_station_dock_not_completed_no_docked_record():
    """If the dock did NOT complete (docked flag stays False), no
    RouteCompleteDocked is recorded (the procedure's own abort line stands)."""
    sender = FakeSender()
    records = []
    st = _station_status()
    r = _dock_runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    r._final_waypoint = (12345, "Destination Sys")
    # _docked stays False (dock failed).
    r._on_tail_event(_ev("NavRouteClear", timestamp="2026-06-07T12:00:00Z"))
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp="2026-06-07T12:00:10Z"))
    assert "SetSpeed50" in sender.actions()                  # dock attempted
    assert not any(n == "RouteCompleteDocked" for n, _ in records)


def test_pit_stop_new_route_while_docked_runs_dock_resume():
    """A NavRoute event with a non-empty route arriving WHILE docked -> the
    pit-stop resume (dock_resume, SetSpeed75) runs."""
    sender = FakeSender()
    records = []

    class _NR:
        route = [SimpleNamespace(system_address=777, star_system="Next Hop")]

    r = FlowRunner(
        procedures=_full_procs(), sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: _station_status(docked=True),
        navroute_reader=type("R", (), {"poll": lambda self: _NR(),
                                       "current": _NR()})(),
        record=lambda n, p: records.append((n, p)))
    r._docked = True
    r._docked_station = "Jameson Memorial"
    r.dispatch(_ev("NavRoute"))
    assert "SetSpeed75" in sender.actions()                  # dock_resume ran
    assert any(n == "DockPitStopResume" for n, _ in records)


def test_new_route_while_NOT_docked_does_not_resume():
    """A NavRoute event while NOT docked must NOT trigger the pit-stop resume
    (that path is docked-only)."""
    sender = FakeSender()

    class _NR:
        route = [SimpleNamespace(system_address=777, star_system="Next Hop")]

    r = FlowRunner(
        procedures=_full_procs(), sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: _station_status(docked=False),
        navroute_reader=type("R", (), {"poll": lambda self: _NR(),
                                       "current": _NR()})())
    r._docked = False
    r.dispatch(_ev("NavRoute"))
    assert "SetSpeed75" not in sender.actions()              # no resume


def test_empty_navroute_while_docked_stays_docked():
    """A NavRoute with an EMPTY route while docked is a clear, not a new plot ->
    stays docked (terminus), no resume."""
    sender = FakeSender()

    class _EmptyNR:
        route = []

    r = FlowRunner(
        procedures=_full_procs(), sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: _station_status(docked=True),
        navroute_reader=type("R", (), {"poll": lambda self: _EmptyNR(),
                                       "current": _EmptyNR()})())
    r._docked = True
    r.dispatch(_ev("NavRoute"))
    assert "SetSpeed75" not in sender.actions()


# ============================ dispatcher dock-state tracking ============================

def test_apply_state_tracks_docked_and_denial_reason():
    sender = FakeSender()
    r = _dock_runner(sender, status=_station_status())
    r._on_tail_event(_ev("DockingDenied", reason="Distance"))
    assert r._docking_denied_reason == "Distance"
    # A grant supersedes the stale denial.
    r._on_tail_event(_ev("DockingGranted"))
    assert r._docking_denied_reason is None
    # Docked sets the flag + station name and clears any denial.
    r._on_tail_event(_ev("Docked", station_name="Jameson Memorial"))
    assert r._docked is True
    assert r._docked_station == "Jameson Memorial"
    # Undocked clears the flag.
    r._on_tail_event(_ev("Undocked", station_name="Jameson Memorial"))
    assert r._docked is False


def test_docking_denied_supplier_wired_into_context():
    """The dispatcher exposes the last denial reason to steps via the context's
    docking_denied_supplier."""
    sender = FakeSender()
    r = _dock_runner(sender, status=_station_status())
    r._docking_denied_reason = "Distance"
    ctx = r._make_context()
    assert ctx.docking_denied_supplier() == "Distance"


# ============================ dock procedure file ============================

def test_dock_procedure_loads_and_validates():
    from pathlib import Path
    from ed_autojump.flow.loader import load_procedures, validate_procedure
    proc_dir = Path(__file__).resolve().parents[2] / "procedures"
    procs = load_procedures(proc_dir)
    assert "dock" in procs
    assert "dock_resume" in procs
    errors = validate_procedure(procs["dock"], STEP_REGISTRY.keys())
    errors += validate_procedure(procs["dock_resume"], STEP_REGISTRY.keys())
    assert errors == [], errors


def test_dock_procedure_gates_are_required():
    from pathlib import Path
    from ed_autojump.flow.loader import load_procedures
    proc_dir = Path(__file__).resolve().parents[2] / "procedures"
    dock = load_procedures(proc_dir)["dock"]
    required = {s.action for s in dock.steps if s.required}
    assert {"dock_target_station", "dock_sc_assist", "dock_request",
            "dock_await_docked"} <= required
    # station_services is best-effort (a no-op service is not a failure).
    assert "station_services" not in required
