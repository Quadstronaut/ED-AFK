"""Route-complete detection + system-park terminal (council-ratified
2026-06-07). At route end the bot used to arrive, run arrival, find no next
hop, watchdog 60s, retry required-fail 3x, and mis-report a clean SUCCESS as
'[ABORTED] manual intervention needed' (~5m40s of false alarm). These pin the
detection latch (NavRouteClear + FSDJump SystemAddress correlation, NOT
NavRouteClear-triggered), the system-vs-station decision, and the restart
terminal-idle guard."""

from types import SimpleNamespace

import numpy as np
import pytest

from ed_autojump.flow.dispatcher import FlowRunner, _CLEAR_JOIN_WINDOW_S
from ed_core.flow.model import Procedure, Step
from tests.flow import FakeSender
import ed_autojump.flow.boot_routes as _br
from ed_autojump.flow.boot_routes import classify_startup, dispatch_route_complete

try:
    from ed_vision.ocr_winrt import available as _winrt_available
    _WINRT = _winrt_available()
except Exception:  # noqa: BLE001
    _WINRT = False


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


def test_dest_name_equals_system_body_nonzero_runs_system_park():
    """D1 (Council B docking rebuild): the arrival STAR can carry a nonzero
    Body index too (Body!=0 is NOT sufficient on its own) -- Name==current-
    system with Body!=0 is still the local star -> system park, never dock.
    Closes an explicit D1 acceptance case: 'a Name==system (arrival star) ...
    routes to route_complete_park'."""
    sender = FakeSender()
    records = []
    st = _status(dest_name="Destination Sys", dest_body=1, dest_system=12345)
    r = _runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert any(n == "RouteComplete" and p["type"] == "system" for n, p in records)
    assert not any(n == "RouteCompleteStation" for n, _ in records)
    assert "SetSpeedZero" in sender.actions()
    assert "SetSpeed50" not in sender.actions()


def test_unread_destination_runs_system_park():
    """D1: an unread/ambiguous Destination (status present but no destination
    locked at all) fails closed to park, never dock. Closes the explicit D1
    acceptance case: 'an unread Destination ... routes to route_complete_park'."""
    sender = FakeSender()
    records = []
    st = _status(dest_name=None, dest_body=0, dest_system=0)   # no lock at all
    r = _runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert any(n == "RouteComplete" and p["type"] == "system" for n, p in records)
    assert "SetSpeedZero" in sender.actions()
    assert "SetSpeed50" not in sender.actions()


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


# ---- icon ROUTER: registry-driven dock-vs-park at route-complete -------------
# The name pass (capture-at-plot / settle) only LOCATES a specific-body candidate
# (name_says_station). The icon registry is the KIND authority. Four branches:
#   dock-kind icon            -> DOCK
#   park-kind icon (star/...)  -> PARK + RouteCompleteIconVetoStar (GLIESE class)
#   None + grabber UNWIRED     -> name fallback = today's DOCK (no regression)
#   None + grabber WIRED       -> DOCK on the game-grounded name (Status.Destination
#                                 Body!=0 + station name) + RouteCompleteDockKindAbstainedNameDock
#                                 (an abstain is "don't know", not a veto; only kind=='park' vetoes)
# _destination_dock_kind is monkeypatched for the dock/park branches (the
# perception is validated in the vision tests); the unwired/wired-abstain branches
# exercise the REAL helper (grabber None / grabber-returns-None) without cv2.

def _station_runner(sender, records):
    st = _status(dest_name="Jameson Memorial", dest_body=4, dest_system=12345)
    r = _runner(sender, status=st, record=lambda n, p: records.append((n, p)))
    r._current_system = "Destination Sys"
    _arm_final_waypoint(r, 12345, "Destination Sys")
    r._on_tail_event(_ev("NavRouteClear", timestamp=_ts(0)))
    return r


def test_icon_router_dock_kind_docks(monkeypatch):
    """A positive registry dock-kind icon -> DOCK (the icon promotes, not just
    vetoes): dock runs, park does NOT, RouteCompleteStation recorded."""
    monkeypatch.setattr(_br, "_destination_dock_kind", lambda r: "dock")
    sender = FakeSender(); records = []
    r = _station_runner(sender, records)
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert "SetSpeed50" in sender.actions()               # dock ran
    assert "SetSpeedZero" not in sender.actions()         # NOT parked
    assert any(n == "RouteCompleteStation" for n, _ in records)


