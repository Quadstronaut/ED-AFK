"""Station-dock feature (station-dock): typed events, dock steps, the dock
terminus vs pit-stop dispatch, and the request_docking macro.

Real fakes only (FakeSender + scripted suppliers/waiters), no game, no sleeps.
"""

from types import SimpleNamespace

import pytest

from ed_core.flow.context import StepContext
from ed_autojump.flow.dispatcher import FlowRunner
from ed_core.flow.model import Procedure, Step
from ed_autojump.flow.steps import INPUT_EXCLUSIVE_ACTIONS, STEP_REGISTRY
from ed_core.journal import (
    Docked,
    DockingDenied,
    DockingGranted,
    DockingRequested,
    ReceiveText,
    SupercruiseDestinationDrop,
    Undocked,
    parse_event,
)
from tests.flow import FakeSender
import ed_autojump.flow.boot_routes as _br
from ed_autojump.flow.boot_routes import classify_startup, dispatch_route_complete


def _dispatch(r, ev):
    name = getattr(ev, 'event', None)
    if name == 'FSDJump':
        _br._route_fsd_jump(r, ev)
    elif name == 'SupercruiseExit':
        _br._route_sc_exit(r, ev)
    elif name == 'NavRoute':
        _br._route_nav_route(r, ev)


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


# ============================ ReceiveText event ============================

def test_receive_text_parses_no_fire_zone():
    """The no-fire-zone entry message must parse as a typed ReceiveText with the
    $STATION_NoFireZone_entered; token in the Message field."""
    ev = parse_event(
        '{"timestamp":"2026-06-08T00:00:00Z","event":"ReceiveText",'
        '"From":"","Message":"$STATION_NoFireZone_entered;","Channel":"npc"}')
    assert isinstance(ev, ReceiveText)
    assert "$STATION_NoFireZone_entered;" in ev.message


def test_receive_text_nfz_sets_dispatcher_flag():
    """FlowRunner._apply_state sets _no_fire_zone_entered=True when the
    no-fire-zone ReceiveText arrives."""
    sender = FakeSender()
    r = _dock_runner(sender, status=_station_status())
    assert r._no_fire_zone_entered is False
    r._on_tail_event(_ev("ReceiveText", message="$STATION_NoFireZone_entered;"))
    assert r._no_fire_zone_entered is True


def test_receive_text_other_message_no_flag():
    """A ReceiveText with a different message (NPC comms, mission update) must
    NOT set _no_fire_zone_entered."""
    sender = FakeSender()
    r = _dock_runner(sender, status=_station_status())
    r._on_tail_event(_ev("ReceiveText", message="$Comm_Friendly_Hello;"))
    assert r._no_fire_zone_entered is False


def test_nfz_supplier_wired_into_context():
    """no_fire_zone_supplier and clear_no_fire_zone are properly wired into
    the StepContext (same pattern as docking_denied_supplier)."""
    sender = FakeSender()
    r = _dock_runner(sender, status=_station_status())
    r._no_fire_zone_entered = True
    ctx = r._make_context()
    assert ctx.no_fire_zone_supplier() is True
    ctx.clear_no_fire_zone()
    assert r._no_fire_zone_entered is False


# ============================ step_dock_approach ============================

def test_dock_approach_closes_on_nfz_signal():
    """After SC-assist drop the ship is outside 7.5km. dock_approach throttles
    at 25%, gates on NoFireZone_entered (via no_fire_zone_supplier), then zeros
    throttle -> True."""
    sender = FakeSender()
    # no_fire_zone_supplier starts False; flips True after one poll
    # (simulates the ReceiveText arriving mid-loop).
    calls = {"n": 0}

    def nfz():
        calls["n"] += 1
        return calls["n"] > 1  # False on clear-check and first poll; True after

    ctx = StepContext(
        sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
        event_waiter=lambda name, t: False,  # no events fire directly
        no_fire_zone_supplier=nfz,
        clear_no_fire_zone=lambda: None)
    assert STEP_REGISTRY["dock_approach"](ctx) is True
    acts = sender.actions()
    # SetSpeed25 first (throttle forward), then SetSpeedZero (zero on exit).
    assert acts[0] == "SetSpeed25"
    assert acts[-1] == "SetSpeedZero"


