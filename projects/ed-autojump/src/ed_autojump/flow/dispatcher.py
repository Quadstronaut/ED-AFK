"""Map live journal events to procedures, run them through the interpreter,
launch the parallel honk track, and own the live tail/status loop.

Replaces the orchestrator's escape/engage handlers. Replay (catch-up) events
only update state; actions fire only once caught up to LIVE."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from .context import StepContext
from .interpreter import run_procedure
from .model import Procedure


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
        overlay: Optional[Any] = None,
        record: Optional[Callable[[str, Any], None]] = None,
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
        self.overlay = overlay
        self.record = record
        self.tail = tail
        self.status_reader = status_reader
        self.navroute_reader = navroute_reader
        self.panic_switch = panic_switch
        self.heat_eject_cooldown_s = heat_eject_cooldown_s

        self._event_times: dict[str, float] = {}
        self._latest_status: Optional[Any] = status_supplier()
        self._caught_up = False
        self._startup_done = False
        self._last_eject_t: float = 0.0
        self._jumps = 0
        self.stop_requested = False
        # Single tail consumer + fan-out (see _TailHub). None without a tail.
        self._hub: Optional[_TailHub] = (
            _TailHub(tail, on_event=self._on_tail_event) if tail is not None else None)

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
        """Operator abort signal for in-step loops: panic hotkey or stop
        request. With wall-clock gates banned, this is the only non-game exit
        from an event-driven wait."""
        if self.stop_requested:
            return True
        return self.panic_switch is not None and getattr(
            self.panic_switch, "tripped", False)

    # ---- context construction --------------------------------------------
    def _make_context(self) -> StepContext:
        # Each context gets its OWN hub subscription so concurrent waiters
        # (honk track + main procedure) all see every event. _run owns the
        # unsubscribe via the handle stashed on the context.
        handle = self._hub.subscribe() if self._hub is not None else None
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
            overlay=self.overlay,
            status_supplier=self._fresh_status,
            event_time=self.event_time,
            event_waiter=lambda name, t, _h=handle: self._wait_for_event(_h, name, t),
            should_abort=self._should_abort,
            record=self.record,
        )
        ctx._tail_handle = handle   # for _run's unsubscribe; None without a tail
        return ctx

    # ---- running procedures ----------------------------------------------
    def _run(self, name: str) -> None:
        proc = self.procedures.get(name)
        if proc is None:
            return
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

    def _apply_state(self, ev: Any) -> None:
        """Hook for tracking next-target etc. State the engage gate needs is
        read live from status; route targeting is done in-procedure via
        target_next_route, so this is intentionally minimal for v1."""
        return

    def _poll_status(self) -> None:
        if self.status_reader is not None:
            st = self.status_reader.poll()
            if st is not None:
                self._latest_status = st

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
        self._run("startup")

    def request_stop(self) -> None:
        self.stop_requested = True

    def run_live(self, *, duration_s: float, poll_interval_s: float = 0.5) -> None:
        if self.tail is None or self._hub is None:
            raise RuntimeError("run_live requires a journal tail")
        main_handle = self._hub.subscribe()
        deadline = self.clock() + duration_s
        try:
            while not self.stop_requested and self.clock() < deadline:
                if self.panic_switch is not None and getattr(self.panic_switch, "tripped", False):
                    break
                self._poll_status()
                self.heat_guard()
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
            self._hub.unsubscribe(main_handle)
