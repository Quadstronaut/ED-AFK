"""Map live journal events to procedures, run them through the interpreter,
launch the parallel honk track, and own the live tail/status loop.

Replaces the orchestrator's escape/engage handlers. Replay (catch-up) events
only update state; actions fire only once caught up to LIVE."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..fsd.scoops import scoop_max_rate_t_s
from .context import ShipFuel, StepContext
from .interpreter import run_procedure
from .model import Procedure

# Procedures whose whole premise is a live supercruise/fresh-load scene: a
# star smack (SupercruiseExit Body=Star) yanks that scene away mid-run, so
# they are PREEMPTED at the next abort poll instead of grinding retry cycles
# against normal-space glare (the 2026-06-06 13:26 pattern: arrival kept
# macro-pressing for 3 retries before the queued smack could dispatch).
# smack_recovery is deliberately absent: a RE-smack is exactly the scene its
# own retry path expects.
# dock joins arrival/startup: its approach leg flies a live supercruise scene
# toward the station, so a star smack (SupercruiseExit Body=Star) mid-approach
# yanks that scene away and must hand off to smack_recovery instead of grinding
# the dock retry loop against normal-space glare.
_PREEMPT_ON_SMACK = frozenset({"arrival", "startup", "dock", "sc_resume"})

# Route-complete correlation join window. NavRouteClear fires in witchspace
# ~10s before the destination FSDJump; this is the max gap (in JOURNAL
# timestamps, not wall clock) we accept between that clear and the arriving
# FSDJump before treating the clear as belonging to a different event (e.g. a
# manual re-plot minutes earlier). This is a CORRELATION window between two
# journal events — NOT a wall-clock success/failure gate (house rule).
_CLEAR_JOIN_WINDOW_S = 60.0


class _TailHub:
    """Single consumer of JournalTail; fans every event out to ALL subscribers.

    Why: the honk track and the main procedure used to call tail.step()
    concurrently, and each journal event was consumed by exactly ONE of them
    at random. A StartJump eaten by the honk's waiter was invisible to
    hold_alignment; an FSSDiscoveryScan eaten by the main waiter blinded the
    honk. JournalTail.step() also isn't thread-safe. The hub serialises the
    tail behind a lock and gives every waiter its own queue, so every
    subscriber sees every event exactly once.
    """

    def __init__(self, tail: Any, on_event: Optional[Callable[[Any], None]] = None):
        self._tail = tail
        self._on_event = on_event   # state tracking — called ONCE per event
        self._lock = threading.Lock()
        self._queues: dict[int, list] = {}
        self._next_handle = 0

    def subscribe(self) -> int:
        with self._lock:
            h = self._next_handle
            self._next_handle += 1
            self._queues[h] = []
            return h

    def unsubscribe(self, handle: int) -> None:
        with self._lock:
            self._queues.pop(handle, None)

    def poll(self, handle: int) -> list:
        """Pump the tail once, broadcast, and return this subscriber's
        pending events. An unsubscribed handle returns [] (a parallel track
        that outlived its join window polls into silence and exits on its
        own key-release backstop rather than KeyError-ing)."""
        with self._lock:
            for ev in self._tail.step():
                if self._on_event is not None:
                    self._on_event(ev)
                for q in self._queues.values():
                    q.append(ev)
            pending = self._queues.get(handle)
            if pending is None:
                return []
            self._queues[handle] = []
            return pending


class FlowRunner:
    def __init__(
        self,
        *,
        procedures: dict[str, Procedure],
        sender: Any,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        now_utc: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        status_supplier: Callable[[], Optional[Any]] = lambda: None,
        compass_reader: Optional[Any] = None,
        frame_grabber: Optional[Callable[[], Any]] = None,
        align_kwargs: Optional[dict] = None,
        compass_samples: int = 7,
        widget_ring_enabled: bool = False,
        widget_ring_reader: Optional[Any] = None,
        widget_frame_grabber: Optional[Callable[[], Any]] = None,
        widget_ring_on_miss: str = "degrade",
        overlay: Optional[Any] = None,
        record: Optional[Callable[[str, Any], None]] = None,
        frame_sink: Optional[Callable[[str, Any], None]] = None,
        tail: Optional[Any] = None,
        status_reader: Optional[Any] = None,
        navroute_reader: Optional[Any] = None,
        panic_switch: Optional[Any] = None,
        heat_eject_cooldown_s: float = 10.0,
    ):
        self.procedures = procedures
        self.sender = sender
        self.clock = clock
        self.sleeper = sleeper
        self.now_utc = now_utc
        self.status_supplier = status_supplier
        self.compass_reader = compass_reader
        self.frame_grabber = frame_grabber
        self.align_kwargs = align_kwargs or {}
        self.compass_samples = compass_samples
        self.widget_ring_enabled = widget_ring_enabled
        self.widget_ring_reader = widget_ring_reader
        self.widget_frame_grabber = widget_frame_grabber
        self.widget_ring_on_miss = widget_ring_on_miss
        self.overlay = overlay
        self.record = record
        self.frame_sink = frame_sink
        self.tail = tail
        self.status_reader = status_reader
        self.navroute_reader = navroute_reader
        self.panic_switch = panic_switch
        self.heat_eject_cooldown_s = heat_eject_cooldown_s

        self._event_times: dict[str, float] = {}
        # True while the journal's last SC transition is SupercruiseExit at a
        # Star (see _record_event_time) — restart-while-smacked routing.
        self._smacked = False
        # Witchspace latch (hyperspace loading screen). SET on a Hyperspace
        # StartJump (JumpType=="Hyperspace"), CLEARED on FSDJump (~18s window,
        # journal-confirmed). While set the interpreter PAUSES every step —
        # the nav panel / orient scene is invalid and input is harmful.
        # Belt-and-suspenders: also cleared on SupercruiseEntry / Docked so a
        # missed FSDJump line can never permanently wedge the bot. Event-gated
        # only — NO clock/timer (no-arbitrary-timed-waits rule).
        self._in_witchspace = False
        self._latest_status: Optional[Any] = status_supplier()
        self._caught_up = False
        self._startup_done = False
        self._last_eject_t: float = 0.0
        self._jumps = 0
        self.stop_requested = False
        # Mid-procedure preemption (2026-06-06): _on_tail_event fires DURING
        # procedures (in-step waiters pump the hub), so a scene-invalidating
        # event can flag the CURRENT run to abort at its next poll. Strings,
        # not Events: set/read across the main + track threads, worst case a
        # beat late, never torn.
        self._running_proc: Optional[str] = None
        self._preempt: Optional[str] = None
        # Single tail consumer + fan-out (see _TailHub). None without a tail.
        self._hub: Optional[_TailHub] = (
            _TailHub(tail, on_event=self._on_tail_event) if tail is not None else None)
        # Status.json is read from the main loop, honk waiters, and the heat
        # watchdog thread — serialise the reader.
        self._status_lock = threading.Lock()
        # >0 while a UI macro owns input (heat watchdog pauses). Counter, not
        # a bool, so a parallel track can't clear the main track's hold.
        self._exclusive_lock = threading.Lock()
        self._exclusive_count = 0
        # Latest FSDTarget + monotone seq — target_next_route's danger gate
        # reads these to tell a NEW target from a stale one.
        self._fsd_target_seq = 0
        self._latest_fsd_target: Optional[Any] = None
        # scoop_refuel inputs (spec 2026-06-06-scoop-refuel-design §4.3),
        # fed by backlog AND live events like _smacked:
        # - the CURRENT system's arrival-star class = the last HYPERSPACE
        #   StartJump's StarClass (FSDTarget at arrival time is the NEXT hop;
        #   supercruise StartJumps carry null and must not clobber).
        # - tank size + equipped scoop max rate from the latest Loadout
        #   (written at every LoadGame, so backlog always provides one).
        self._arrival_star_class: Optional[str] = None
        # Current system name (FSDJump/Location StarSystem, backlog AND live)
        # — feeds the star-lock identity check (2026-06-07 council).
        self._current_system: Optional[str] = None
        self._ship_fuel: Optional[ShipFuel] = None
        # AWARE-UTC timestamp of the last FSDJump, parsed from the EVENT'S OWN
        # journal timestamp (NOT _event_times["jump"], which is monotonic
        # clock() stamped at backlog REPLAY time and reads a 25-min-stale
        # restart as a fresh arrival — the 11:57Z incident). scoop_refuel's
        # stale-arrival skip derives its age from this.
        self._last_fsdjump_utc: Optional[datetime] = None
        # Route-complete detection (council-ratified 2026-06-07). The final hop
        # emits FSDTarget RemainingJumpsInRoute:1 -> StartJump Hyperspace ->
        # NavRouteClear (in witchspace, ~10s pre-arrival) -> FSDJump to the
        # destination. NavRouteClear ALSO fires on a manual re-plot, so it is
        # NOT the trigger by itself: we cache the LAST waypoint while the route
        # still exists, latch the clear + its timestamp, and at the next FSDJump
        # confirm completion by matching SystemAddress (int, never name) AND a
        # tight journal-timestamp correlation window. The _navroute_cleared latch
        # is single-shot (consumed at completion), which by itself blocks a
        # re-fire on a second jump into the same system without a fresh plot.
        #
        # _final_waypoint is cached from the NavRoute EVENT when present, but is
        # ALSO resolved at decision time from the DURABLE NavRoute.json reader
        # (_navroute_state) — the event is missing across a journal rotation /
        # game restart mid-route, while the FILE persists (FIX 2026-06-07: the
        # missed-fire bug where a rotation dropped the cache and the final hop
        # fell into the 5m40s false-abort grind).
        self._final_waypoint: Optional[tuple[int, str]] = None
        self._navroute_cleared: bool = False
        self._navroute_cleared_utc: Optional[datetime] = None
        # Station docking (station-dock feature). Reason of the last
        # DockingDenied (step_dock_request reads it via docking_denied_supplier
        # to tell a retryable Distance denial from an abort-to-human one);
        # whether the ship is currently docked at a station (the pit-stop
        # resume trigger arms only while docked); and the station name for the
        # terminus overlay/record.
        self._docking_denied_reason: Optional[str] = None
        self._docked: bool = False
        self._docked_station: Optional[str] = None
        # CAPTURE-AT-PLOT (station-dock): when the operator plots a route to a
        # STATION (galaxy map -> station), the game may set Status.Destination
        # to the station body (Body != 0) at the NavRoute event — BEFORE the
        # bot's first TargetNextRouteSystem overwrites it to the route's first
        # hop system star (Body 0). We snapshot it here and use it as the dock
        # trigger at route-complete instead of the (by-then-overwritten) live
        # Destination. Stored as (system_address, body, name).
        #
        # LIVE-TEST-GATED: the game mechanic "plot-to-station sets
        # Status.Destination.Body != 0 at NavRoute" is UNCONFIRMED from
        # journals (Status.json is a live snapshot with no history; the journals
        # for this session show a system-level plot, not a station-level one).
        # If the mechanic does NOT hold, _dest_is_named_station() returns False
        # at the NavRoute event -> _dock_target stays None -> is_station stays
        # False -> today's park path. Fail-safe: absent or wrong capture ==
        # current behavior, never worse.
        #
        # Operator confirmation test (do before relying on this in production):
        # 1. Undock, route cleared.
        # 2. Galaxy map -> plot to a STATION (e.g. "Robigo Mines"), confirm.
        #    Do NOT press Target-Next or move.
        # 3. Read Status.json Destination. If Body != 0 and Name == station ->
        #    this capture works. If Body == 0 -> capture-at-plot is dead;
        #    a different signal is needed before this helps.
        self._dock_target: Optional[tuple[int, int, str]] = None

    # ---- public state accessors ------------------------------------------
    def event_time(self, name: str) -> Optional[float]:
        return self._event_times.get(name)

    def _jump_age(self) -> Optional[float]:
        """Seconds since the last FSDJump, per the EVENT'S OWN journal
        timestamp. None when no FSDJump has been seen. Evaluated at call time
        (now_utc()) so a context wires the live age, not a build-time snapshot.
        Both sides are AWARE-UTC datetimes — subtraction is well-defined."""
        if self._last_fsdjump_utc is None:
            return None
        return (self.now_utc() - self._last_fsdjump_utc).total_seconds()

    @staticmethod
    def _parse_journal_ts(ts: str) -> Optional[datetime]:
        """Parse an ED journal ISO8601 timestamp ("...Z") into an AWARE-UTC
        datetime. The trailing Z (Zulu/UTC) is normalised to +00:00 for
        fromisoformat on older Pythons. Returns None on a malformed stamp so a
        garbled line degrades to 'no jump seen' rather than crashing the tail."""
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    def _fresh_status(self) -> Optional[Any]:
        """Re-poll Status.json on every read. The previous wiring handed steps
        a snapshot frozen at procedure start — `_poll_status` only ran in the
        outer live loop, so engage_jump's fsd_charging gate and any in-step
        state machine were reading STALE flags for the procedure's whole life.
        Event-driven steps need live state; a stat() per read is cheap.

        Without a status_reader (tests), fall back to the injected supplier
        so scripted fakes stay live too."""
        if self.status_reader is not None:
            self._poll_status()
        else:
            st = self.status_supplier()
            if st is not None:
                self._latest_status = st
        return self._latest_status

    def _should_abort(self) -> bool:
        """Operator abort signal: panic hotkey or stop request. OPERATOR-ONLY
        by design — the preempt flag must not flow through here (the heat
        watchdog exits PERMANENTLY on this signal; a transient preemption
        would kill heat protection for the rest of the session). Per-run
        contexts get the combined signal via _run_abort instead."""
        if self.stop_requested:
            return True
        return self.panic_switch is not None and getattr(
            self.panic_switch, "tripped", False)

    def _run_abort(self) -> bool:
        """Abort signal for the CURRENT procedure's contexts: operator abort
        OR scene preemption. run_procedure polls this before every step and
        every in-step loop consults it, so a preempt lands at the next poll
        — cooperative, key-release-safe, no thread killing."""
        return self._should_abort() or self._preempt is not None

    # ---- exclusive-input guard (heat watchdog pauses) ----------------------
    @contextmanager
    def _exclusive_input(self):
        """Mark 'a UI macro owns input' for the body's duration. The heat
        watchdog skips ticks while any holder is active (spec
        2026-06-06-heat-watchdog-design)."""
        with self._exclusive_lock:
            self._exclusive_count += 1
        try:
            yield
        finally:
            with self._exclusive_lock:
                self._exclusive_count -= 1

    def input_exclusive(self) -> bool:
        with self._exclusive_lock:
            return self._exclusive_count > 0

    # ---- context construction --------------------------------------------
    def _clear_docking_denied(self) -> None:
        """Reset the stashed DockingDenied reason. step_dock_request calls this
        when it arms so it acts only on a denial earned by its own request,
        never a stale one left by an earlier out-of-range probe."""
        self._docking_denied_reason = None

    def _make_context(self) -> StepContext:
        # Each context gets its OWN hub subscription so concurrent waiters
        # (honk track + main procedure) all see every event. _run owns the
        # unsubscribe via the handle stashed on the context.
        # No hub (no tail) -> event_waiter=None, TRUTHFULLY "no journal
        # wiring": steps take their unit-test fallbacks instead of spinning
        # on a waiter that always says True against suppliers that never
        # advance.
        handle = self._hub.subscribe() if self._hub is not None else None
        waiter = (None if self._hub is None else
                  (lambda name, t, _h=handle: self._wait_for_event(_h, name, t)))
        ctx = StepContext(
            sender=self.sender,
            clock=self.clock,
            sleeper=self.sleeper,
            compass_reader=self.compass_reader,
            frame_grabber=self.frame_grabber,
            align_kwargs=self.align_kwargs,
            compass_samples=self.compass_samples,
            widget_ring_enabled=self.widget_ring_enabled,
            widget_ring_reader=self.widget_ring_reader,
            widget_frame_grabber=self.widget_frame_grabber,
            widget_ring_on_miss=self.widget_ring_on_miss,
            overlay=self.overlay,
            status_supplier=self._fresh_status,
            event_time=self.event_time,
            event_waiter=waiter,
            should_abort=self._run_abort,
            exclusive_guard=self._exclusive_input,
            fsd_target_supplier=self._fsd_target_state,
            navroute_supplier=self._navroute_state,
            arrival_star_class_supplier=lambda: self._arrival_star_class,
            current_system_supplier=lambda: self._current_system,
            ship_fuel_supplier=lambda: self._ship_fuel,
            # Bound method, NOT a lambda capturing a value: jump age must be
            # evaluated at call time (now_utc() advances) — a build-time
            # snapshot would freeze the age at context construction.
            jump_age_supplier=self._jump_age,
            docking_denied_supplier=lambda: self._docking_denied_reason,
            clear_docking_denied=self._clear_docking_denied,
            in_witchspace=lambda: self._in_witchspace,
            record=self.record,
            frame_sink=self.frame_sink,
        )
        ctx._tail_handle = handle   # for _run's unsubscribe; None without a tail
        return ctx

    # ---- running procedures ----------------------------------------------
    def _run(self, name: str) -> None:
        proc = self.procedures.get(name)
        if proc is None:
            return
        # Fresh run owns a fresh preempt slate; the previous run's flag must
        # not abort this one (smack_recovery dispatches right after the
        # arrival it preempted).
        self._preempt = None
        self._running_proc = name
        ctxs: list[StepContext] = []
        ctx = self._make_context()
        ctxs.append(ctx)
        threads: list[threading.Thread] = []
        try:
            for track_name in proc.parallel_tracks:
                track = self.procedures.get(track_name)
                if track is None:
                    continue
                track_ctx = self._make_context()
                ctxs.append(track_ctx)
                th = threading.Thread(
                    target=run_procedure, args=(track, track_ctx), daemon=True
                )
                th.start()
                threads.append(th)
            result = run_procedure(proc, ctx)
            for th in threads:
                th.join(timeout=15.0)
            if result.aborted and self._preempt is not None:
                # PREEMPTED, not aborted (2026-06-07 14:24:09Z: arrival's
                # star-smack preempt printed "[ABORTED] ... manual intervention
                # needed" then smack_recovery auto-dispatched 61ms later — the
                # message lied). A preempt is a scene handoff, NOT a terminal
                # abort: no "manual intervention", no failed-at clause, and the
                # successor name is NOT hardcoded (run_live's queued event owns
                # the dispatch). Transient EVENT slot only — never status():
                # the persistent status line belongs to true terminal aborts
                # (council intersection: event-slot + status-stays-empty).
                msg = f"[PREEMPTED] {name} — {self._preempt}"
                print(msg, flush=True)
                if self.overlay is not None:
                    try:
                        self.overlay.event(msg)
                    except Exception:  # noqa: BLE001
                        pass
            elif result.aborted:
                # ABORTED = human eyes required (notification-only: NO
                # auto-restart, NO retry). Name the failing step when there is
                # one, but guard the operator-abort case where the last step
                # may not be the failer (or there are no steps at all) so the
                # message stays sensible either way.
                msg = f"[ABORTED] {name} — manual intervention needed"
                if result.steps:
                    msg += f" (failed at {result.steps[-1].action})"
                print(msg, flush=True)
                if self.overlay is not None:
                    # Persistent STATUS slot (the keepalive line that stays
                    # up), NOT event() — an abort must remain visible. Fail-soft
                    # like every other overlay call here.
                    try:
                        self.overlay.status(msg)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            self._running_proc = None
            if self._preempt is not None and self.record is not None:
                self.record("Preempted", {"procedure": name,
                                          "reason": self._preempt})
            # Drop every subscription, even for a track that outlived its
            # join window — a leaked queue grows for the rest of the session.
            # A late track polling an unsubscribed handle just gets [] and
            # exits on its own backstop (see _TailHub.poll).
            if self._hub is not None:
                for c in ctxs:
                    h = getattr(c, "_tail_handle", None)
                    if h is not None:
                        self._hub.unsubscribe(h)

    def dispatch(self, ev: Any) -> None:
        """Run the procedure mapped to a LIVE event."""
        name = getattr(ev, "event", None)
        if name == "FSDJump":
            self._jumps += 1
            if self.overlay is not None:
                system = getattr(ev, "star_system", None) or getattr(ev, "StarSystem", None)
                try:
                    self.overlay.event(f"Jump {self._jumps}"
                                       + (f": {system}" if system else ""))
                except Exception:  # noqa: BLE001
                    pass
            # Route-complete check (council-ratified): is this the LAST hop?
            # Consume the NavRouteClear latch and run the terminal park instead
            # of arrival — arrival's target_next_route would find no next hop
            # and mis-report a clean success as a manual-intervention abort.
            if self._is_route_complete(ev):
                # Consume the single-shot latch: a second FSDJump into this same
                # system without a fresh plot finds no clear latched and falls
                # through to normal arrival (it is NOT a re-completion).
                self._navroute_cleared = False
                self.dispatch_route_complete(ev)
                return
            self._run("arrival")
        elif name == "SupercruiseExit" and getattr(ev, "body_type", None) == "Star":
            self._event_times["drop"] = self.clock()
            self._run("smack_recovery")
        elif name == "NavRoute" and self._docked:
            # PIT-STOP resume (station-dock feature): a NEW route plotted WHILE
            # docked means the station was a pit stop, not the terminus — the
            # bot must launch and resume. Gate on a non-empty route (an empty
            # NavRoute is a clear, not a new plot). The _apply_state NavRoute
            # branch has already cached the new final waypoint; here we run the
            # resume procedure (auto-launch -> wait FsdMassLocked clear ->
            # target-next -> orient -> jump). Absent a new route, the bot stays
            # docked (terminus) — this branch simply never fires.
            nr = self._navroute_state()
            route = getattr(nr, "route", None) if nr is not None else None
            if route:
                if self.record is not None:
                    self.record("DockPitStopResume",
                                {"station": self._docked_station})
                self._run("dock_resume")

    # ---- route completion -------------------------------------------------
    def _resolve_final_waypoint(self) -> Optional[tuple[int, str]]:
        """The route's final waypoint as (system_address, star_system).

        Prefers the event-time cache (_final_waypoint, set from the NavRoute
        EVENT). When that is None — the council's MISSED-fire case: a journal
        rotation / game restart mid-route emits NO NavRoute event, so the cache
        is empty even though the route was real — fall back to the DURABLE
        NavRoute.json reader (_navroute_state, which polls the FILE that
        persists across rotation). Caches what it resolves so the catch-up read
        seeds the latch for the rest of the session.

        Returns None when neither source yields an addressed waypoint (fails
        closed at the call site)."""
        if self._final_waypoint is not None:
            return self._final_waypoint
        nr = self._navroute_state()
        route = getattr(nr, "route", None) if nr is not None else None
        if not route:
            return None
        last = route[-1]
        addr = getattr(last, "system_address", None)
        if addr is None:
            return None
        sysname = getattr(last, "star_system", None) or ""
        self._final_waypoint = (addr, sysname)   # seed for the rest of the run
        return self._final_waypoint

    def _is_route_complete(self, ev: Any) -> bool:
        """True iff this FSDJump is the arrival at the route's FINAL waypoint.

        All four conditions must hold:
        - a NavRouteClear was latched (the clear that precedes the final hop),
        - it falls within _CLEAR_JOIN_WINDOW_S of THIS jump (journal-timestamp
          correlation — a manual re-plot minutes ago won't match),
        - a final waypoint is resolvable — from the event cache OR, when the
          NavRoute event was missed (rotation/restart), the durable
          NavRoute.json reader, and
        - this jump's SystemAddress == that waypoint's (int match, never name).

        Fails closed (False) on any missing piece, so an unrecognised scene
        falls through to the normal arrival flow."""
        if not self._navroute_cleared:
            return False
        final = self._resolve_final_waypoint()
        if final is None:
            return False
        jump_ts = self._parse_journal_ts(getattr(ev, "timestamp", "") or "")
        if jump_ts is None or self._navroute_cleared_utc is None:
            return False
        gap = (jump_ts - self._navroute_cleared_utc).total_seconds()
        if not (0.0 <= gap <= _CLEAR_JOIN_WINDOW_S):
            return False
        addr = getattr(ev, "system_address", None)
        return addr is not None and addr == final[0]

    def dispatch_route_complete(self, ev: Any) -> None:
        """Terminal ROUTE COMPLETE handler. SUCCESS, not an abort — positive
        wording, no auto-restart, no retry. The live loop simply sees no
        further FSDJump after this.

        STATION destination -> run the full dock flow (approach, request, dock,
        service) and STAY DOCKED, idle. SYSTEM/star destination -> park in
        orbit and hold. (A NEW route plotted while docked later triggers the
        pit-stop resume from dispatch(); absent that, the bot stays docked.)"""
        from .steps import _destination_is_local_star

        system = (getattr(ev, "star_system", None)
                  or (self._final_waypoint[1] if self._final_waypoint else None)
                  or "destination")
        status = self._fresh_status()
        dest = getattr(status, "destination", None) if status is not None else None
        arrival_addr = getattr(ev, "system_address", None)

        # CAPTURE-AT-PLOT path: prefer the station captured at plot time (the
        # live Destination has been overwritten to the arrival system's star by
        # every TargetNextRouteSystem press along the route). Only use it when
        # it was captured in THIS arrival system (scope guard blocks a stale
        # capture from a previous route to a different station).
        captured = self._dock_target
        is_station = False
        station_name = "station"
        if (captured is not None
                and captured[0] == arrival_addr
                and captured[1] != 0
                and captured[2] and not captured[2].startswith("$")):
            is_station = True
            station_name = captured[2]
        else:
            # Legacy live-status path (unchanged): covers the edge case where
            # Status still holds a station at arrival (e.g. no target_next_route
            # ran on a single-hop route). Do NOT weaken any existing guard.
            local_star = _destination_is_local_star(status, self._current_system)
            if (dest is not None
                    and getattr(dest, "body", 0) != 0
                    and getattr(dest, "system", None) == arrival_addr
                    and local_star is False):
                is_station = True
                station_name = (getattr(dest, "name", "") or "").strip() or "station"

        if is_station:
            # Run the real dock flow (procedures/dock.toml): approach under SC
            # assist, request inside the no-fire zone, let the ADC land, service.
            if self.overlay is not None:
                try:
                    self.overlay.event(
                        f"[ROUTE COMPLETE] {station_name} — docking")
                except Exception:  # noqa: BLE001
                    pass
            if self.record is not None:
                self.record("RouteCompleteStation", {"station": station_name})
            self._run("dock")
            # TERMINUS: on a successful dock, stay docked and idle with a
            # positive line. Confirm by the live docked flag (set from the
            # Docked event in _apply_state) — fail-soft if the dock didn't
            # complete (the procedure's own [ABORTED] line already stands).
            if self._docked:
                name = self._docked_station or station_name
                if self.record is not None:
                    self.record("RouteCompleteDocked", {"station": name})
                if self.overlay is not None:
                    try:
                        self.overlay.event(
                            f"[ROUTE COMPLETE] — docked at {name}")
                        self.overlay.status(
                            f"Route complete. Docked at {name}.")
                    except Exception:  # noqa: BLE001
                        pass
            return

        # SYSTEM / star / unknown: park in orbit and hold.
        if self.overlay is not None:
            try:
                # EVENT slot = the transient announcement; STATUS slot = the
                # persistent positive idle line (distinct from the [ABORTED]
                # alarm that also lives in the STATUS slot).
                self.overlay.event(f"[ROUTE COMPLETE] {system} — parking in orbit")
                self.overlay.status(f"Route complete. Holding at {system}.")
            except Exception:  # noqa: BLE001
                pass
        if self.record is not None:
            self.record("RouteComplete", {"system": system, "type": "system"})
        self._run("route_complete_park")

    # ---- live loop --------------------------------------------------------
    def _wait_for_event(self, handle: Optional[int], event_name: str,
                        timeout_s: float) -> bool:
        """Poll this subscriber's event queue until `event_name` arrives or
        the window closes. `timeout_s` is the caller's poll cadence (steps
        pass their poll_s) — never a success/failure gate by itself."""
        if self._hub is None or handle is None:
            return True  # no tail wired (unit tests) -> proceed
        deadline = self.clock() + timeout_s
        while True:
            for ev in self._hub.poll(handle):
                if getattr(ev, "event", None) == event_name:
                    return True
            if self.clock() >= deadline:
                return False
            self.sleeper(0.2)

    def _on_tail_event(self, ev: Any) -> None:
        """Hub callback — exactly once per journal event, whichever
        subscriber's poll pumped it."""
        self._record_event_time(ev)
        self._apply_state(ev)

    def _record_event_time(self, ev: Any) -> None:
        name = getattr(ev, "event", None)
        if name == "FSDJump":
            # Staleness instrument (2026-06-07 council): lets routing and
            # diagnostics tell a fresh hyperspace arrival from an N-minute
            # loiter — both scenes read in_supercruise=true.
            self._event_times["jump"] = self.clock()
        if name == "SupercruiseExit" and getattr(ev, "body_type", None) == "Star":
            self._event_times["drop"] = self.clock()
            # Mid-procedure preemption: a star smack invalidates the scene
            # arrival/startup are flying — flag the CURRENT run to abort at
            # its next poll. The smack event itself is already queued for
            # run_live, which dispatches smack_recovery right after the
            # preempted procedure returns. (Backlog replay can't trip this:
            # no procedure runs before catch-up, so _running_proc is None.)
            if self._running_proc in _PREEMPT_ON_SMACK:
                self._preempt = "star_smack"
                if self.record is not None:
                    self.record("PreemptRequested",
                                {"procedure": self._running_proc,
                                 "reason": "star_smack"})
        # Flight-scene tracker (2026-06-06 13:41): smacked = the journal's
        # LAST supercruise transition is a star drop. Fed by backlog AND live
        # events (the tail replays from the top on attach), so a bot
        # restarted while the ship sits smacked in normal space knows it —
        # the live SupercruiseExit dispatch never fires for backlog events.
        if name == "SupercruiseExit":
            self._smacked = getattr(ev, "body_type", None) == "Star"
        elif name in ("SupercruiseEntry", "FSDJump"):
            self._smacked = False

        # Witchspace latch — SET on a Hyperspace StartJump, CLEARED on FSDJump.
        # Supercruise StartJumps (JumpType=="Supercruise") must NOT set it.
        # Belt-and-suspenders releases on SupercruiseEntry / Docked ensure a
        # missed FSDJump can never permanently wedge the interpreter pause.
        if name == "StartJump" and getattr(ev, "jump_type", None) == "Hyperspace":
            self._in_witchspace = True
        elif name in ("FSDJump", "SupercruiseEntry", "Docked"):
            self._in_witchspace = False

    def _apply_state(self, ev: Any) -> None:
        """Track per-event state for steps. Called exactly once per event via
        the hub's on_event (backlog AND live, so restarts repopulate it)."""
        name = getattr(ev, "event", None)
        if name == "FSDTarget":
            # Latest FSDTarget (+ a monotone seq) so the target_next_route
            # danger gate can verify the NEW target's StarClass.
            self._fsd_target_seq += 1
            self._latest_fsd_target = ev
        elif name == "StartJump":
            # Hyperspace-only: this StartJump's StarClass is the star the
            # ship arrives AT, i.e. the current system once FSDJump lands.
            # A supercruise StartJump carries star_class=None — ignoring it
            # (rather than clobbering) is council must-fix #3, pinned by test.
            if (getattr(ev, "jump_type", None) == "Hyperspace"
                    and getattr(ev, "star_class", None)):
                self._arrival_star_class = ev.star_class
        elif name in ("FSDJump", "Location"):
            # Current system name for the star-lock identity check
            # (2026-06-07 council: row-0 locked the Nav Beacon; the only
            # discriminator is Destination.Name vs the system name).
            sysname = getattr(ev, "star_system", None)
            if sysname:
                self._current_system = sysname
            if name == "FSDJump":
                # Stale-arrival instrument (2026-06-07 council): the FSDJump's
                # OWN ISO8601 timestamp parsed to an AWARE-UTC datetime. Works
                # for backlog AND live events because it's the event's own
                # stamp, not a replay-time clock(). _event_times["jump"] is
                # left untouched for its other consumers.
                ts = getattr(ev, "timestamp", None)
                if ts:
                    self._last_fsdjump_utc = self._parse_journal_ts(ts)
        elif name == "NavRoute":
            # A (re-)plotted route. Cache the LAST waypoint as the final
            # destination WHILE the route still exists (it's gone after the
            # NavRouteClear that precedes the final FSDJump). Re-arm: a fresh
            # plot clears any prior clear latch so a NEW route can complete.
            # (_is_route_complete also re-resolves from the durable file reader
            # when this event is missed across a rotation — see
            # _resolve_final_waypoint.)
            nr = self._navroute_state()
            route = getattr(nr, "route", None) if nr is not None else None
            if route:
                last = route[-1]
                addr = getattr(last, "system_address", None)
                sysname = getattr(last, "star_system", None)
                if addr is not None:
                    self._final_waypoint = (addr, sysname or "")
            self._navroute_cleared = False
            self._navroute_cleared_utc = None
            # CAPTURE-AT-PLOT: if Status.Destination is a named non-star body
            # right now, the operator plotted to a STATION and the game locked
            # it before we could overwrite it with TargetNextRouteSystem.
            # Reuse _dest_is_named_station (same predicate the dock decision
            # uses) to guard the capture.  A non-station (Body 0) or absent
            # status leaves _dock_target None -> park path at terminus.
            # LIVE-TEST-GATED: see __init__ comment above.
            from .steps import _dest_is_named_station as _dns
            st_cap = self._fresh_status()
            if st_cap is not None and _dns(st_cap):
                d = getattr(st_cap, "destination", None)
                self._dock_target = (
                    getattr(d, "system", None),
                    getattr(d, "body", 0),
                    (getattr(d, "name", "") or "").strip(),
                )
            else:
                # A new plot that is NOT to a station CLEARS any prior capture
                # (skeptic seat): FlowRunner is long-lived, so a stale station
                # latch would otherwise survive into a later same-system
                # system-route and wrongly trigger the dock flow. Every NavRoute
                # resets the latch to the CURRENT plot's intent.
                self._dock_target = None
        elif name == "NavRouteClear":
            # Route cleared. Latch it + its journal timestamp. This fires on the
            # final hop (in witchspace) AND on a manual re-plot — the FSDJump
            # branch in dispatch() correlates by SystemAddress + the join window
            # to tell the two apart, so we never act on the clear alone.
            self._navroute_cleared = True
            ts = getattr(ev, "timestamp", None)
            self._navroute_cleared_utc = self._parse_journal_ts(ts) if ts else None
        elif name == "DockingDenied":
            # Stash the Reason so step_dock_request (which gates via a name-only
            # event_waiter that can't read fields) can tell Distance (retry)
            # from an abort-to-human denial.
            self._docking_denied_reason = getattr(ev, "reason", None) or None
        elif name == "DockingGranted":
            # A fresh grant supersedes any stale denial reason.
            self._docking_denied_reason = None
        elif name == "Docked":
            self._docked = True
            self._docking_denied_reason = None
            self._docked_station = (getattr(ev, "station_name", "") or "").strip() or None
        elif name == "Undocked":
            self._docked = False
        elif name == "Loadout":
            cap = getattr(getattr(ev, "fuel_capacity", None), "main", None)
            if cap:
                scoop_item = next(
                    (m.item for m in getattr(ev, "modules", ())
                     if m.item.startswith("int_fuelscoop_")), None)
                rate = (scoop_max_rate_t_s(scoop_item)
                        if scoop_item is not None else None)
                self._ship_fuel = ShipFuel(capacity_t=float(cap),
                                           scoop_max_rate_t_s=rate)

    def _fsd_target_state(self) -> tuple:
        return (self._fsd_target_seq, self._latest_fsd_target)

    def _navroute_state(self) -> Optional[Any]:
        """Latest parsed NavRoute.json. poll() refreshes on mtime change and
        returns None when unchanged — fall through to .current so steps
        always see the last good parse. WIRED 2026-06-06: the reader had
        been constructed and stored since v1 with no consumer."""
        r = self.navroute_reader
        if r is None:
            return None
        nr = r.poll()
        return nr if nr is not None else r.current

    def _poll_status(self) -> None:
        with self._status_lock:
            if self.status_reader is not None:
                st = self.status_reader.poll()
                if st is not None:
                    self._latest_status = st

    # ---- heat watchdog -----------------------------------------------------
    def _heat_tick(self) -> None:
        """One watchdog tick: skip while a UI macro owns input, else poll
        status and run the reactive heatsink check."""
        if self.input_exclusive():
            return
        self._poll_status()
        self.heat_guard()

    def _heat_watchdog_loop(self, stop: threading.Event, tick_s: float = 1.0) -> None:
        """Flight-only heat protection (spec 2026-06-06): a daemon thread so
        OverHeating during long steps (alignment holds, star escapes,
        fly-outs, scooping) gets a heatsink WITHOUT waiting for the procedure
        to end. EDAPGui runs its heat/SCO monitor the same way. Exits on
        stop, panic, or stop_requested — after a panic the operator owns the
        ship, no input from us."""
        while not stop.is_set():
            if self._should_abort():
                return
            self._heat_tick()
            self.sleeper(tick_s)

    def heat_guard(self) -> None:
        """Reactive heatsink eject. Fires DeployHeatSink the moment the
        OverHeating status flag (bit 20, Heat >= 1.0) is observed, debounced
        by `heat_eject_cooldown_s` so a stuck flag can't spam the launcher.

        Caveat: OverHeating means damage has *already started* (>=1.0). A
        threshold-on-Status.Heat trigger would be cleaner, but Frontier only
        writes the Heat field above some internal cutoff so it's unreliable
        as a continuous signal. Flag-driven is good-enough for the alpha."""
        st = self._latest_status
        if st is None or not getattr(st, "overheating", False):
            return
        if (self.clock() - self._last_eject_t) < self.heat_eject_cooldown_s:
            return
        try:
            self.sender.press("DeployHeatSink")
        except KeyError:
            # Bind missing -> log once, still debounce so we don't spam-fail.
            if self.record is not None:
                self.record("HeatEjectBindMissing", {})
            self._last_eject_t = self.clock()
            return
        self._last_eject_t = self.clock()
        if self.record is not None:
            self.record("HeatEject", {"t": self._last_eject_t})

    def _is_parked_terminal(self, st: Any) -> bool:
        """Is the ship sitting at a COMPLETED route's destination, parked?

        The terminal end state dispatch_route_complete leaves behind: in
        supercruise, the NavRoute empty (no next hop), and the locked
        Destination is the local primary star — or nothing is locked at all.
        Used by _maybe_startup to idle a restart into this scene instead of
        re-running arrival. Fails closed (False) when the route still has
        waypoints, so an interrupted mid-route restart still routes to arrival.

        in_supercruise is the caller's precondition; this checks the rest."""
        from .steps import _destination_is_local_star

        # Require an AFFIRMATIVELY empty route (route == []). Anything else is
        # NOT known-empty — fail closed to arrival, since a mid-route arrival-
        # star restart looks identical to a parked one except for the live
        # route. Three non-empty cases all fail closed:
        #   - nr is None            (no reader / file unreadable -> unknown)
        #   - route is None/missing  (malformed object, attr absent -> UNKNOWN,
        #                             not "empty"; getattr-default trap fixed
        #                             2026-06-07: a falsy None must NOT pass)
        #   - route is truthy        (waypoints remain -> still mid-route)
        nr = self._navroute_state()
        route = getattr(nr, "route", None) if nr is not None else None
        if nr is None or route is None or route:
            return False
        dest = getattr(st, "destination", None)
        if dest is None:
            return True                        # nothing locked = parked/idle
        # Local primary/secondary star lock == the parked orbit target.
        return _destination_is_local_star(st, self._current_system) is True

    def _maybe_startup(self) -> None:
        if self._startup_done:
            return
        st = self._latest_status
        if st is None:
            return
        self._startup_done = True
        if getattr(st, "docked", False):
            return  # docked on load -> nothing to escape
        if getattr(st, "in_supercruise", False):
            # Restart into a COMPLETED scene (route-complete terminal-idle
            # guard, council-ratified): a bot launched while parked at the
            # destination must IDLE, not re-run arrival (which would try to
            # target a next hop that no longer exists and false-abort). The
            # parked end state is: in supercruise, NO plotted route, and the
            # locked Destination is the local primary star (or nothing locked).
            if self._is_parked_terminal(st):
                if self.record is not None:
                    self.record("RouteCompleteIdleOnRestart",
                                {"system": self._current_system})
                return
            # PROXIMITY BRANCH (2026-06-08 operator spec, Robigo incident):
            # On first launch with ship in supercruise and a route plotted,
            # decide between arrival (orbit get-around) and sc_resume
            # (throttle+orient+jump, no orbit) using a 4-priority gate.
            #
            # Gate priority (highest first):
            #   1. INDETERMINATE (dest=None or system unknown) -> arrival
            #      (fail-safe; indeterminate is never worse than today).
            #   2. Destination IS the local star -> arrival
            #      (genuine nose-on-star scene needs the orbit get-around).
            #   3. jump_age <= FRESH_ARRIVAL_WINDOW_S -> arrival
            #      SMACK GUARD: immediately after FSDJump, Status.Destination
            #      is already the NEXT route hop (different system/body),
            #      so _destination_is_local_star returns False even though
            #      the ship is physically nose-on the arrival star. The 30s
            #      window was operator-confirmed by Operator (2026-06-08) as a
            #      DELIBERATE EXCEPTION to the [[no-arbitrary-timed-waits]]
            #      rule: this is a CLASSIFIER heuristic only, not a
            #      success/failure gate; the wall-clock span is bounded by
            #      the ED FSDJump-to-scene-stable window and cannot be
            #      replaced by a journal event (the signal we need — "am I
            #      nose-on the star" — is overwritten by ED before we read it).
            #   4. OTHERWISE (jump_age > 30s AND confident non-local-star lock)
            #      -> sc_resume (fast path: Robigo loiter, named station, etc.)
            #
            # FRESH_ARRIVAL_WINDOW_S: operator-confirmed override of the
            # no-arbitrary-timed-waits rule FOR THIS CLASSIFIER ONLY.
            FRESH_ARRIVAL_WINDOW_S = 30.0

            from .steps import _destination_is_local_star
            dest = getattr(st, "destination", None)
            near_star = _destination_is_local_star(st, self._current_system)

            # Priority 1: indeterminate (no destination read / unknown system)
            if near_star is None or dest is None:
                if self.record is not None:
                    self.record("ArrivalOnRestart",
                                {"system": self._current_system,
                                 "near_star": None,
                                 "reason": "indeterminate"})
                self._run("arrival")
                return

            # Priority 2: destination IS the local star -> orbit needed
            if near_star is True:
                if self.record is not None:
                    self.record("ArrivalOnRestart",
                                {"system": self._current_system,
                                 "near_star": True,
                                 "reason": "local_star"})
                self._run("arrival")
                return

            # Priority 3: fresh arrival (smack guard) — even a confident
            # non-local-star dest is unreliable within FRESH_ARRIVAL_WINDOW_S
            # because ED pre-loads the NEXT hop before the scene has settled.
            jump_age = self._jump_age()
            if jump_age is None or jump_age <= FRESH_ARRIVAL_WINDOW_S:
                if self.record is not None:
                    self.record("ArrivalOnRestart",
                                {"system": self._current_system,
                                 "near_star": False,
                                 "reason": "fresh_arrival",
                                 "jump_age": jump_age})
                self._run("arrival")
                return

            # Priority 4: stale loiter with a confident non-local-star lock
            # (jump_age > FRESH_ARRIVAL_WINDOW_S): fast resume path.
            # No nav_panel_target, no sc_assist_orbit.
            if self.record is not None:
                self.record("ScResumeOnRestart",
                            {"system": self._current_system,
                             "reason": "not_local_star",
                             "jump_age": jump_age})
            self._run("sc_resume")
            return
        if self._smacked and getattr(st, "fsd_cooldown", False):
            # Restart while SMACKED (normal space, last SC transition was a
            # star drop, FSD cooldown STILL burning): smack_recovery owns
            # this state — startup's throttle-100 + glare-blind orient is
            # the 13:26 dive all over again, and the SupercruiseExit that
            # would dispatch the reflex live is backlog here, never
            # dispatched.
            #
            # COOLDOWN GATE (2026-06-07 10:05 false positive): a manual drop
            # near a star writes a journal-identical SupercruiseExit
            # BodyType=Star — the operator parked 8 Ls out, launched the
            # bot, and the backlog routed it to smack_recovery in a clean
            # scene. The FsdCooldown flag is the only live discriminator:
            # a real exclusion-zone drop imposes ~40s of cooldown, a normal
            # drop ~5s (gone by boot). A stale smack falls through to
            # startup, whose recovery lane runs the same star-astern escape.
            self._run("smack_recovery")
            return
        # EMPTY-ROUTE GUARD (2026-06-08 council, Wolf 359 fresh-login defect):
        # a normal-space fresh login with NO plotted route fell through to
        # startup, which flailed against a non-existent route — target_next_route
        # spun the full 60s watchdog (no FSDTarget ever fires with no next hop)
        # and the recovery lane orbited a star it had nowhere to jump from.
        # _is_parked_terminal (above) only covers the in-supercruise case; the
        # normal-space empty-route login is the gap. Reuse the same affirmative
        # route read (L829-831) but the OPPOSITE safe default: empty/absent/
        # unknown route -> do NOT fly. Clean abort (council-decided over idle-
        # with-rearm: re-arm would dispatch startup mid-session into a drifted
        # scene — untested, against "don't regress the jump loop"). The operator
        # plots a route and relaunches; the gap is signalled on the overlay.
        # This is NOT stop_requested — returning keeps the heat watchdog alive;
        # _startup_done is already True (above) so _maybe_startup never re-fires.
        # `not route` collapses both route=None and route=[] into "block" —
        # the safe direction (fail closed). A len>=2 route falls through to _run.
        nr = self._navroute_state()
        route = getattr(nr, "route", None) if nr is not None else None
        if not route:                          # None / absent / [] -> no onward hop
            if self.record is not None:
                self.record("NoRouteOnStartup", {"system": self._current_system})
            if self.overlay is not None:
                try:
                    self.overlay.event("[NO ROUTE] Plot a route and relaunch")
                    self.overlay.status("No route plotted. Idle.")
                except Exception:              # noqa: BLE001 — overlay is fail-soft
                    pass
            return
        self._run("startup")

    def request_stop(self) -> None:
        self.stop_requested = True

    def run_live(self, *, duration_s: float, poll_interval_s: float = 0.5) -> None:
        if self.tail is None or self._hub is None:
            raise RuntimeError("run_live requires a journal tail")
        main_handle = self._hub.subscribe()
        # Heat protection lives on its own thread (covers long steps too);
        # the inline heat_guard call is gone — single owner, no double-fire.
        watchdog_stop = threading.Event()
        watchdog = threading.Thread(
            target=self._heat_watchdog_loop, args=(watchdog_stop,), daemon=True)
        watchdog.start()
        deadline = self.clock() + duration_s
        try:
            while not self.stop_requested and self.clock() < deadline:
                if self.panic_switch is not None and getattr(self.panic_switch, "tripped", False):
                    break
                self._poll_status()
                # Events pumped by in-procedure waiters land in this queue
                # too, so an FSDJump arriving DURING a procedure dispatches
                # right after it returns (previously a waiter swallowed it
                # and the arrival flow never ran).
                events = self._hub.poll(main_handle)
                if not events:
                    self._caught_up = True
                    self._maybe_startup()
                    self.sleeper(poll_interval_s)
                    continue
                for ev in events:
                    if self._caught_up:
                        self.dispatch(ev)
        finally:
            watchdog_stop.set()
            self._hub.unsubscribe(main_handle)
