"""Map live journal events to procedures, run them through the interpreter,
launch the parallel honk track, and own the live tail/status loop.

ENGINE ONLY -- the jump classifier, event routes, and domain-specific routing
live in ed_autojump.flow.boot_routes (registered into this engine via the
surface #1 and #2 registry in ed_core.flow.registry).

Replaces the orchestrator's escape/engage handlers. Replay (catch-up) events
only update state; actions fire only once caught up to LIVE."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ed_core.flow.context import ShipFuel, StepContext
from ed_core.flow.registry import run_classifiers, run_event_routes
from ed_core.flow.interpreter import run_procedure
from ed_core.flow.model import Procedure
from ed_core.flow.predicates import _dest_is_named_station, _destination_is_local_star
from ed_core.fsd_util import scoop_max_rate_t_s as _scoop_max_rate_t_s

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
# "traversal" JOINED 2026-07-07 (D2/C4, never-strand council): the steady-state
# A->B hop flies a live supercruise scene toward the next jump exactly like
# arrival/startup/dock/sc_resume, so a star/planet drop mid-traversal must
# preempt it the same way — closing the live dead-end where traversal's
# EngageJumpClearanceObscured abort left a stale drop stranded with no
# handoff to smack_recovery.
_PREEMPT_ON_SMACK = frozenset({"arrival", "startup", "dock", "sc_resume", "traversal"})

# Route-complete correlation join window. NavRouteClear fires in witchspace
# ~10s before the destination FSDJump; this is the max gap (in JOURNAL
# timestamps, not wall clock) we accept between that clear and the arriving
# FSDJump before treating the clear as belonging to a different event (e.g. a
# manual re-plot minutes earlier). This is a CORRELATION window between two
# journal events — NOT a wall-clock success/failure gate (house rule).
_CLEAR_JOIN_WINDOW_S = 60.0

# NEVER-STRAND re-dispatch bounded backoff (workstream A, 2026-07-07 council).
# Monotonic: base * 2**(attempts-1), capped. Module-level tuning constants
# (not a config surface -- no operator knob exists or is needed; this is an
# internal safety-net cadence, same tier as _CLEAR_JOIN_WINDOW_S above).
_REDISPATCH_BACKOFF_BASE_S = 2.0
_REDISPATCH_BACKOFF_CAP_S = 30.0


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
            self._pump_locked()
            pending = self._queues.get(handle)
            if pending is None:
                return []
            self._queues[handle] = []
            return pending

    def pump(self) -> None:
        """Pump the tail once and broadcast — no handle, nothing consumed
        from any queue. Lets should_abort() deliver preempt-class journal
        events (Star drop) MID-STEP: before this, events were only consumed
        inside wait-steps' event_waiter or by a live honk track, so a smack
        during a sleeper-only step sat unread while the scene kept flying
        the smacked ship (live 2026-07-11 23:43, session 234324)."""
        with self._lock:
            self._pump_locked()

    def _pump_locked(self) -> None:
        for ev in self._tail.step():
            if self._on_event is not None:
                self._on_event(ev)
            for q in self._queues.values():
                q.append(ev)


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
        body_tour_enabled: bool = False,
        body_tour_dwell_s: float = 2.0,
        body_tour_max_bodies: int = 5,
        body_tour_max_rows: int = 8,
        body_tour_orbit_timeout_s: float = 120.0,
        body_tour_min_bodies: int = 0,
        nav_panel_reader: Optional[Any] = None,
        nav_panel_grabber: Optional[Callable[[], Any]] = None,
        station_menu_grabber: Optional[Callable[[], Any]] = None,
        navpanel_icon_grabber: Optional[Callable[[], Any]] = None,
        # CV-action family (#3/#4/#5/#6) full-frame .grab; ONE grab wired to both
        # (each consumer crops its own region). None -> blind/unreadable fallback.
        navpanel_detail_grabber: Optional[Callable[[], Any]] = None,
        navpanel_frame_grabber: Optional[Callable[[], Any]] = None,
        # SC-assist HUD prompt reads (#17): full-frame grab, panel closed.
        hud_grabber: Optional[Callable[[], Any]] = None,
        # Council B docking: right-side target-panel km read (float|None per
        # call). None -> steps see the fail-closed lambda: None default.
        dock_distance_km_supplier: Optional[Callable[[], Optional[float]]] = None,
        visited_logger: Optional[Any] = None,
        scoop_rate_fn: Optional[Callable[[str], Optional[float]]] = None,
        dest_is_named_station_fn: Optional[Callable[[Any], bool]] = None,
        # Escape-vector CV frame grabber (D2/C3, never-strand council 2026-07-07):
        # a FULL-frame BGR grab, wired exactly like navpanel_icon_grabber. Read by
        # boot_routes._route_sc_exit to STEER (never gate) the smack body-kind
        # (_smack_kind). None (default/unwired) -> the steer is skipped; recovery
        # ALWAYS dispatches regardless (C2 repeal of INV1/INV2 -- see _route_sc_exit).
        escape_vector_grabber: Optional[Callable[[], Any]] = None,
        # Never-strand re-dispatch driver (workstream A, 2026-07-07 council).
        # (runner) -> None domain hook, invoked by run_live's _maybe_redispatch
        # when a required-fail abort queued a redispatch and the bounded-backoff
        # window elapsed. Wired by the CLI host at construction time (same pattern
        # as every other domain-injected callable here — no core->domain import).
        # None (unwired -- unit tests / no domain activate()) degrades never-strand
        # to a LOUD bounded idle: the backoff/attempt mechanics still run and are
        # testable via an injected fake driver, but nothing actually re-dispatches.
        redispatch_driver: Optional[Callable[["FlowRunner"], None]] = None,
        # OPERATOR RULING 2026-07-11: re-assert ED window foreground at every
        # error state (retry/abort via ctx.focus_reassert, strand-guard
        # redispatch here) — never before every keypress ("overkill"). cli
        # wires launcher.focus.focus_ed_window when --engage-keys.
        focus_reassert: Optional[Callable[[], bool]] = None,
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
        self._body_tour_enabled = body_tour_enabled
        self._body_tour_dwell_s = body_tour_dwell_s
        self._body_tour_max_bodies = body_tour_max_bodies
        self._body_tour_max_rows = body_tour_max_rows
        self._body_tour_orbit_timeout_s = body_tour_orbit_timeout_s
        self._body_tour_min_bodies = body_tour_min_bodies
        self.nav_panel_reader = nav_panel_reader      # identity targeting (task #45)
        self.nav_panel_grabber = nav_panel_grabber
        # Docked-menu CV (full-frame grab): auto_launch safety gate + the
        # services-macro menu-up entry gate read the menu through this.
        self.station_menu_grabber = station_menu_grabber
        # CV-action family full-frame grabbers (#3/#4/#5/#6) — see StepContext.
        self.navpanel_detail_grabber = navpanel_detail_grabber
        self.navpanel_frame_grabber = navpanel_frame_grabber
        self.hud_grabber = hud_grabber
        self.dock_distance_km_supplier = dock_distance_km_supplier
        # Append-only log of systems visited on live FSDJump arrivals. Pure
        # observability: never touches a condition/action and is fail-soft on
        # write, so it can't disturb the flight loop. None == disabled.
        self._visited_logger = visited_logger
        # Optional domain-provided scoop rate lookup fn.
        # Injected by activate() so the engine never imports a domain module.
        self._scoop_rate_fn = scoop_rate_fn
        # Optional domain-provided station predicate (captures at plot time).
        # Same injection pattern as scoop_rate_fn -- no core->domain import.
        self._dest_is_named_station_fn = dest_is_named_station_fn

        self._event_times: dict[str, float] = {}
        # True while the journal's last SC transition is SupercruiseExit at a
        # massive body (Star OR Planet — widened from Star-only by BUG C fix).
        # Two-stage design: wide preempt (Star+Planet scene-abort) + narrow
        # CV-gated recovery (_route_sc_exit fail-closed). A benign planet drop
        # that preempts a live scene does NOT trigger smack_recovery — re-dispatch
        # passes through the CV gate which abstains on a 'none' token.
        # Downstream consumers (STARSMACK scene + smack_recovery dispatch) MUST
        # re-confirm via the CV gate (_route_sc_exit) before recovering — this
        # latch alone is NOT an authorization to run smack_recovery.
        self._smacked = False
        # CV-confirmed smack body kind set by _route_sc_exit after a positive
        # detect_escape_vector result. None = not yet confirmed / cold start.
        # Read by build_determine_context (boot_routes) and smack_recovery steps.
        self._smack_kind = None
        # Injected escape-vector frame grabber (Optional[Callable[[], Any]]).
        # Wired exactly like frame_grabber / station_menu_grabber: set at
        # construction time or by activate(); UNWIRED (None) by default so
        # _route_sc_exit abstains until the operator calibrates the CV (INV2).
        # Exposed as a runner attribute so boot_routes._route_sc_exit can read
        # it without importing a domain module into the core engine.
        # WIRED 2026-07-07 (D2/C3): now a constructor param (was hardcoded
        # None) -- cli.py composes a full-frame BGR grab exactly like the
        # navpanel grabbers, NOT gated on WinRT OCR (escape-vector CV is
        # pixel/color, not text).
        self._escape_vector_grabber = escape_vector_grabber
        # Injected nav-panel ICON frame grabber (Optional[Callable[[], Any]]).
        # Wired exactly like _escape_vector_grabber: a FULL-frame BGR grab with
        # the nav panel OPEN and the locked destination highlighted, read by
        # boot_routes.dispatch_route_complete to confirm the destination's body
        # KIND by its column-0 icon (star vs station) -- the authoritative signal
        # the name heuristics only approximate. None -> route-complete falls back
        # to the name heuristics (today's dock, no regression).
        #
        # WIRED 2026-06-22: cli.py composes capture.build_navpanel_icon_grabber
        # (bare full-frame grab) with executor.navpanel.grab_navpanel_frame (the
        # open-panel/close keystroke wrapper) and passes it here on a keyed live
        # run. Still None when keys are off or capture is unavailable.
        self._navpanel_icon_grabber = navpanel_icon_grabber
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
        # OPERATOR WIRE-IN 2026-07-06: a step saw ALIGN WITH ESCAPE VECTOR
        # (boot-smack state). Latched by ctx.escape_vector_notify; consumed by
        # boot_routes' startup override, which hands off to smack_recovery.
        self._escape_vector_seen: bool = False
        # NEVER-STRAND re-dispatch (workstream A, 2026-07-07 council). A
        # required-step exhaustion that is NEITHER an operator-abort NOR a
        # preempt (see _run's three-way disambiguation) queues a re-dispatch
        # here instead of idling forever ([ABORTED] used to be terminal).
        # run_live's own loop iteration drives the actual re-dispatch call
        # (never nested recursion inside _run) — see _maybe_redispatch.
        self._needs_redispatch: bool = False
        # Attempt counter for the monotonic bounded backoff (base * 2**(n-1),
        # capped). Reset to 0 ONLY on a COMPLETED procedure (a clean run) —
        # NOT on every queue, so a still-unresolved strand keeps backing off
        # across repeated attempts instead of hot-looping at the base delay.
        self._redispatch_attempts: int = 0
        # Monotonic clock() deadline for the NEXT redispatch attempt. 0.0
        # (the __init__ default) is <= any real clock() reading, so a FRESH
        # strand's first attempt fires on run_live's very next idle check —
        # no added delay beyond the poll cadence (no-idling law, L2).
        self._redispatch_next_t: float = 0.0
        self._redispatch_driver = redispatch_driver
        self.focus_reassert = focus_reassert
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
        # body_tour latches (set in _apply_state, exposed via _make_context).
        # _autoscan_bodies / _autoscan_seq / _fss_discovered are SYSTEM-SCOPED
        # (reset on FSDJump). _drop_seq / _scex_seq are monotone SESSION
        # counters compared only against a same-loop snapshot — resetting them
        # is unnecessary and a mid-arrival reset cannot occur during a SC-only
        # tour (matches _fsd_target_seq, which also never resets).
        self._autoscan_bodies: set[str] = set()
        self._autoscan_seq = 0          # monotone, mirrors _fsd_target_seq (D5)
        self._fss_discovered = False    # D4
        self._fss_body_count = 0        # FSSDiscoveryScan BodyCount (min-bodies gate)
        self._drop_seq = 0              # SupercruiseDestinationDrop counter (PD1)
        self._scex_seq = 0              # SupercruiseExit counter (PD7)
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
        # Current ship model — lowercase internal name as emitted by the journal
        # "Ship" field of LoadGame and Loadout events (e.g. "mandalay", "type9").
        # Feeds dock-pitch duration via ship_sizes.pitch_s_for_ship.
        self._current_ship: Optional[str] = None
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
        # D5 same-system replot guard (boot_routes._route_nav_route): the
        # system_address of the station where the ship is docked. Sourced from
        # the Docked event's SystemAddress field (Optional[int]). None when not
        # docked or when the Docked event lacked the field (fleet carrier / edge
        # mode). The guard in _route_nav_route fails OPEN on None -- today's
        # relaunch behavior as the safe default.
        self._docked_system_addr: Optional[int] = None
        # True once the station has broadcast "$STATION_NoFireZone_entered;"
        # via ReceiveText — the live-verified (2026-06-07 operator journal)
        # signal that the ship is inside the 7.5km docking request range.
        # Cleared by _clear_no_fire_zone at the start of each dock_approach
        # run so the flag only holds for THE CURRENT approach leg.
        self._no_fire_zone_entered: bool = False
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
        # ROUTE-COMPLETE SETTLE re-poll (live-proven 2026-06-09): at a real
        # station terminus the game auto-targets the terminus station into
        # Status.Destination ~1.09s AFTER the FSDJump (measured: TRC Holding
        # Inc., Body=29, appeared 1.09s post-jump and stayed through docking).
        # The legacy one-shot read at the FSDJump instant therefore sees only
        # the arrival STAR (Body=0) and wrongly parks. These bound a short
        # re-poll of Destination so the station flag can resolve. The CAP is a
        # ceiling on the wait, NEVER the success signal (the Destination flag
        # decides success); on cap-exhaust we fall back to today's park path.
        self._route_complete_settle_s = 2.0       # ceiling (live flip <=1.09s)
        self._route_complete_settle_poll_s = 0.25  # re-read cadence

    # ---- public state accessors ------------------------------------------
    def event_time(self, name: str) -> Optional[float]:
        return self._event_times.get(name)

    def _dock_target_name(self) -> Optional[str]:
        """The station NAME the dock flow should re-acquire by name (Q2).

        Prefers the capture-at-plot name (_dock_target[2]) -- the live
        Destination has been overwritten to the arrival star by every
        TargetNextRouteSystem along the route, so the captured name is the
        durable identity. Falls back to the live Destination.Name (the settle
        loop resolves it ~1.09s post-jump). None when neither is a usable,
        non-symbolic name -> step_dock_target_station takes its legacy walk."""
        cap = self._dock_target
        if cap is not None:
            name = (cap[2] or "").strip()
            if name and not name.startswith("$"):
                return name
        st = self._latest_status
        dest = getattr(st, "destination", None) if st is not None else None
        name = (getattr(dest, "name", "") or "").strip() if dest is not None else ""
        if name and not name.startswith("$"):
            return name
        return None

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
        — cooperative, key-release-safe, no thread killing.

        HUB PUMP FIRST (operator "ship them" 2026-07-11): journal events used
        to be consumed only inside wait-steps' event_waiter or by a live honk
        track — a SupercruiseExit@Star during a sleeper-only step (orient) sat
        UNREAD in the tail while sc_resume kept flying the smacked ship (live
        23:43:43, session 234324; the D2 always-recover route never saw the
        event). Pumping here makes THIS poll the delivery point: the event
        routes through _on_tail_event -> _record_event_time -> _preempt, and
        the very same call returns True. run_live's queued routing (e.g.
        _route_sc_exit) still fires after the preempted scene returns —
        pumping only moves lines from the file into the queues."""
        if self._hub is not None:
            try:
                self._hub.pump()
            except Exception:  # noqa: BLE001 — pump is best-effort, never fatal
                pass
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

    def _clear_no_fire_zone(self) -> None:
        """Reset the no-fire-zone entry flag. step_dock_approach calls this on
        entry so a stale True from a prior approach (the ship was already in
        range when the step ran on a restart) cannot skip the closing leg."""
        self._no_fire_zone_entered = False

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
            body_tour_enabled=self._body_tour_enabled,
            body_tour_dwell_s=self._body_tour_dwell_s,
            body_tour_max_bodies=self._body_tour_max_bodies,
            body_tour_max_rows=self._body_tour_max_rows,
            body_tour_orbit_timeout_s=self._body_tour_orbit_timeout_s,
            body_tour_min_bodies=self._body_tour_min_bodies,
            nav_panel_reader=self.nav_panel_reader,
            nav_panel_grabber=self.nav_panel_grabber,
            # Name-driven dock re-target (Q2): the captured station name, else
            # the live Destination's name. step_dock_target_station matches it
            # against the OCR'd nav panel to target the station's row by name.
            dock_target_name_supplier=self._dock_target_name,
            station_menu_grabber=self.station_menu_grabber,
            # CV-action family (#3/#4/#5/#6): the pre-press #8 label confirm
            # (detail) + the nav-list read (frame). One full-frame grab, both.
            navpanel_detail_grabber=self.navpanel_detail_grabber,
            navpanel_frame_grabber=self.navpanel_frame_grabber,
            hud_grabber=self.hud_grabber,
            # <7.5km docking gate read; unwired -> fail-closed default.
            dock_distance_km_supplier=(self.dock_distance_km_supplier
                                       or (lambda: None)),
            # Current ship model (journal LoadGame/Loadout latch) — feeds the
            # dock blind-maneuver's ship-size pitch duration.
            ship_supplier=lambda: self._current_ship,
            fss_body_count_supplier=lambda: self._fss_body_count,
            fss_discovered_supplier=lambda: self._fss_discovered,
            autoscan_supplier=lambda: (self._autoscan_seq,
                                       frozenset(self._autoscan_bodies)),
            drop_seq_supplier=lambda: self._drop_seq,
            scex_seq_supplier=lambda: self._scex_seq,
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
            no_fire_zone_supplier=lambda: self._no_fire_zone_entered,
            clear_no_fire_zone=self._clear_no_fire_zone,
            in_witchspace=lambda: self._in_witchspace,
            record=self.record,
            frame_sink=self.frame_sink,
            focus_reassert=self.focus_reassert,
        )
        ctx._tail_handle = handle   # for _run's unsubscribe; None without a tail

        def _escape_vector_notify() -> None:
            # OPERATOR WIRE-IN 2026-07-06: ALIGN WITH ESCAPE VECTOR seen mid-
            # step = smack state. Latch for the boot-override consumer AND
            # preempt the running procedure at its next abort poll — a scene
            # handoff ([PREEMPTED]), never retry flapping in a gravity well.
            self._escape_vector_seen = True
            self._preempt = "escape_vector"
        ctx.escape_vector_notify = _escape_vector_notify
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
        try:
            for track_name in proc.parallel_tracks:
                track = self.procedures.get(track_name)
                if track is None:
                    continue
                # FULLY DETACHED (#26, operator-ratified): the parent never
                # joins a parallel track — the main scene hands off to its
                # successor the instant it finishes, honk still charging or
                # not. The track ctx is deliberately NOT in `ctxs`: the track
                # thread owns its OWN tail unsubscribe (in _run_track) so the
                # parent's finally can't blind a still-holding honk mid-event
                # (which would push the release to the 30s hold backstop).
                # Threads are daemon: process exit reaps them.
                track_ctx = self._make_context()
                threading.Thread(
                    target=self._run_track, args=(track, track_ctx), daemon=True
                ).start()
            result = run_procedure(proc, ctx)
            # THREE-WAY DISAMBIGUATION (workstream A, 2026-07-07 council) on a
            # required-step exhaustion (result.aborted). Order is LOAD-BEARING:
            # preempt is checked FIRST (a preempt can coincide with a stale
            # _should_abort read on a slower thread), then operator-abort,
            # and ONLY THEN does an abort mean "never-strand, queue a
            # re-dispatch" — the prior behavior (a terminal [ABORTED] idle)
            # is what let a ship sit stranded at a star forever (interpreter.py
            # required-fail -> here -> run_live only polls, nothing re-fires).
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
                # ASCII-only console text (operator order 2026-07-06: the
                # em-dash rendered as "â€”" garbage on the PS console).
                msg = f"[PREEMPTED] {name} -- {self._preempt}"
                print(msg, flush=True)
                if self.overlay is not None:
                    try:
                        self.overlay.event(msg)
                    except Exception:  # noqa: BLE001
                        pass
            elif result.aborted and self._should_abort():
                # TERMINAL OPERATOR STOP (panic / stop_requested): unchanged
                # from before — human eyes required, NO auto-restart, NO
                # retry, NO re-dispatch. Name the failing step when there is
                # one, but guard the case where the last step may not be the
                # failer (or there are no steps at all).
                msg = f"[ABORTED] {name} -- manual intervention needed"
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
            elif result.aborted:
                # NEVER-STRAND (workstream A): a required-fail exhaustion that
                # is NEITHER a preempt NOR an operator-abort. Queue a
                # re-dispatch from LIVE state instead of the old terminal
                # [ABORTED] idle — run_live's own loop iteration drives the
                # actual re-dispatch call under bounded backoff (never nested
                # recursion inside _run; see _maybe_redispatch). LOUD: this is
                # exactly the class of incident (a ship idling silently at a
                # star, e.g. nav_supercruise_star refuse / engage_jump_clearance
                # obscured / target_next_route watchdog / engage_supercruise
                # no-charge / dock deny / smack_recovery internal fail) that
                # used to strand silently.
                self._needs_redispatch = True
                msg = f"[STRAND-GUARD] {name} exhausted retries -- queuing re-dispatch from live state"
                if result.steps:
                    msg += f" (failed at {result.steps[-1].action})"
                print(msg, flush=True)
                if self.overlay is not None:
                    try:
                        self.overlay.event(msg)
                        self.overlay.status(msg)   # persistent: stays visible
                    except Exception:  # noqa: BLE001
                        pass
                if self.record is not None:
                    self.record("RedispatchQueued",
                                {"procedure": name,
                                 "failed_at": (result.steps[-1].action
                                              if result.steps else None),
                                 "retries": result.retries})
            else:
                # COMPLETED cleanly: reset the backoff ladder so a LATER,
                # unrelated strand starts its own attempt count from zero
                # rather than inheriting a prior incident's climbed backoff.
                self._redispatch_attempts = 0
                self._needs_redispatch = False
        finally:
            self._running_proc = None
            if self._preempt is not None and self.record is not None:
                self.record("Preempted", {"procedure": name,
                                          "reason": self._preempt})
            # Drop the MAIN ctx's subscription (detached tracks are not in
            # `ctxs` — each unsubscribes itself in _run_track when it ends, so
            # a still-running honk keeps seeing its release event).
            if self._hub is not None:
                for c in ctxs:
                    h = getattr(c, "_tail_handle", None)
                    if h is not None:
                        self._hub.unsubscribe(h)

    def _run_track(self, track: Any, track_ctx: StepContext) -> None:
        """Detached parallel-track runner (#26): run the track to completion on
        its own daemon thread, then release its tail subscription. The parent
        scene never waits on this — a leaked queue is prevented HERE, by the
        track itself, instead of by the parent's join-then-unsubscribe."""
        try:
            run_procedure(track, track_ctx)
        finally:
            if self._hub is not None:
                h = getattr(track_ctx, "_tail_handle", None)
                if h is not None:
                    self._hub.unsubscribe(h)

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
            # NEW-SYSTEM PREEMPT (Phroea Eaec IB-N d7-8 ram, live 2026-07-12,
            # session 031503). A live FSDJump means the ship is now in a NEW
            # system: every scene the running procedure was flying is obsolete
            # and BLIND against the new arrival star. Flag the current run to
            # abort at its next poll so run_live can dispatch _route_fsd_jump
            # (the new system's arrival) instead of the stale scene grinding on
            # -- the ram was traversal's engage-retry re-issuing SetSpeed100 4s
            # AFTER the FSD-charge-stuck false-negative "lost" jump had actually
            # committed and dropped the ship nose-on the new star. Unlike the
            # star_smack preempt (gated on _PREEMPT_ON_SMACK), a new system
            # obsoletes EVERY scene including smack_recovery -- once it jumps
            # out, arrival owns the arrival. Backlog-safe: no procedure runs
            # before catch-up, so _running_proc is None then (the same guard the
            # star_smack preempt relies on) and a replayed jump never preempts.
            if self._running_proc is not None:
                self._preempt = "new_system"
                if self.record is not None:
                    self.record("PreemptRequested",
                                {"procedure": self._running_proc,
                                 "reason": "new_system"})
        # BUG C fix: widen preempt to Star OR Planet (INV5 — planet-smack coverage).
        # TWO-STAGE DESIGN: this preempt is CONSERVATIVE-WIDE (aborts the current
        # scene on ANY Star/Planet drop, including deliberate). The subsequent
        # re-dispatch goes through the CV-gated _route_sc_exit, which is the
        # fail-closed decision point — a benign drop that preempts a scene does
        # NOT result in smack_recovery (the CV abstains on a 'none' token).
        # Never mistake the wide preempt for the recovery authorization.
        if name == "SupercruiseExit" and getattr(ev, "body_type", None) in ("Star", "Planet"):
            # Mid-procedure preemption: a smack (Star or Planet) invalidates the
            # scene arrival/startup are flying. Flag the CURRENT run to abort at
            # its next poll. The smack event itself is already queued for run_live,
            # which dispatches _route_sc_exit right after the preempted proc returns.
            # (Backlog replay can't trip this: no procedure runs before catch-up,
            # so _running_proc is None.)
            if self._running_proc in _PREEMPT_ON_SMACK:
                self._preempt = "star_smack"
                if self.record is not None:
                    self.record("PreemptRequested",
                                {"procedure": self._running_proc,
                                 "reason": "star_smack"})
        # Flight-scene tracker: smacked = the journal's LAST supercruise transition
        # is a drop at a massive body (Star OR Planet — widened from Star-only).
        # Fed by backlog AND live events (tail replays from top on attach), so a
        # bot restarted while sitting smacked in normal space knows it.
        # NOTE: _smacked = True alone does NOT authorize smack_recovery; downstream
        # consumers MUST re-confirm via the CV gate in _route_sc_exit (INV1).
        # Clear _smack_kind on any SC transition so a stale CV result from a prior
        # drop does not bleed into a fresh restart.
        if name == "SupercruiseExit":
            self._smacked = getattr(ev, "body_type", None) in ("Star", "Planet")
            self._smack_kind = None  # cleared; re-confirmed by _route_sc_exit CV gate
        elif name in ("SupercruiseEntry", "FSDJump"):
            self._smacked = False
            self._smack_kind = None
        elif name == "Location" and getattr(ev, "docked", False):
            # Respawn/restart repair (GATEWALK gap #1, the Tortooga incident):
            # a death/rebuy respawn emits Location(Docked=true) with NO
            # Undocked/FSDJump in between, so the pre-death smack latch
            # survived into a docked scene and startup routed wrong.
            self._smacked = False
            self._smack_kind = None

        # Witchspace latch — SET on a Hyperspace StartJump, CLEARED on FSDJump.
        # Supercruise StartJumps (JumpType=="Supercruise") must NOT set it.
        # Belt-and-suspenders releases on SupercruiseEntry / Docked / Location
        # ensure a missed FSDJump can never permanently wedge the interpreter
        # pause. LOCATION added 2026-07-12 (live wedge, session 040709): a game
        # QUIT mid-hyperspace leaves a Hyperspace StartJump with NO FSDJump in
        # the backlog; on relaunch ED emits LoadGame + Location (ship reverted to
        # its last stable system) but neither FSDJump/SC-entry/Docked ever fires,
        # so the latch stayed True and startup WitchspacePaused every step — the
        # bot never took its first action. Location is ED's authoritative
        # post-load real-space position event, never emitted inside the tunnel.
        if name == "StartJump" and getattr(ev, "jump_type", None) == "Hyperspace":
            self._in_witchspace = True
        elif name in ("FSDJump", "SupercruiseEntry", "Docked", "Location"):
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
            if name == "Location" and getattr(ev, "docked", False):
                # Respawn/restart world-state repair (GATEWALK gap #1): a
                # rebuy respawn puts the ship ON A PAD with no Docked event —
                # Location(Docked=true) is the only signal. Without this the
                # docked flag stays stale-False and the pit-stop resume
                # trigger (NavRoute-while-docked) never arms.
                self._docked = True
                self._docked_station = (
                    (getattr(ev, "station_name", "") or "").strip() or None)
                self._docked_system_addr = getattr(ev, "system_address", None)  # D5 guard
            if name == "FSDJump":
                # Stale-arrival instrument (2026-06-07 council): the FSDJump's
                # OWN ISO8601 timestamp parsed to an AWARE-UTC datetime. Works
                # for backlog AND live events because it's the event's own
                # stamp, not a replay-time clock(). _event_times["jump"] is
                # left untouched for its other consumers.
                ts = getattr(ev, "timestamp", None)
                if ts:
                    self._last_fsdjump_utc = self._parse_journal_ts(ts)
                # body_tour per-system reset (D5): a new system has its own
                # body list + its own honk. _drop_seq / _scex_seq are NOT
                # reset (monotone session counters, see __init__).
                self._autoscan_bodies = set()
                self._autoscan_seq = 0
                self._fss_discovered = False
                self._fss_body_count = 0
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
            st_cap = self._fresh_status()
            _dns = self._dest_is_named_station_fn or _dest_is_named_station
            if st_cap is not None and _dns is not None and _dns(st_cap):
                d = getattr(st_cap, "destination", None)
                self._dock_target = (
                    getattr(d, "system", None),
                    getattr(d, "body", 0),
                    (getattr(d, "name", "") or "").strip(),
                )
                # TRACE (gate-walk): make capture-at-plot observable — did the
                # station actually win the read at the NavRoute instant?
                if self.record is not None:
                    self.record("DockTargetCaptured", {
                        "name": self._dock_target[2],
                        "body": self._dock_target[1],
                        "system": self._dock_target[0]})
            else:
                # A new plot that is NOT to a station CLEARS any prior capture
                # (skeptic seat): FlowRunner is long-lived, so a stale station
                # latch would otherwise survive into a later same-system
                # system-route and wrongly trigger the dock flow. Every NavRoute
                # resets the latch to the CURRENT plot's intent.
                self._dock_target = None
                # TRACE (gate-walk): log WHAT the live Destination was so we can
                # see WHY the capture missed (expecting star Body=0 at plot time).
                if self.record is not None:
                    _dm = getattr(st_cap, "destination", None) if st_cap is not None else None
                    self.record("DockTargetMissed", {
                        "dest_name": getattr(_dm, "name", None),
                        "dest_body": getattr(_dm, "body", None)})
        elif name == "NavRouteClear":
            # Route cleared. Latch it + its journal timestamp. This fires on the
            # final hop (in witchspace) AND on a manual re-plot — the FSDJump
            # branch in dispatch() correlates by SystemAddress + the join window
            # to tell the two apart, so we never act on the clear alone.
            self._navroute_cleared = True
            ts = getattr(ev, "timestamp", None)
            self._navroute_cleared_utc = self._parse_journal_ts(ts) if ts else None
        elif name == "ReceiveText":
            # "$STATION_NoFireZone_entered;" = the ship just crossed inside the
            # 7.5km docking request range (live-verified 2026-06-07). Set the
            # flag; cleared by _clear_no_fire_zone at the start of each
            # dock_approach run so the gate only reflects THIS approach leg.
            msg = getattr(ev, "message", "") or ""
            if "$STATION_NoFireZone_entered;" in msg:
                self._no_fire_zone_entered = True
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
            self._docked_system_addr = getattr(ev, "system_address", None)  # D5 guard
        elif name == "Undocked":
            self._docked = False
            self._docked_system_addr = None  # D5 guard: clear on departure
        elif name == "LoadGame":
            # Ship field is Optional[str] on LoadGame (absent in some modes).
            ship = getattr(ev, "ship", None)
            if ship:
                self._current_ship = ship.lower().strip()
        elif name == "Loadout":
            # Loadout always carries the Ship field; prefer it over LoadGame
            # because Loadout fires after an outfit change (ship_name updated).
            ship = getattr(ev, "ship", None)
            if ship:
                self._current_ship = ship.lower().strip()
            cap = getattr(getattr(ev, "fuel_capacity", None), "main", None)
            if cap:
                scoop_item = next(
                    (m.item for m in getattr(ev, "modules", ())
                     if m.item.startswith("int_fuelscoop_")), None)
                rate = None
                if scoop_item is not None:
                    _smr = self._scoop_rate_fn or _scoop_max_rate_t_s
                    rate = _smr(scoop_item)
                self._ship_fuel = ShipFuel(capacity_t=float(cap),
                                           scoop_max_rate_t_s=rate)
        elif name == "Scan":
            # body_tour per-body ARRIVAL+DATA gate (M5/D5): the proximity
            # AutoScan that fires when the ship flies up to / orbits a body.
            # Detailed/NavBeacon scans do NOT count toward the tour gate.
            if getattr(ev, "scan_type", None) == "AutoScan":
                bn = getattr(ev, "body_name", None)
                if bn:
                    self._autoscan_bodies.add(bn)
                    self._autoscan_seq += 1
        elif name == "FSSDiscoveryScan":
            # body_tour honk latch (D4): durable "this system honked" record,
            # independent of the tour's own waiter timing. Reset on FSDJump.
            self._fss_discovered = True
            # BodyCount feeds the min-bodies gate (operator 2026-06-08).
            self._fss_body_count = getattr(ev, "body_count", 0) or 0
        elif name == "SupercruiseDestinationDrop":
            # body_tour station/POI hint (D2): a toured row that DROPS is a
            # station/POI. Monotone session counter; the tour snapshots it.
            self._drop_seq += 1
        elif name == "SupercruiseExit":
            # body_tour re-engage trigger (PD7): the drop's matching exit is
            # what actually puts the ship in real space. Monotone session
            # counter; the station-drop recovery re-engages on this bump.
            self._scex_seq += 1

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

    def request_stop(self) -> None:
        self.stop_requested = True

    # ---- never-strand re-dispatch (workstream A, 2026-07-07 council) ------
    def _maybe_redispatch(self) -> None:
        """Fire the queued re-dispatch when its bounded-backoff window has
        elapsed. Called from run_live's OWN loop iteration (never nested
        inside _run — see _run's three-way disambiguation, which only SETS
        `_needs_redispatch`). A no-op when nothing is queued or the window
        hasn't elapsed yet — between attempts this is a single clock()
        comparison, so run_live's idle branch stays a plain poll+sleep with
        NO sender.press and NO busy-spin (the never-strand law forbids a
        tight input/CPU loop as much as it forbids idling).

        On fire, in this exact order (spec-mandated):
          (i)   increment `_redispatch_attempts`
          (ii)  advance `_redispatch_next_t` by the bounded-backoff window
                computed from the NEW attempt count
          (iii) LOUD-announce (console + overlay + record)
          (iv)  clear `_needs_redispatch`
          (v)   call the domain driver (runner._redispatch_driver), if wired

        Ordering matters: (ii) must happen BEFORE (v) so a driver call that
        itself re-aborts (still stranded) re-queues `_needs_redispatch`
        WITHOUT touching the just-advanced deadline — the backoff keeps
        climbing across repeated unresolved attempts instead of one queue
        fighting the next attempt's timer.

        UNWIRED (`_redispatch_driver is None` — unit tests / no domain
        `activate()`): degrades to a LOUD bounded idle. The backoff/attempt
        mechanics above still run (and are unit-testable via an injected fake
        driver in ed-core alone) — only the actual re-dispatch call is a
        no-op."""
        if not self._needs_redispatch:
            return
        if self.clock() < self._redispatch_next_t:
            return
        self._redispatch_attempts += 1
        # OVERFLOW-SAFE (council blocker fix 2026-07-07, boundaries lens): the
        # exponent is CLAMPED. `_redispatch_attempts` resets only on a COMPLETED
        # run or a new journal event — NEITHER fires during a genuine unending
        # strand (ship idles, emits nothing, recovery keeps failing), so the
        # counter climbs unbounded. Without the clamp, ~attempt 1025 evaluates
        # 2**~1024 -> float OverflowError -> caught by run_live's CrashParked
        # handler -> panic + stop = the EXACT strand this guard exists to
        # prevent, inside one overnight session. Clamped, the backoff simply
        # pins at the cap and re-dispatches there FOREVER = infinite loud
        # bounded idle (operator ceiling contract: never a terminal stop).
        _exp = min(self._redispatch_attempts - 1, 20)   # 2**20 * base >> cap
        backoff = min(_REDISPATCH_BACKOFF_CAP_S,
                      _REDISPATCH_BACKOFF_BASE_S * (2 ** _exp))
        self._redispatch_next_t = self.clock() + backoff
        msg = (f"[STRAND-GUARD] re-dispatching from live state "
               f"(attempt {self._redispatch_attempts}, next backoff {backoff:.1f}s)")
        print(msg, flush=True)
        if self.overlay is not None:
            try:
                self.overlay.event(msg)
                self.overlay.status(msg)
            except Exception:  # noqa: BLE001 — overlay is fail-soft
                pass
        if self.record is not None:
            self.record("RedispatchAttempt",
                        {"attempt": self._redispatch_attempts, "backoff_s": backoff})
        self._needs_redispatch = False
        # OPERATOR RULING 2026-07-11: re-assert ED foreground before the
        # re-dispatch drives — overnight run 094825 spent 4.8 h pressing keys
        # into a stolen focus; this recovers it at the first strand-guard
        # window. Fail-soft, logged, no-op when unwired.
        if self.focus_reassert is not None:
            try:
                _focus_ok = bool(self.focus_reassert())
            except Exception:  # noqa: BLE001 — focus is best-effort, never fatal
                _focus_ok = False
            if self.record is not None:
                self.record("FocusReassert", {"ok": _focus_ok, "at": "redispatch"})
        driver = self._redispatch_driver
        if driver is None:
            # UNWIRED: LOUD bounded idle (still testable — see docstring).
            msg2 = ("[STRAND-GUARD] no redispatch driver wired -- idling "
                    "(bounded backoff, no input/CPU spin)")
            print(msg2, flush=True)
            if self.record is not None:
                self.record("RedispatchDriverUnwired", {})
            return
        try:
            driver(self)
        except Exception as exc:  # noqa: BLE001 — never crash the live loop
            # RE-ARM (council blocker fix 2026-07-07, failure-recovery lens):
            # `_needs_redispatch` was cleared above (iv) BEFORE this call. If the
            # driver itself raises (a transient fault OUTSIDE the dispatched
            # procedure's own _run, which would otherwise re-queue via the
            # three-way branch), re-set the flag so the NEXT backoff window
            # retries. Otherwise ONE driver hiccup leaves never-strand
            # permanently silent = idle-forever, the very failure this guard
            # exists to eliminate. Loud, logged, never crash-parked, never
            # silently disabled.
            self._needs_redispatch = True
            emsg = (f"[STRAND-GUARD] redispatch driver raised: "
                    f"{type(exc).__name__}: {exc} -- re-armed, retrying next window")
            print(emsg, flush=True)
            if self.record is not None:
                self.record("RedispatchDriverError", {"error": repr(exc)})
            if self.overlay is not None:
                try:
                    self.overlay.status(emsg)
                except Exception:  # noqa: BLE001
                    pass

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
                    run_classifiers(self)
                    # NEVER-STRAND (workstream A): fire a queued re-dispatch
                    # once its backoff window elapses. A no-op (single clock
                    # comparison) when nothing is queued -- the idle branch
                    # stays a plain poll+sleep, no busy-spin.
                    self._maybe_redispatch()
                    self.sleeper(poll_interval_s)
                    continue
                for ev in events:
                    if self._caught_up:
                        # A NEW journal event is evidence the strand (if any)
                        # moved: reset the backoff ladder so a later, separate
                        # incident starts its own attempt count from zero
                        # rather than inheriting a resolved one's climbed
                        # backoff. Does NOT touch `_needs_redispatch` itself —
                        # the route below re-dispatches its own procedure,
                        # whose _run() completion/abort governs that flag.
                        self._redispatch_attempts = 0
                        if (self._visited_logger is not None
                                and getattr(ev, "event", None) == "FSDJump"):
                            # Passive side-effect: record the live arrival, then
                            # dispatch exactly as before (logging never gates it).
                            self._visited_logger.record(
                                getattr(ev, "star_system", None),
                                getattr(ev, "timestamp", None))
                        run_event_routes(self, ev)
        except Exception as exc:  # noqa: BLE001 — never die silently mid-flight
            # PARK, don't crash (council 2026-06-09). An unhandled exception
            # anywhere in the live loop (a step raise the interpreter didn't
            # catch, a dispatch/_maybe_startup fault, a library error) used to
            # propagate out, kill the process, and leave the overlay FROZEN on
            # its last line — the 2026-06-09 NE-Y b34-0 "stuck on hold_alignment
            # forever" report. Park instead: record the reason, release keys,
            # label the overlay, trip panic, and stop the loop cleanly via the
            # finally below. KeyboardInterrupt/BaseException are NOT caught here,
            # so Ctrl+C / panic still propagate to cmd_run.
            if self.record is not None:
                self.record("CrashParked", {"reason": repr(exc)})
            try:
                self.sender.release_all()
            except Exception:  # noqa: BLE001
                pass
            if self.overlay is not None:
                try:
                    self.overlay.status(
                        f"[CRASH-PARKED] {type(exc).__name__}: {exc}")
                except Exception:  # noqa: BLE001
                    pass
            if self.panic_switch is not None:
                try:
                    self.panic_switch.trip()
                except Exception:  # noqa: BLE001
                    pass
            self.stop_requested = True
        finally:
            watchdog_stop.set()
            self._hub.unsubscribe(main_handle)

    # (Phase-1 reorg): backward-compat shim methods _maybe_startup, dispatch,
    # dispatch_route_complete removed. Tests now call boot_routes functions directly.