def test_dock_approach_already_in_range_no_throttle():
    """Edge case: ship is already inside 7.5km at step entry (e.g. bot restarted
    close to station). no_fire_zone_supplier returns True on entry -> immediate
    pass, no throttle presses."""
    sender = FakeSender()
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
        event_waiter=lambda name, t: False,
        no_fire_zone_supplier=lambda: True,
        clear_no_fire_zone=lambda: None)
    assert STEP_REGISTRY["dock_approach"](ctx) is True
    assert sender.actions() == []   # no throttle — already in range


def test_dock_approach_clears_stale_nfz_flag_on_arm():
    """dock_approach must call clear_no_fire_zone on entry so a stale True from
    a prior approach (e.g. a retry loop) cannot skip the closing leg.
    The clear happens even when the subsequent supplier then returns True
    (the 'already in range after clearing' path)."""
    cleared = {"did": False}
    nfz_values = {"v": True}  # starts True (stale); stays True after clear

    def clear():
        cleared["did"] = True
        # Simulate the flag being re-set externally after clear (edge-case:
        # a new ReceiveText arrives between clear and first check). We leave
        # it True so the step still exits quickly, but the clear MUST have run.
        nfz_values["v"] = True

    sender = FakeSender()
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
        event_waiter=lambda name, t: False,
        no_fire_zone_supplier=lambda: nfz_values["v"],
        clear_no_fire_zone=clear)
    STEP_REGISTRY["dock_approach"](ctx)
    assert cleared["did"] is True   # clear was called on arm


def test_dock_approach_zeros_throttle_on_watchdog_timeout():
    """If the NoFireZone signal never arrives before max_approach_s, the step
    fails (returns False) but MUST zero the throttle first (ram guard: the ship
    must not keep flying into the station)."""
    sender = FakeSender()
    clock_t = {"t": 0.0}

    def clock():
        clock_t["t"] += 200.0  # each call advances past max_approach_s=120.0
        return clock_t["t"]

    ctx = StepContext(
        sender=sender, sleeper=lambda s: None, clock=clock,
        event_waiter=lambda name, t: False,  # no signal ever
        no_fire_zone_supplier=lambda: False,
        clear_no_fire_zone=lambda: None)
    result = STEP_REGISTRY["dock_approach"](ctx)
    assert result is False
    acts = sender.actions()
    assert "SetSpeed25" in acts       # throttle was set
    assert acts[-1] == "SetSpeedZero" # zeroed before return


def test_dock_approach_no_event_wiring_is_noop():
    """Without event wiring (unit tests with no journal) dock_approach returns
    True immediately (no-op fallback, same pattern as other steps)."""
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None)   # no wiring
    assert STEP_REGISTRY["dock_approach"](ctx) is True
    assert sender.actions() == []   # no keys at all


def test_dock_approach_nfz_via_receive_text_event():
    """ReceiveText event fires during the loop. After the event the supplier
    reports True -> step exits cleanly."""
    sender = FakeSender()
    saw_nfz = {"v": False}

    def waiter(name, t):
        if name == "ReceiveText":
            saw_nfz["v"] = True
            return True
        return False

    ctx = StepContext(
        sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
        event_waiter=waiter,
        no_fire_zone_supplier=lambda: saw_nfz["v"],
        clear_no_fire_zone=lambda: None)
    assert STEP_REGISTRY["dock_approach"](ctx) is True
    acts = sender.actions()
    assert acts[0] == "SetSpeed25"
    assert acts[-1] == "SetSpeedZero"


# ============================ step_dock_request (no range wait) ============================
# The new step_dock_request no longer waits for the in-range signal itself —
# that is step_dock_approach's job. These tests cover the new contract.

def test_dock_request_fires_immediately_and_gates_on_grant():
    """step_dock_request no longer waits for a range signal — it runs the
    request macro immediately then gates on DockingGranted."""
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
                      status_supplier=lambda: _status(docked=False),
                      event_waiter=_waiter_for("DockingGranted"))
    assert STEP_REGISTRY["dock_request"](ctx) is True
    assert "CycleNextPanel" in sender.actions()   # the request macro ran


