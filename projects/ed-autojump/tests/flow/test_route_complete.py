"""Route-complete detection + system-park terminal (council-ratified
2026-06-07). At route end the bot used to arrive, run arrival, find no next
hop, watchdog 60s, retry required-fail 3x, and mis-report a clean SUCCESS as
'[ABORTED] manual intervention needed' (~5m40s of false alarm). These pin the
detection latch (NavRouteClear + FSDJump SystemAddress correlation, NOT
NavRouteClear-triggered), the system-vs-station decision, and the restart
terminal-idle guard."""

from types import SimpleNamespace

from ed_autojump.flow.dispatcher import FlowRunner, _CLEAR_JOIN_WINDOW_S
from ed_core.flow.model import Procedure, Step
from tests.flow import FakeSender
import ed_autojump.flow.boot_routes as _br
from ed_autojump.flow.boot_routes import classify_startup, dispatch_route_complete


def _ev(name, **fields):
    return SimpleNamespace(event=name, **fields)


def _dispatch(r, ev):
    name = getattr(ev, 'event', None)
    if name == 'FSDJump':
        _br._route_fsd_jump(r, ev)
    elif name == 'SupercruiseExit':
        _br._route_sc_exit(r, ev)
    elif name == 'NavRoute':
        _br._route_nav_route(r, ev)


# A jump's journal timestamp is its OWN ISO8601 stamp (the same field
# _parse_journal_ts reads); the NavRouteClear carries one too. The window is a
# correlation between those two stamps, not wall clock.
def _ts(sec: int) -> str:
    """A journal-style UTC timestamp at minute 0, `sec` seconds in."""
    return f"2026-06-07T12:00:{sec:02d}Z"


def _status(dest_name=None, *, dest_body=0, dest_system=0,
            in_supercruise=True):
    dest = None
    if dest_name is not None or dest_body or dest_system:
        dest = SimpleNamespace(name=dest_name or "", body=dest_body,
                               system=dest_system)
    return SimpleNamespace(
        destination=dest, in_supercruise=in_supercruise, docked=False,
        fsd_charging=False, fsd_cooldown=False, fsd_mass_locked=False,
        overheating=False)


class _FakeNavReader:
    """Stands in for NavRouteReader: poll() returns the current parse on first
    call then None (mtime-unchanged), and .current always holds it — exactly
    how _navroute_state falls back. `value` may be a NavRoute-like object or
    None (no route file)."""
    def __init__(self, value):
        self._value = value
        self._polled = False

    def poll(self):
        if self._polled:
            return None
        self._polled = True
        return self._value

    @property
    def current(self):
        return self._value


def _runner(sender, *, status=None, record=None, navroute=None, overlay=None):
    """Distinguishable single-step procedures so the dispatch target is
    legible from sender output: arrival -> TargetNextRouteSystem,
    route_complete_park -> SetSpeedZero (set_throttle 0)."""
    procs = {
        "arrival": Procedure(name="arrival", steps=(Step("target_next_route"),)),
        "route_complete_park": Procedure(
            name="route_complete_park",
            steps=(Step("set_throttle", {"pct": 0}),)),
        # dock (station terminus) -> SetSpeed50, distinguishable from park.
        "dock": Procedure(name="dock", steps=(Step("set_throttle", {"pct": 50}),)),
    }
    return FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: status if status is not None else _status(),
        navroute_reader=navroute,
        record=record, overlay=overlay,
    )


def _arm_final_waypoint(r, addr, sysname):
    """Cache a final waypoint exactly as _apply_state would on a NavRoute
    event: by reading the navroute reader. We set it directly here (no reader
    wired in these dispatch-level tests)."""
    r._final_waypoint = (addr, sysname)


# ---- E1 happy path -----------------------------------------------------------