def test_icon_router_park_kind_vetoes_to_park(monkeypatch):
    """CV confirms a park-kind (the GLIESE 293 B off-pattern STAR the name pass
    mis-flagged) -> PARK, RouteCompleteIconVetoStar recorded, dock NOT run."""
    monkeypatch.setattr(_br, "_destination_dock_kind", lambda r: "park")
    sender = FakeSender(); records = []
    r = _station_runner(sender, records)
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert "SetSpeedZero" in sender.actions()             # parked
    assert "SetSpeed50" not in sender.actions()           # dock vetoed
    assert any(n == "RouteCompleteIconVetoStar" for n, _ in records)


def test_icon_router_unwired_falls_back_to_name_dock():
    """Grabber UNWIRED (default) -> _destination_dock_kind returns None and the
    router falls back to today's NAME decision: name_says_station -> DOCK. NO
    docking regression while CV is uncalibrated; logged RouteCompleteIconUnwiredNameDock."""
    sender = FakeSender(); records = []
    r = _station_runner(sender, records)
    assert getattr(r, "_navpanel_icon_grabber", None) is None   # unwired default
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert "SetSpeed50" in sender.actions()               # dock ran (today's behavior)
    assert any(n == "RouteCompleteIconUnwiredNameDock" for n, _ in records)


def test_icon_router_wired_but_abstains_docks_on_game_grounded_name():
    """Grabber WIRED but the read ABSTAINS (returns a None frame) -> DOCK on the
    game-grounded name, NOT a fail-closed park. name_says_station already required
    Status.Destination Body!=0 + a station name + not-local-star, so the game
    itself says the destination is a non-star body; an abstain is "don't know",
    not an "it's a star" veto (only kind=='park' vetoes). OPERATOR 2026-07-12:
    targeted Jaques Station, icon abstained in the dense Colonia field, and the
    bot PARKED on the star instead. Logged RouteCompleteDockKindAbstainedNameDock."""
    sender = FakeSender(); records = []
    r = _station_runner(sender, records)
    r._navpanel_icon_grabber = lambda: None      # wired, but yields no frame -> abstain
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Destination Sys",
                   system_address=12345, timestamp=_ts(10)))
    assert "SetSpeed50" in sender.actions()               # DOCK ran (game-grounded name)
    assert "SetSpeedZero" not in sender.actions()         # NOT parked
    assert any(n == "RouteCompleteDockKindAbstainedNameDock" for n, _ in records)


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


def test_navpanel_icon_grabber_kwarg_wires_onto_runner():
    """The constructor kwarg navpanel_icon_grabber (what cli.py passes on a keyed
    live run) must land on runner._navpanel_icon_grabber so the route-complete
    router reads it. Default (no kwarg) stays None -> name fallback, no regression."""
    sender = FakeSender()
    grab = lambda: object()
    r = FlowRunner(procedures={}, sender=sender, navpanel_icon_grabber=grab)
    assert r._navpanel_icon_grabber is grab
    assert FlowRunner(procedures={}, sender=sender)._navpanel_icon_grabber is None


# ---- council fixes: concurrency guard + name-confirmation (ruling C) ---------

def test_dock_kind_holds_exclusive_input_during_grab():
    """Council BLOCKER fix: the open/walk/grab/close runs UNDER the runner's
    exclusive-input guard so the heat watchdog daemon can't inject a DeployHeatSink
    keypress into the open panel. The guard is held while the grab runs, released
    after."""
    sender = FakeSender()
    r = _runner(sender)
    r._dock_target_name = lambda: "Anywhere"   # need a dest name to reach the grab
    saw = {}

    def grab():
        saw["exclusive"] = r.input_exclusive()
        return None                     # frame1 None -> abstain; we assert the guard

    r._navpanel_icon_grabber = grab
    assert _br._destination_dock_kind(r) is None
    assert saw["exclusive"] is True     # guard held during the grab
    assert r.input_exclusive() is False  # released after


def test_dock_kind_no_dest_name_abstains_before_grab():
    """No destination name (can't name-match the row) -> abstain BEFORE touching the
    panel: the grabber is never called."""
    sender = FakeSender()
    r = _runner(sender)
    r._dock_target_name = lambda: None
    calls = []
    r._navpanel_icon_grabber = lambda: calls.append(1) or None
    assert _br._destination_dock_kind(r) is None
    assert calls == []                  # never opened the panel