def test_dock_request_no_range_state_fallback():
    """The broken state fallback (not in_supercruise + station targeted) was
    REMOVED. dock_request must NOT fire immediately just because the ship is in
    normal space with the station targeted — that was the root of the bug.
    With no event wiring the step runs the macro (legacy no-wiring path),
    but with wiring it must not bypass the grant wait on state alone."""
    sender = FakeSender()
    st = _status(in_supercruise=False, dest_name="Jameson Memorial", dest_body=4)
    # event_waiter is wired but DockingGranted never fires and denial is None.
    # Old code: would trigger can_request via state fallback and then spin on
    # the grant wait -> watchdog. New code: runs macro immediately, then spins
    # on grant wait -> watchdog.  Either way it must NOT pass just on state.
    # CLOCK: must ADVANCE so clock() - start exceeds max_wait_s=120.0 and the
    # watchdog trips. A constant lambda: 200.0 gives clock()-start=0 forever
    # -> infinite loop (the broken-test bug, 895d833).
    clock_t = {"t": 0.0}

    def adv_clock():
        clock_t["t"] += 200.0
        return clock_t["t"]
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None, clock=adv_clock,
        status_supplier=lambda: st,
        event_waiter=lambda name, t: False)   # no events
    assert STEP_REGISTRY["dock_request"](ctx) is False   # watchdog


# ============================ registry / exclusivity contract ============================

def test_dock_steps_registered():
    for name in ("dock_target_station", "dock_sc_assist", "dock_approach",
                 "dock_request", "dock_await_docked", "station_services",
                 "auto_launch", "wait_masslock_clear"):
        assert name in STEP_REGISTRY


def test_dock_ui_macros_are_input_exclusive():
    for name in ("dock_target_station", "dock_sc_assist", "dock_approach",
                 "dock_request", "station_services", "auto_launch"):
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
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
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
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
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
    _dispatch(r, _ev("NavRoute"))
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
    _dispatch(r, _ev("NavRoute"))
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
    _dispatch(r, _ev("NavRoute"))
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
    from ed_core.flow.loader import load_procedures, validate_procedure
    proc_dir = Path(__file__).resolve().parents[2] / "procedures"
    procs = load_procedures(proc_dir)
    assert "dock" in procs
    assert "dock_resume" in procs
    errors = validate_procedure(procs["dock"], STEP_REGISTRY.keys())
    errors += validate_procedure(procs["dock_resume"], STEP_REGISTRY.keys())
    assert errors == [], errors


def test_dock_procedure_gates_are_required():
    from pathlib import Path
    from ed_core.flow.loader import load_procedures
    proc_dir = Path(__file__).resolve().parents[2] / "procedures"
    dock = load_procedures(proc_dir)["dock"]
    required = {s.action for s in dock.steps if s.required}
    assert {"dock_target_station", "dock_sc_assist", "dock_approach",
            "dock_request", "dock_await_docked"} <= required
    # station_services is best-effort (a no-op service is not a failure).
    assert "station_services" not in required


def test_dock_procedure_retry_from_is_dock_approach():
    """on_required_fail.retry_from must be 'dock_approach', not
    'dock_target_station': a Distance denial should re-close from the current
    position, NOT re-fly SC-assist from the system star."""
    from pathlib import Path
    from ed_core.flow.loader import load_procedures
    proc_dir = Path(__file__).resolve().parents[2] / "procedures"
    dock = load_procedures(proc_dir)["dock"]
    assert dock.on_required_fail.retry_from == "dock_approach"


# ============================ capture-at-plot ============================
# Four TDD tests for _dock_target capture and consumption.
# These exercise the LIVE-TEST-GATED mechanic: the game may or may not set
# Status.Destination.Body != 0 at NavRoute time (see __init__ comment in
# dispatcher.py).  The tests drive the code path directly by setting up a
# status supplier that returns a station destination at NavRoute time.

def _system_status():
    """Status with a SYSTEM star destination (Body 0) — the common hop case."""
    dest = SimpleNamespace(name="Robigo", body=0, system=99999)
    return SimpleNamespace(destination=dest, in_supercruise=True, docked=False,
                           fsd_charging=False, fsd_cooldown=False,
                           fsd_mass_locked=False, overheating=False)


def test_capture_at_plot_stores_station_dest():
    """NavRoute event while Status.Destination is a named non-star body ->
    _dock_target is populated with (system_addr, body, name)."""
    sender = FakeSender()
    # Status at NavRoute time: station is the locked Destination.
    st_station = SimpleNamespace(
        destination=SimpleNamespace(name="Robigo Mines", body=4, system=55555),
        in_supercruise=True, docked=False, fsd_charging=False,
        fsd_cooldown=False, fsd_mass_locked=False, overheating=False)

    class _NR:
        route = [SimpleNamespace(system_address=55555, star_system="Robigo")]

    r = FlowRunner(
        procedures=_full_procs(), sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: st_station,
        navroute_reader=type("R", (), {"poll": lambda self: _NR(),
                                       "current": _NR()})())
    r._on_tail_event(_ev("NavRoute"))

    assert r._dock_target == (55555, 4, "Robigo Mines")