def test_e1_route_complete_runs_park_not_arrival():
    """Cache the final waypoint, latch a NavRouteClear, then an FSDJump whose
    SystemAddress matches within the window -> route_complete_park runs,
    arrival does NOT, the latch is consumed (the single-shot re-fire guard)."""
    sender = FakeSender()
    records = []
    r = _runner(sender, status=_status(), record=lambda n, p: records.append((n, p)))
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert "SetSpeedZero" in sender.actions()                    # park ran
    assert "TargetNextRouteSystem" not in sender.actions()    # arrival did NOT
    assert r._navroute_cleared is False                       # latch consumed
    assert any(n == "RouteComplete" and p["type"] == "system" for n, p in records)


# ---- E4 manual-cancel: clear then jump to a DIFFERENT system -----------------

def test_e4_clear_then_jump_to_different_system_runs_arrival():
    """A NavRouteClear from a manual re-plot, then an FSDJump to a system that
    is NOT the cached final waypoint -> normal arrival, never route-complete."""
    sender = FakeSender()
    r = _runner(sender)
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Other",
                   system_address=99999, timestamp=_ts(10)))
    # Arrival ran (NOT park). The C2 orchestrator additionally branches to a
    # successor section after arrival (here 'dock' via the empty-route
    # terminal read, since no navroute is wired into this dispatch-level
    # fake) — so assert arrival ran and park did NOT, not list-exact equality.
    assert "TargetNextRouteSystem" in sender.actions()       # arrival ran
    assert "SetSpeedZero" not in sender.actions()            # NOT route-park


# ---- guards: no-clear / address-mismatch / stale-clear -----------------------

def test_no_clear_runs_arrival():
    sender = FakeSender()
    r = _runner(sender)
    _arm_final_waypoint(r, 12345, "Destination Sys")
    # no NavRouteClear latched
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    # Arrival ran (NOT route-park). C2 then branches a successor section after
    # arrival; assert arrival ran and park did NOT (the route-complete decision
    # under test), not exact list equality.
    assert "TargetNextRouteSystem" in sender.actions()
    assert "SetSpeedZero" not in sender.actions()


def test_no_final_waypoint_runs_arrival():
    sender = FakeSender()
    r = _runner(sender)
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    # _final_waypoint never cached (no route ever seen)
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="X",
                   system_address=12345, timestamp=_ts(10)))
    # Arrival ran (NOT route-park); C2 branches a successor section after.
    assert "TargetNextRouteSystem" in sender.actions()
    assert "SetSpeedZero" not in sender.actions()


def test_stale_clear_outside_window_runs_arrival():
    """A NavRouteClear far outside the join window (manual re-plot long ago)
    must NOT correlate with this jump even at a matching address."""
    sender = FakeSender()
    r = _runner(sender)
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    # jump_ts - clear_ts > window -> stale
    far = f"2026-06-07T12:0{int(_CLEAR_JOIN_WINDOW_S // 60) + 1}:01Z"
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=far))
    # Arrival ran (NOT route-park); C2 branches a successor section after.
    assert "TargetNextRouteSystem" in sender.actions()
    assert "SetSpeedZero" not in sender.actions()


def test_clear_after_jump_is_not_completion():
    """A clear with a LATER timestamp than the jump (negative gap) is not the
    final-hop clear that precedes arrival -> arrival."""
    sender = FakeSender()
    r = _runner(sender)
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(30)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    # Arrival ran (NOT route-park); C2 branches a successor section after.
    assert "TargetNextRouteSystem" in sender.actions()
    assert "SetSpeedZero" not in sender.actions()


# ---- re-arm: a fresh NavRoute clears a prior done latch ----------------------

def test_fresh_navroute_rearms_after_completion():
    """After a completed route, a new plot (NavRoute event) must re-arm: clear
    the clear latch and re-cache the new final waypoint so the NEXT route can
    complete too."""
    sender = FakeSender()

    class _NR:
        def __init__(self, route):
            self.route = route

    nr = _NR([SimpleNamespace(system_address=777, star_system="New Dest")])
    r = _runner(sender, navroute=_FakeNavReader(nr))
    r._navroute_cleared = True
    r._on_tail_event(_ev("NavRoute"))
    assert r._navroute_cleared is False
    assert r._final_waypoint == (777, "New Dest")


