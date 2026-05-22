"""
Phase 12 — main loop orchestrator.

Wires `JournalTail.step()` → `GameState` → executor dispatch (`handle_start_jump`,
`perform_star_escape`, `perform_scoop`, danger filter) with `Recorder` snooping
every event + outcome. Two entry points:

- `handle_event(ev)` — single event, used by tests and `run_offline`.
- `run_offline(events)` — consumes a synthetic iterator end-to-end.
- `run_live(duration_s)` — polls a real JournalTail for `duration_s` seconds.

Everything is injectable (clock, sleeper, sender, recorder) so the same
orchestrator drives offline tests and live overnight runs unchanged.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Iterator, Optional

from .config import Config
from .executor.jump import (
    ChargeOutcome,
    ChargeResult,
    handle_start_jump,
    perform_star_escape,
    should_refuse_target,
)
from .executor.scoop import ScoopOutcome, perform_scoop, should_scoop
from .journal.events import (
    Event,
    FSDJump,
    FSDTarget,
    FuelScoop,
    HullDamage,
    Loadout,
    StartJump,
)
from .keys.sender import Sender
from .panic import PanicSwitch
from .recorder import Recorder
from .state import GameState, State


class Orchestrator:
    """The main loop. Owns the state, dispatches events to executors,
    records everything via the injected Recorder.

    Designed to be driven from either:
    - A synthetic iterator in tests (`run_offline`).
    - A live `JournalTail` (`run_live`).

    Single-threaded by design — the panic-stop flag is set from a
    listener thread but checked only on each event boundary.
    """

    def __init__(
        self,
        *,
        sender: Sender,
        recorder: Optional[Recorder],
        state: GameState,
        config: Config,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        heat_supplier: Optional[Callable[[], Optional[float]]] = None,
        panic_switch: Optional[PanicSwitch] = None,
    ):
        self.sender = sender
        self.recorder = recorder
        self.state = state
        self.config = config
        self.clock = clock
        self.sleeper = sleeper
        self.heat_supplier = heat_supplier
        self.panic_switch = panic_switch
        self.stop_requested = False
        self._shutdown_done = False
        self._panic_handled = False

    # --- public surface -----------------------------------------------------

    def request_stop(self) -> None:
        """Idempotent. Next tick boundary will break out of run_*."""
        self.stop_requested = True

    def shutdown(self) -> None:
        """Release keys + close recorder. Idempotent."""
        if self._shutdown_done:
            return
        try:
            self.sender.release_all()
        except Exception:
            # Best-effort; never let a key-release failure prevent close.
            pass
        if self.recorder is not None:
            self.recorder.close()
        self._shutdown_done = True

    def _poll_panic(self) -> bool:
        """Returns True if the panic switch is tripped. Records the abort
        outcome exactly once, releases keys, sets stop."""
        if self.panic_switch is None or not self.panic_switch.tripped:
            return False
        if not self._panic_handled:
            self._panic_handled = True
            self._record_outcome("SafetyAbort", {"reason": "panic_switch_tripped"})
            try:
                self.sender.release_all()
            except Exception:
                pass
            self.request_stop()
        return True

    def handle_event(
        self,
        ev: Event,
        *,
        follow_stream: Optional[Iterator[Event]] = None,
    ) -> None:
        """Process a single event. `follow_stream` is required only if a
        scoop fires; tests can omit it when they know no scoop will occur.
        """
        self._record_journal(ev)
        self._dispatch(ev, follow_stream)

    def run_offline(
        self,
        events: Iterable[Event],
        *,
        timeout_s: float = float("inf"),
    ) -> None:
        """Drain events one at a time. The iterator is reused as the
        follow_stream so executors that consume downstream events (scoop)
        pull from the same source."""
        # Check panic switch before doing anything.
        if self._poll_panic():
            return
        it = iter(events)
        deadline = self.clock() + timeout_s
        # Recording wrapper: as scoop or other executors consume downstream
        # events from this iterator, the recorder gets each one first.
        recording_it = self._recording_wrap(it)
        while not self.stop_requested and self.clock() < deadline:
            if self._poll_panic():
                break
            try:
                ev = next(recording_it)
            except StopIteration:
                break
            # Note: _record_journal was already called by recording_wrap
            # for this event. Skip the recording in _dispatch via
            # _dispatch_no_record.
            self._dispatch(ev, recording_it)

    def run_live(
        self,
        tail,
        *,
        duration_s: float,
        poll_interval_s: float = 0.5,
    ) -> None:
        """Poll a JournalTail for events until `duration_s` elapses or
        stop_requested. Each tick: drain events, dispatch each, sleep.

        The tail's events stream IS the follow_stream — passed through
        `_recording_wrap` so scoop loops record-as-they-consume.
        """
        deadline = self.clock() + duration_s

        def _stream() -> Iterator[Event]:
            while not self.stop_requested and self.clock() < deadline:
                try:
                    chunk = tail.step()
                except FileNotFoundError:
                    chunk = []
                if not chunk:
                    self.sleeper(poll_interval_s)
                    continue
                for ev in chunk:
                    yield ev

        recording_it = self._recording_wrap(_stream())
        while not self.stop_requested and self.clock() < deadline:
            if self._poll_panic():
                break
            try:
                ev = next(recording_it)
            except StopIteration:
                break
            self._dispatch(ev, recording_it)

    # --- internals ----------------------------------------------------------

    def _recording_wrap(self, source: Iterator[Event]) -> Iterator[Event]:
        """Record every event as it flows out, including events pulled by
        downstream executors (perform_scoop etc.)."""
        for ev in source:
            self._record_journal(ev)
            yield ev

    def _record_journal(self, ev: Event) -> None:
        if self.recorder is not None:
            self.recorder.record_journal(ev)

    def _record_outcome(self, outcome_type: str, payload: Any) -> None:
        if self.recorder is not None:
            self.recorder.record_outcome(outcome_type, payload)

    def _dispatch(self, ev: Event, follow_stream: Optional[Iterator[Event]]) -> None:
        """Type-dispatch to the right handler. Already-recorded by the
        caller — do NOT record `ev` again here."""
        if isinstance(ev, Loadout):
            self._on_loadout(ev)
        elif isinstance(ev, FSDTarget):
            self._on_fsd_target(ev)
        elif isinstance(ev, StartJump):
            self._on_start_jump(ev)
        elif isinstance(ev, FSDJump):
            self._on_fsd_jump(ev, follow_stream)
        elif isinstance(ev, HullDamage):
            self._on_hull_damage(ev)
        # Other events: pass through; state already updated by recorder/journal.

    # --- handlers ----------------------------------------------------------

    def _on_loadout(self, ev: Loadout) -> None:
        self.state.apply_loadout(ev)
        if not ev.fuel_scoop_present():
            # Fail-fast — bot cannot operate without a scoop.
            self._record_outcome("SafetyAbort", {
                "reason": "no_fuel_scoop_in_loadout",
                "ship": ev.ship,
            })
            self.request_stop()

    def _on_fsd_target(self, ev: FSDTarget) -> None:
        self.state.apply_fsd_target(ev)
        if should_refuse_target(ev, danger_classes=self.config.routing.danger_classes):
            self._record_outcome("RefuseTarget", {
                "star_class": ev.star_class,
                "system": ev.name,
            })

    def _on_start_jump(self, ev: StartJump) -> None:
        self.state.apply_start_jump(ev)
        result: ChargeResult = handle_start_jump(
            ev,
            self.sender,
            danger_classes=self.config.routing.danger_classes,
        )
        # Skip recording for supercruise (no-op outcome).
        if result.outcome != ChargeOutcome.NO_HYPERSPACE_EVENT:
            self._record_outcome("ChargeResult", {
                "outcome": result.outcome.name,
                "star_class": result.star_class,
            })

    def _on_fsd_jump(self, ev: FSDJump, follow_stream: Optional[Iterator[Event]]) -> None:
        self.state.apply_fsd_jump(ev)
        escape = perform_star_escape(
            ev, self.sender,
            cached_star_class=self.state.last_star_class,
            class_pitch_s=self.config.input.class_pitch_overrides,
            sleeper=self.sleeper,
        )
        self._record_outcome("EscapeOutcome", {
            "star_class": escape.star_class,
            "pitch_held_s": escape.pitch_held_s,
            "throttle_action": escape.throttle_action,
        })

        # Scoop decision: KGBFOAM + fuel below refuel_threshold + loadout has scoop.
        loadout = self.state.loadout
        if loadout is None or not loadout.fuel_scoop_present():
            return
        if follow_stream is None:
            return
        if not should_scoop(
            star_class=self.state.last_star_class or "",
            current_fuel_t=ev.fuel_level,
            fuel_capacity_t=loadout.fuel_capacity.main,
            refuel_threshold=self.config.routing.refuel_threshold,
        ):
            return

        outcome: ScoopOutcome = perform_scoop(
            self.sender,
            events=follow_stream,
            initial_fuel_t=ev.fuel_level,
            fuel_capacity_t=loadout.fuel_capacity.main,
            heat_supplier=self.heat_supplier,
            clock=self.clock,
        )
        self._record_outcome("ScoopOutcome", {
            "result": outcome.result.name,
            "initial_fuel_t": outcome.initial_fuel_t,
            "final_fuel_t": outcome.final_fuel_t,
            "max_heat_seen": outcome.max_heat_seen,
        })

    def _on_hull_damage(self, ev: HullDamage) -> None:
        self._record_outcome("SafetyAbort", {
            "reason": "hull_damage",
            "health": ev.health,
        })
        self.request_stop()
