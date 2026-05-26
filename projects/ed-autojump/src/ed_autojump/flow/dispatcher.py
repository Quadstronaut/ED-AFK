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
        record: Optional[Callable[[str, Any], None]] = None,
        tail: Optional[Any] = None,
        status_reader: Optional[Any] = None,
        navroute_reader: Optional[Any] = None,
        panic_switch: Optional[Any] = None,
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
        self.record = record
        self.tail = tail
        self.status_reader = status_reader
        self.navroute_reader = navroute_reader
        self.panic_switch = panic_switch

        self._event_times: dict[str, float] = {}
        self._latest_status: Optional[Any] = status_supplier()
        self._caught_up = False
        self._startup_done = False
        self.stop_requested = False

    # ---- public state accessors ------------------------------------------
    def event_time(self, name: str) -> Optional[float]:
        return self._event_times.get(name)

    # ---- context construction --------------------------------------------
    def _make_context(self) -> StepContext:
        return StepContext(
            sender=self.sender,
            clock=self.clock,
            sleeper=self.sleeper,
            compass_reader=self.compass_reader,
            frame_grabber=self.frame_grabber,
            align_kwargs=self.align_kwargs,
            compass_samples=self.compass_samples,
            status_supplier=lambda: self._latest_status,
            event_time=self.event_time,
            event_waiter=self._wait_for_event,
            record=self.record,
        )

    # ---- running procedures ----------------------------------------------
    def _run(self, name: str) -> None:
        proc = self.procedures.get(name)
        if proc is None:
            return
        ctx = self._make_context()
        threads: list[threading.Thread] = []
        for track_name in proc.parallel_tracks:
            track = self.procedures.get(track_name)
            if track is None:
                continue
            th = threading.Thread(
                target=run_procedure, args=(track, self._make_context()), daemon=True
            )
            th.start()
            threads.append(th)
        run_procedure(proc, ctx)
        for th in threads:
            th.join(timeout=15.0)

    def dispatch(self, ev: Any) -> None:
        """Run the procedure mapped to a LIVE event."""
        name = getattr(ev, "event", None)
        if name == "FSDJump":
            self._run("arrival")
        elif name == "SupercruiseExit" and getattr(ev, "body_type", None) == "Star":
            self._event_times["drop"] = self.clock()
            self._run("smack_recovery")

    # ---- live loop --------------------------------------------------------
    def _wait_for_event(self, event_name: str, timeout_s: float) -> bool:
        """Poll the journal tail until `event_name` is logged or timeout."""
        if self.tail is None:
            return True  # no tail wired (unit tests) -> proceed
        deadline = self.clock() + timeout_s
        while self.clock() < deadline:
            for ev in self.tail.step():
                self._record_event_time(ev)
                self._apply_state(ev)
                if getattr(ev, "event", None) == event_name:
                    return True
            self.sleeper(0.2)
        return False

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
        if self.tail is None:
            raise RuntimeError("run_live requires a journal tail")
        deadline = self.clock() + duration_s
        while not self.stop_requested and self.clock() < deadline:
            if self.panic_switch is not None and getattr(self.panic_switch, "tripped", False):
                break
            self._poll_status()
            events = self.tail.step()
            if not events:
                self._caught_up = True
                self._maybe_startup()
                self.sleeper(poll_interval_s)
                continue
            for ev in events:
                self._record_event_time(ev)
                self._apply_state(ev)
                if self._caught_up:
                    self.dispatch(ev)