# ---- FIX 1: reader-driven resolution (journal rotation / NavRoute-event miss) -
# These exercise the REAL populate path (_resolve_final_waypoint reading the
# durable NavRoute.json reader) instead of _arm_final_waypoint, which bypasses
# it. They FAIL against the pre-fix code, whose _is_route_complete only consulted
# the event-cached self._final_waypoint and never the reader.

class _NRRoute:
    """A NavRoute-like object with a .route list of waypoints."""
    def __init__(self, route):
        self.route = route


def _wp(addr, name):
    return SimpleNamespace(system_address=addr, star_system=name)


def test_fix1_journal_rotation_no_navroute_event_resolves_from_reader():
    """The council's MISSED-fire case: route was plotted in a journal that has
    since rotated, so NO NavRoute event was seen this session -> _final_waypoint
    is None. But NavRoute.json (the FILE) persists and the reader returns it. A
    NavRouteClear + matching final-hop FSDJump must STILL fire route-complete by
    resolving the waypoint from the reader. (FAILS pre-fix: None waypoint -> the
    5m40s false-abort grind this feature exists to kill.)"""
    sender = FakeSender()
    records = []
    nr = _NRRoute([_wp(1, "Hop A"), _wp(2, "Hop B"), _wp(54321, "Final Sys")])
    r = _runner(sender, status=_status(), navroute=_FakeNavReader(nr),
                record=lambda n, p: records.append((n, p)))
    assert r._final_waypoint is None                          # no NavRoute event
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Final Sys",
                   system_address=54321, timestamp=_ts(10)))
    assert "SetSpeedZero" in sender.actions()                    # park ran
    assert "TargetNextRouteSystem" not in sender.actions()    # arrival did NOT
    assert any(n == "RouteComplete" for n, _ in records)


def test_fix1_reader_none_first_poll_then_current_holds_route():
    """Event/file race: the reader's first poll() returns None (mtime-unchanged
    at the moment we look) but .current holds the last good route -> _navroute_
    state falls through to .current and the waypoint still resolves -> fires."""
    sender = FakeSender()
    nr = _NRRoute([_wp(54321, "Final Sys")])

    class _PollNoneCurrentHolds:
        """poll() ALWAYS returns None (no mtime change ever); .current holds."""
        def poll(self):
            return None

        @property
        def current(self):
            return nr

    r = _runner(sender, status=_status(), navroute=_PollNoneCurrentHolds())
    assert r._final_waypoint is None
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Final Sys",
                   system_address=54321, timestamp=_ts(10)))
    assert "SetSpeedZero" in sender.actions()                    # park ran
    assert "TargetNextRouteSystem" not in sender.actions()


def test_fix1_reader_resolved_address_mismatch_runs_arrival():
    """Reader resolves a final waypoint, but THIS jump's address doesn't match
    it (a mid-route hop arriving with a stale clear latched) -> not complete ->
    arrival. Proves reader-resolution still honours the int-address gate."""
    sender = FakeSender()
    nr = _NRRoute([_wp(54321, "Final Sys")])
    r = _runner(sender, status=_status(), navroute=_FakeNavReader(nr))
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Mid Hop",
                   system_address=999, timestamp=_ts(10)))
    assert sender.actions() == ["TargetNextRouteSystem"]      # arrival


# ---- FIX: latch-consumed second jump (no re-plot) ----------------------------

