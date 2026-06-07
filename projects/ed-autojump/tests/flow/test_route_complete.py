"""Route-complete detection + system-park terminal (council-ratified
2026-06-07). At route end the bot used to arrive, run arrival, find no next
hop, watchdog 60s, retry required-fail 3x, and mis-report a clean SUCCESS as
'[ABORTED] manual intervention needed' (~5m40s of false alarm). These pin the
detection latch (NavRouteClear + FSDJump SystemAddress correlation, NOT
NavRouteClear-triggered), the system-vs-station decision, and the restart
terminal-idle guard."""

from types import SimpleNamespace

from ed_autojump.flow.dispatcher import FlowRunner, _CLEAR_JOIN_WINDOW_S
from ed_autojump.flow.model import Procedure, Step
from tests.flow import FakeSender


def _ev(name, **fields):
    return SimpleNamespace(event=name, **fields)


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
    arrival does NOT, _route_done is True, the latch is consumed."""
    sender = FakeSender()
    records = []
    r = _runner(sender, status=_status(), record=lambda n, p: records.append((n, p)))
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert "SetSpeedZero" in sender.actions()                    # park ran
    assert "TargetNextRouteSystem" not in sender.actions()    # arrival did NOT
    assert r._route_done is True
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
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Other",
                   system_address=99999, timestamp=_ts(10)))
    assert sender.actions() == ["TargetNextRouteSystem"]      # arrival
    assert r._route_done is False


# ---- guards: no-clear / address-mismatch / stale-clear -----------------------

def test_no_clear_runs_arrival():
    sender = FakeSender()
    r = _runner(sender)
    _arm_final_waypoint(r, 12345, "Destination Sys")
    # no NavRouteClear latched
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert sender.actions() == ["TargetNextRouteSystem"]


def test_no_final_waypoint_runs_arrival():
    sender = FakeSender()
    r = _runner(sender)
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    # _final_waypoint never cached (no route ever seen)
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="X",
                   system_address=12345, timestamp=_ts(10)))
    assert sender.actions() == ["TargetNextRouteSystem"]


def test_stale_clear_outside_window_runs_arrival():
    """A NavRouteClear far outside the join window (manual re-plot long ago)
    must NOT correlate with this jump even at a matching address."""
    sender = FakeSender()
    r = _runner(sender)
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    # jump_ts - clear_ts > window -> stale
    far = f"2026-06-07T12:0{int(_CLEAR_JOIN_WINDOW_S // 60) + 1}:01Z"
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=far))
    assert sender.actions() == ["TargetNextRouteSystem"]


def test_clear_after_jump_is_not_completion():
    """A clear with a LATER timestamp than the jump (negative gap) is not the
    final-hop clear that precedes arrival -> arrival."""
    sender = FakeSender()
    r = _runner(sender)
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(30)))
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert sender.actions() == ["TargetNextRouteSystem"]


# ---- re-arm: a fresh NavRoute clears a prior done latch ----------------------

def test_fresh_navroute_rearms_after_completion():
    """After a completed route, a new plot (NavRoute event) must re-arm: clear
    _route_done and the clear latch so the NEXT route can complete too."""
    sender = FakeSender()

    class _NR:
        def __init__(self, route):
            self.route = route

    nr = _NR([SimpleNamespace(system_address=777, star_system="New Dest")])
    r = _runner(sender, navroute=_FakeNavReader(nr))
    r._route_done = True
    r._navroute_cleared = True
    r._on_tail_event(_ev("NavRoute"))
    assert r._route_done is False
    assert r._navroute_cleared is False
    assert r._final_waypoint == (777, "New Dest")


# ---- system-vs-station decision ----------------------------------------------

def test_station_destination_records_gated_and_still_parks():
    """Destination Body != 0, in the arrival system, name is a non-star
    station -> the station-gated path: record RouteCompleteStationGated AND
    still park (docking not built). Never abort-to-human."""
    sender = FakeSender()
    records = []
    st = _status(dest_name="Jameson Memorial", dest_body=4, dest_system=12345)
    r = _runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert any(n == "RouteCompleteStationGated" and p["station"] == "Jameson Memorial"
               for n, p in records)
    assert "SetSpeedZero" in sender.actions()                    # still parked


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
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert any(n == "RouteComplete" for n, _ in records)
    assert not any(n == "RouteCompleteStationGated" for n, _ in records)


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
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert any("[ROUTE COMPLETE]" in t for t in ov.events)
    assert any("Route complete" in t for t in ov.status_lines)
    assert all("[ABORTED]" not in t for t in ov.status_lines)


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
    r._maybe_startup()
    assert sender.actions() == []                             # idled
    assert any(n == "RouteCompleteIdleOnRestart" for n, _ in records)


def test_restart_parked_terminal_no_destination_idles():
    """Restart in supercruise, NavRoute empty, nothing locked -> also idle."""
    sender = FakeSender()
    st = _status(dest_name=None, in_supercruise=True)
    r = _startup_runner(sender, status=st, navroute=_FakeNavReader(_EmptyNR()))
    r._maybe_startup()
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
    r._maybe_startup()
    assert sender.actions() == ["TargetNextRouteSystem"]      # arrival


def test_restart_no_navroute_reader_runs_arrival():
    """No navroute wiring (reader None) -> route emptiness is UNKNOWN -> fail
    closed to arrival, never idle on an assumption."""
    sender = FakeSender()
    st = _status(dest_name="Destination Sys", in_supercruise=True)
    r = _startup_runner(sender, status=st, navroute=None,
                        current_system="Destination Sys")
    r._maybe_startup()
    assert sender.actions() == ["TargetNextRouteSystem"]      # arrival