def _ocr_line(text, y, wh=16):
    """A fake ocr_winrt.OcrLine (text + .y + words[0].h) for the confirmation."""
    return SimpleNamespace(
        text=text, y=y,
        words=[SimpleNamespace(text=text.split()[0], x=0.0, y=float(y),
                               w=10.0, h=float(wh))])


def test_selected_row_confirmed_matches_destination_row():
    """Ruling C: trust the read ONLY when the selected row's OCR name matches the
    destination AND its y coincides with the read band. Mismatched name, wrong-row
    y, missing name, and no-OCR-lines all -> False (caller abstains -> fail-closed)."""
    from ed_vision import ocr_winrt as o
    region = (505, 435, 410, 330)
    ry, wh, ln_y = region[1], 16, 100
    full_y = int(ry + (ln_y - o._PAD) / o._UPSCALE + (wh / o._UPSCALE) / 2.0)
    lines = [_ocr_line("JAMESON MEMORIAL", ln_y, wh),
             _ocr_line("OTHER BODY", ln_y + 300, wh)]
    frame = np.zeros((1080, 1920, 3), np.uint8)

    def conf(dest, cy, lns=lines):
        return _br._selected_row_confirmed(frame, cy, dest, region,
                                           ocr_detail=lambda _c: lns)

    assert conf("Jameson Memorial", full_y) is True           # name + row match
    assert conf("Jameson Memoriai", full_y) is True           # fuzzy OCR slip
    assert conf("Nowhere Station", full_y) is False           # name mismatch
    assert conf("Jameson Memorial", full_y + 300) is False    # right name, wrong row
    assert conf(None, full_y) is False                        # no dest name
    assert conf("Jameson Memorial", full_y, []) is False      # no OCR lines


def _seq_grabber(*names):
    """A bare full-frame grab returning the named REAL fixtures in sequence
    (frame1 for the OCR row-match, frame2 after the simulated cursor-walk)."""
    import cv2
    from pathlib import Path
    base = Path(__file__).resolve().parents[1] / "fixtures" / "navpanel"
    frames = [cv2.imread(str(base / n)) for n in names]
    it = iter(frames)
    return lambda: next(it)


@pytest.mark.skipif(not _WINRT, reason="WinRT OCR not available in this env")
def test_dock_kind_real_frame_unselected_station_walks_and_docks():
    """The headline case: the destination station is NOT the cursor row. grab1 =
    Jameson UNSELECTED (cursor on the row-0 system, shinrarta_populated) -> OCR
    name-matches Jameson at its on-screen row -> cursor-walk -> grab2 = Jameson
    SELECTED (navpanel_nav_station) -> read NON_STAR -> DOCK. This is what the
    over-park bug broke; the walk fixes it."""
    pytest.importorskip("cv2")
    r = _runner(FakeSender())
    r._navpanel_icon_grabber = _seq_grabber(
        "shinrarta_populated_1080.png",        # grab1: Jameson unselected (row 2)
        "navpanel_nav_station_km_1080.png")    # grab2: Jameson now selected
    r._dock_target_name = lambda: "Jameson Memorial"
    assert _br._destination_dock_kind(r) == "dock"


@pytest.mark.skipif(not _WINRT, reason="WinRT OCR not available in this env")
def test_dock_kind_real_frame_star_destination_parks():
    """A STAR destination (the arrival star is row 0, already selected): name-match
    -> walk (row 0, pin only) -> read STAR -> PARK (the GLIESE catastrophe guard)."""
    pytest.importorskip("cv2")
    r = _runner(FakeSender())
    r._navpanel_icon_grabber = _seq_grabber(
        "tyriedgoea_kn-o_b47-1_full.png",      # grab1: star at row 0 (selected)
        "tyriedgoea_kn-o_b47-1_full.png")      # grab2: still the star row
    r._dock_target_name = lambda: "Tyriedgoea KN-O B47-1 A"
    assert _br._destination_dock_kind(r) == "park"


@pytest.mark.skipif(not _WINRT, reason="WinRT OCR not available in this env")
def test_dock_kind_real_frame_dest_not_on_screen_abstains():
    """Ruling C safety: the destination name is on NO row -> resolve_row None -> no
    walk, no read -> None (router fails closed to PARK), never a wrong-row read."""
    pytest.importorskip("cv2")
    r = _runner(FakeSender())
    r._navpanel_icon_grabber = _seq_grabber("shinrarta_populated_1080.png")
    r._dock_target_name = lambda: "Some Far Station Not On Screen"
    assert _br._destination_dock_kind(r) is None


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