def test_second_jump_same_system_no_replot_runs_arrival():
    """After a completed route, a SECOND FSDJump to the SAME address with NO
    fresh plot must run arrival, NOT a second park. The single-shot clear latch
    (consumed at completion) is the guard — _route_done was dead and is gone."""
    sender = FakeSender()
    nr = _NRRoute([_wp(54321, "Final Sys")])
    r = _runner(sender, status=_status(), navroute=_FakeNavReader(nr))
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    # First jump completes the route -> park.
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Final Sys",
                   system_address=54321, timestamp=_ts(10)))
    assert "SetSpeedZero" in sender.actions()
    assert r._navroute_cleared is False                       # latch consumed
    # Second jump into the same system, no NavRoute / NavRouteClear between.
    sender2 = FakeSender()
    r.sender = sender2
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Final Sys",
                   system_address=54321, timestamp=_ts(20)))
    assert sender2.actions() == ["TargetNextRouteSystem"]     # arrival, not park


# ---- system-vs-station decision ----------------------------------------------

def test_station_destination_runs_dock_not_park():
    """Destination Body != 0, in the arrival system, name is a non-star station
    -> the STATION branch runs the real dock flow (SetSpeed50 stand-in), NOT
    the system park (SetSpeedZero), and records RouteCompleteStation. (Replaces
    the old gated-marker-and-park behavior, which the dock feature superseded.)"""
    sender = FakeSender()
    records = []
    st = _status(dest_name="Jameson Memorial", dest_body=4, dest_system=12345)
    r = _runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert any(n == "RouteCompleteStation" and p["station"] == "Jameson Memorial"
               for n, p in records)
    assert "SetSpeed50" in sender.actions()                  # dock ran
    assert "SetSpeedZero" not in sender.actions()            # NOT the park path


def test_star_destination_body_zero_runs_system_park():
    """Destination Body == 0 (an FSD route hop / primary star lock) -> system
    park, NOT the station-gated path."""
    sender = FakeSender()
    records = []
    st = _status(dest_name="Destination Sys", dest_body=0, dest_system=12345)
    r = _runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert any(n == "RouteComplete" for n, _ in records)
    assert not any(n == "RouteCompleteStationGated" for n, _ in records)


def test_dest_locked_in_different_system_runs_system_park():
    """Stale Destination lock from a PRIOR hop: a body is locked (Body != 0) but
    its .system is a DIFFERENT system than this arrival -> NOT a station here ->
    system park, never station-gated. (Non-blocking nit (e).)"""
    sender = FakeSender()
    records = []
    # dest_system 99999 != the arrival's system_address 12345
    st = _status(dest_name="Some Station", dest_body=7, dest_system=99999)
    r = _runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert any(n == "RouteComplete" and p["type"] == "system" for n, p in records)
    assert not any(n == "RouteCompleteStationGated" for n, _ in records)
    assert "SetSpeedZero" in sender.actions()


def test_address_mismatch_same_system_name_runs_arrival():
    """SystemAddress is the int identity; star_system name matching is NOT
    enough. A jump whose NAME equals the cached final waypoint's but whose
    ADDRESS differs (procedural-name collision / dupe) is NOT route-complete ->
    arrival. (Non-blocking nit (f).)"""
    sender = FakeSender()
    r = _runner(sender)
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=67890, timestamp=_ts(10)))   # name same, addr differs
    # Arrival ran (NOT route-park); C2 branches a successor section after.
    assert "TargetNextRouteSystem" in sender.actions()       # arrival ran
    assert "SetSpeedZero" not in sender.actions()            # NOT route-park


def test_route_complete_overlay_uses_event_and_status_slots():
    """System park announces in the EVENT slot (transient) AND the STATUS slot
    (persistent positive idle line) — distinct from the [ABORTED] alarm."""
    class _Overlay:
        def __init__(self):
            self.events, self.status_lines = [], []

        def event(self, t):
            self.events.append(t)

        def status(self, t):
            self.status_lines.append(t)

    ov = _Overlay()
    sender = FakeSender()
    r = _runner(sender, overlay=ov)
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert any("[ROUTE COMPLETE]" in t for t in ov.events)
    assert any("Route complete" in t for t in ov.status_lines)
    assert all("[ABORTED]" not in t for t in ov.status_lines)


