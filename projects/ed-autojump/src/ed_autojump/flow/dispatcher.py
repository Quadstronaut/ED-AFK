"""Map live journal events to procedures, run them through the interpreter,
launch the parallel honk track, and own the live tail/status loop.

Replaces the orchestrator's escape/engage handlers. Replay (catch-up) events
only update state; actions fire only once caught up to LIVE."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
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
_PREEMPT_ON_SMACK = frozenset({"arrival", "startup"})


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
        self._ship_fuel: Optional[ShipFuel] = None

    # ---- public state accessors ------------------------------------------
    def event_time(self, name: str) -> Optional[float]:
        return self._event_times.get(name)

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
            ship_fuel_supplier=lambda: self._ship_fuel,
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
            run_procedure(proc, ctx)
            for th in threads:
                th.join(timeout=15.0)
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
            self._run("arrival")
        elif name == "SupercruiseExit" and getattr(ev, "body_type", None) == "Star":
            self._event_times["drop"] = self.clock()
            self._run("smack_recovery")

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
            # Restart mid-route (2026-06-06 13:26 star smack): a bot launched
            # while the ship is ALREADY in supercruise is sitting at its last
            # arrival star, nose-on — the ARRIVAL scene, not the fresh-load
            # scene startup.toml was written for. startup's throttle-100-
            # then-orient dove straight into the scoop zone (FuelScoop
            # 13:26:17 -> SupercruiseExit Body=Star 13:26:21). arrival.toml
            # orbits the star and clears it BEFORE throttling up.
            self._run("arrival")
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