def test_capture_at_plot_ignores_system_star_dest():
    """NavRoute event while Status.Destination is a SYSTEM star (Body 0) ->
    _dock_target stays None -> park path is preserved (fail-safe)."""
    sender = FakeSender()

    class _NR:
        route = [SimpleNamespace(system_address=99999, star_system="Robigo")]

    r = FlowRunner(
        procedures=_full_procs(), sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: _system_status(),
        navroute_reader=type("R", (), {"poll": lambda self: _NR(),
                                       "current": _NR()})())
    r._on_tail_event(_ev("NavRoute"))

    assert r._dock_target is None


def test_capture_at_plot_clears_stale_station_on_system_replot():
    """A station capture must NOT persist into a later system-only plot
    (skeptic seat): FlowRunner is long-lived, so a second NavRoute to a SYSTEM
    (Body 0) clears the prior station latch -> park, never a wrong dock."""
    sender = FakeSender()
    st_station = SimpleNamespace(
        destination=SimpleNamespace(name="Robigo Mines", body=4, system=55555),
        in_supercruise=True, docked=False, fsd_charging=False,
        fsd_cooldown=False, fsd_mass_locked=False, overheating=False)
    cur = {"st": st_station}

    class _NR:
        route = [SimpleNamespace(system_address=55555, star_system="Robigo")]

    r = FlowRunner(
        procedures=_full_procs(), sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: cur["st"],
        navroute_reader=type("R", (), {"poll": lambda self: _NR(),
                                       "current": _NR()})())
    r._on_tail_event(_ev("NavRoute"))
    assert r._dock_target == (55555, 4, "Robigo Mines")   # station captured
    # A NEW plot to a pure system (Body 0) must CLEAR the stale capture.
    cur["st"] = _system_status()
    r._on_tail_event(_ev("NavRoute"))
    assert r._dock_target is None


def test_route_complete_with_captured_station_runs_dock():
    """Route arrives at the captured station's system -> dock runs (SetSpeed50)
    instead of park (SetSpeedZero), even though live Status.Destination has been
    overwritten to the system star (Body 0) by target_next_route hops."""
    sender = FakeSender()
    records = []

    # Live status at arrival: Destination has been overwritten to the star.
    star_dest = SimpleNamespace(name="Robigo", body=0, system=55555)
    st_arrival = SimpleNamespace(
        destination=star_dest, in_supercruise=True, docked=False,
        fsd_charging=False, fsd_cooldown=False, fsd_mass_locked=False,
        overheating=False)

    r = FlowRunner(
        procedures=_full_procs(), sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: st_arrival,
        record=lambda n, p: records.append((n, p)))
    r._current_system = "Robigo"
    r._final_waypoint = (55555, "Robigo")
    # Simulate a prior capture-at-plot: the station was snapshotted when the
    # operator plotted to "Robigo Mines" and Status.Destination.Body was 4.
    r._dock_target = (55555, 4, "Robigo Mines")
    r._docked = True
    r._docked_station = "Robigo Mines"

    r._on_tail_event(_ev("NavRouteClear", timestamp="2026-06-08T05:44:54Z"))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Robigo",
                   system_address=55555, timestamp="2026-06-08T05:45:05Z"))

    assert "SetSpeed50" in sender.actions()              # dock ran
    assert "SetSpeedZero" not in sender.actions()        # NOT the park path
    assert any(n == "RouteCompleteStation" and p["station"] == "Robigo Mines"
               for n, p in records)


def test_route_complete_no_capture_parks():
    """No capture-at-plot (_dock_target is None) AND live Destination is the
    system star -> system park path (SetSpeedZero), not dock (SetSpeed50)."""
    sender = FakeSender()
    records = []

    r = FlowRunner(
        procedures=_full_procs(), sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: _system_status(),
        record=lambda n, p: records.append((n, p)))
    r._current_system = "Robigo"
    r._final_waypoint = (99999, "Robigo")
    # _dock_target is None (default) — the current park behavior.

    r._on_tail_event(_ev("NavRouteClear", timestamp="2026-06-08T05:44:54Z"))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Robigo",
                   system_address=99999, timestamp="2026-06-08T05:45:05Z"))

    assert "SetSpeedZero" in sender.actions()            # park ran
    assert "SetSpeed50" not in sender.actions()          # dock did NOT run
    assert not any(n == "RouteCompleteStation" for n, _ in records)