# ---- icon-CV veto: confirmed STAR downgrades a name-flagged station to park --
# The GLIESE 293 B class (live 2026-06-21): an off-pattern arrival STAR whose
# name is unrelated to the system defeats the name heuristics, which mis-flag it
# a station. The nav-panel column-0 ICON is the authoritative kind signal; a
# positive STAR confirmation VETOES the dock and parks instead. The CV read is
# monkeypatched here (the perception primitive is validated in the vision tests),
# so these exercise the WIRING in dispatch_route_complete without cv2.

def test_icon_veto_downgrades_station_to_park(monkeypatch):
    """CV confirms the locked destination is a STAR -> the name-based station
    decision is VETOED: park runs, dock does NOT, RouteCompleteIconVetoStar is
    recorded."""
    monkeypatch.setattr(_br, "_destination_icon_is_star", lambda r: True)
    sender = FakeSender()
    records = []
    st = _status(dest_name="Jameson Memorial", dest_body=4, dest_system=12345)
    r = _runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert "SetSpeedZero" in sender.actions()             # parked
    assert "SetSpeed50" not in sender.actions()           # dock vetoed
    assert any(n == "RouteCompleteIconVetoStar" for n, _ in records)


def test_icon_abstain_leaves_name_station_decision(monkeypatch):
    """CV abstains (None: unwired / panel closed / unreadable) -> the name-based
    decision stands unchanged -> dock runs, exactly as before the veto existed
    (ZERO regression when the grab is uncalibrated)."""
    monkeypatch.setattr(_br, "_destination_icon_is_star", lambda r: None)
    sender = FakeSender()
    records = []
    st = _status(dest_name="Jameson Memorial", dest_body=4, dest_system=12345)
    r = _runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert "SetSpeed50" in sender.actions()               # dock ran (unchanged)
    assert not any(n == "RouteCompleteIconVetoStar" for n, _ in records)


def test_icon_non_star_does_not_veto_a_real_station(monkeypatch):
    """CV confirms NON_STAR (a real station's icon) -> NO veto: the dock decision
    stands. The veto only fires on a POSITIVE star, never on a station read."""
    monkeypatch.setattr(_br, "_destination_icon_is_star", lambda r: False)
    sender = FakeSender()
    records = []
    st = _status(dest_name="Jameson Memorial", dest_body=4, dest_system=12345)
    r = _runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert "SetSpeed50" in sender.actions()               # dock ran
    assert not any(n == "RouteCompleteIconVetoStar" for n, _ in records)


def test_destination_icon_is_star_unwired_abstains():
    """No _navpanel_icon_grabber wired -> _destination_icon_is_star abstains
    (None): returns BEFORE importing cv2, never raises -- the default until the
    operator calibrates the panel-open grab."""
    sender = FakeSender()
    r = _runner(sender)
    assert getattr(r, "_navpanel_icon_grabber", None) is None
    assert _br._destination_icon_is_star(r) is None


def test_destination_icon_grabber_exception_abstains():
    """A grabber that RAISES -> abstain (None), never propagates into the
    terminal handler. Fail-closed: a CV fault can't crash route-complete."""
    sender = FakeSender()
    r = _runner(sender)

    def _boom():
        raise RuntimeError("capture backend died")

    r._navpanel_icon_grabber = _boom
    assert _br._destination_icon_is_star(r) is None


# ---- _maybe_startup terminal-idle on restart --------------------------------

def _startup_runner(sender, *, status, navroute, record=None, current_system=None):
    procs = {
        "startup": Procedure(name="startup", steps=(Step("target_ahead"),)),
        "arrival": Procedure(name="arrival", steps=(Step("target_next_route"),)),
        "smack_recovery": Procedure(
            name="smack_recovery", steps=(Step("set_throttle", {"pct": 50}),)),
    }
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0, sleeper=lambda s: None,
        status_supplier=lambda: status, navroute_reader=navroute, record=record)
    if current_system is not None:
        r._current_system = current_system
    return r


class _EmptyNR:
    route: list = []


def test_restart_parked_terminal_idles_no_arrival():
    """Restart in supercruise, NavRoute EMPTY, Destination is the local
    primary star -> idle, run NOTHING (no arrival, the false-abort path)."""
    sender = FakeSender()
    records = []
    st = _status(dest_name="Destination Sys", in_supercruise=True)
    r = _startup_runner(sender, status=st, navroute=_FakeNavReader(_EmptyNR()),
                        record=lambda n, p: records.append((n, p)),
                        current_system="Destination Sys")
    classify_startup(r)
    assert sender.actions() == []                             # idled
    assert any(n == "RouteCompleteIdleOnRestart" for n, _ in records)


def test_restart_parked_terminal_no_destination_idles():
    """Restart in supercruise, NavRoute empty, nothing locked -> also idle."""
    sender = FakeSender()
    st = _status(dest_name=None, in_supercruise=True)
    r = _startup_runner(sender, status=st, navroute=_FakeNavReader(_EmptyNR()))
    classify_startup(r)
    assert sender.actions() == []


def test_restart_with_live_route_runs_arrival():
    """Restart in supercruise but the route still has waypoints -> mid-route
    arrival-star restart, NOT a completed route -> run arrival."""
    sender = FakeSender()

    class _LiveNR:
        route = [SimpleNamespace(system_address=1, star_system="Next")]

    st = _status(dest_name="Destination Sys", in_supercruise=True)
    r = _startup_runner(sender, status=st, navroute=_FakeNavReader(_LiveNR()),
                        current_system="Destination Sys")
    classify_startup(r)
    assert sender.actions() == ["TargetNextRouteSystem"]      # arrival


def test_restart_no_navroute_reader_runs_arrival():
    """No navroute wiring (reader None) -> route emptiness is UNKNOWN -> fail
    closed to arrival, never idle on an assumption."""
    sender = FakeSender()
    st = _status(dest_name="Destination Sys", in_supercruise=True)
    r = _startup_runner(sender, status=st, navroute=None,
                        current_system="Destination Sys")
    classify_startup(r)
    assert sender.actions() == ["TargetNextRouteSystem"]      # arrival


# ---- FIX 2: malformed navroute object (no/None .route) fails CLOSED ----------
# Pre-fix: `if nr is None or getattr(nr, "route", None): return False`. A
# malformed object whose .route is absent/None -> getattr returns None ->
# falsy -> does NOT return False -> falls through and may IDLE on UNKNOWN state
# (fail-OPEN). Post-fix distinguishes route is None (UNKNOWN -> closed) from
# route == [] (KNOWN empty -> parked). These FAIL pre-fix (they'd idle).

def test_restart_navroute_route_attr_missing_runs_arrival():
    """The reader returns an object with NO .route attribute at all (malformed
    / partial parse) -> UNKNOWN, not 'empty' -> fail closed -> arrival runs."""
    sender = FakeSender()

    class _NoRouteAttr:        # deliberately has no .route
        pass

    st = _status(dest_name="Destination Sys", in_supercruise=True)
    r = _startup_runner(sender, status=st, navroute=_FakeNavReader(_NoRouteAttr()),
                        current_system="Destination Sys")
    classify_startup(r)
    assert sender.actions() == ["TargetNextRouteSystem"]      # arrival, not idle


def test_restart_navroute_route_is_none_runs_arrival():
    """The reader returns an object whose .route is explicitly None (UNKNOWN)
    -> fail closed -> arrival runs, never idles."""
    sender = FakeSender()

    class _NoneRoute:
        route = None

    st = _status(dest_name="Destination Sys", in_supercruise=True)
    r = _startup_runner(sender, status=st, navroute=_FakeNavReader(_NoneRoute()),
                        current_system="Destination Sys")
    classify_startup(r)
    assert sender.actions() == ["TargetNextRouteSystem"]      # arrival, not idle
