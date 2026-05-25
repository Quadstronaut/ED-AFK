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
from .executor.escape import SensedEscapeOutcome, perform_sensed_escape
from .executor.jump import (
    ChargeOutcome,
    ChargeResult,
    handle_start_jump,
    perform_star_escape,
    should_refuse_target,
)
from .executor.align import align_to_target
from .executor.honk import HonkOutcome, perform_honk
from .executor.scoop import ScoopOutcome, perform_scoop, should_scoop
from .executor.refuel import RefuelOutcome, perform_refuel_on_star
from .executor.smack_recovery import SmackRecoveryOutcome, perform_smack_recovery
from .journal.events import (
    Event,
    FSDJump,
    FSDTarget,
    FSSAllBodiesFound,
    FSSDiscoveryScan,
    FuelScoop,
    HullDamage,
    Loadout,
    StartJump,
    SupercruiseExit,
)
from .eddn.publisher import EddnError, EddnPublisher
from .keys.sender import Sender
from .panic import PanicSwitch
from .planner.spansh import SpanshRouteResult
from .recorder import Recorder
from .state import GameState, State
from .status.navroute import NavRoute, NavRouteReader
from .status.status import Status, StatusReader


# (source_system, destination_system, range_ly) -> Optional[SpanshRouteResult]
RoutePlannerFn = Callable[[str, str, float], Optional[SpanshRouteResult]]


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
        route_planner: Optional[RoutePlannerFn] = None,
        status_reader: Optional[StatusReader] = None,
        eddn_publisher: Optional[EddnPublisher] = None,
        navroute_reader: Optional[NavRouteReader] = None,
        auto_engage: bool = True,
        compass_reader: Optional[object] = None,
        frame_grabber: Optional[Callable[[], object]] = None,
        sun_grab: Optional[Callable[[], object]] = None,
    ):
        self.sender = sender
        self.recorder = recorder
        self.state = state
        self.config = config
        self.clock = clock
        self.sleeper = sleeper
        self.panic_switch = panic_switch
        self.route_planner = route_planner
        self.status_reader = status_reader
        self.eddn_publisher = eddn_publisher
        self.navroute_reader = navroute_reader
        self.auto_engage = auto_engage
        self.compass_reader = compass_reader
        self.frame_grabber = frame_grabber
        self.sun_grab = sun_grab
        # One-shot: on a fresh load we sit in realspace AT the arrival star with
        # no FSDJump/SupercruiseExit to trigger the normal escape. Without this
        # the engage gate would try to align-and-jump while stuck on the star.
        # Cleared after the first flyable Status tick (see _maybe_startup_escape).
        self._startup_escape_pending = True
        self.stop_requested = False
        self._shutdown_done = False
        self._panic_handled = False
        # heat_supplier defaults: explicit arg wins; else if status_reader
        # is wired, read from state.status.heat; else None.
        if heat_supplier is not None:
            self.heat_supplier: Optional[Callable[[], Optional[float]]] = heat_supplier
        elif status_reader is not None:
            self.heat_supplier = self._status_heat
        else:
            self.heat_supplier = None

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

    def _status_heat(self) -> Optional[float]:
        """Default heat_supplier when a status_reader is wired."""
        if self.state.status is not None:
            return self.state.status.heat
        return None

    def tick_navroute(self) -> None:
        """Pull NavRoute.json once and apply to state. No-op without reader."""
        if self.navroute_reader is None:
            return
        try:
            nr = self.navroute_reader.poll()
        except Exception as exc:  # noqa: BLE001
            self._record_outcome("NavRouteError", {"error": str(exc)})
            return
        if nr is not None:
            self.state.apply_navroute(nr)

    def tick_status(self) -> None:
        """Pull Status.json once, apply to state, check safety flags.

        Safe to call when status_reader is None — no-ops. Should be called
        from the live loop's poll-interval pause."""
        if self.status_reader is None:
            return
        try:
            status = self.status_reader.poll()
        except Exception as exc:  # noqa: BLE001
            self._record_outcome("StatusError", {"error": str(exc)})
            return
        if status is None:
            return
        self.state.apply_status(status)
        if status.overheating:
            self._record_outcome("SafetyAbort", {
                "reason": "overheating",
                "heat": status.heat,
            })
            self.request_stop()
            return
        if status.is_in_danger:
            self._record_outcome("SafetyAbort", {
                "reason": "in_danger",
            })
            self.request_stop()
            return
        # One-shot get-off-star on launch, BEFORE any jump-engage, so we never
        # try to align/jump while still stuck on the arrival star.
        if self._startup_escape_pending:
            self._maybe_startup_escape(status)
        # Auto-engage next jump if conditions are met.
        self._maybe_engage_next_jump(status)

    def _maybe_startup_escape(self, status: Status) -> None:
        """Run the get-off-star pitch ONCE on launch (your spec #1: 'script
        launched -> check for a star -> pitch up until it's gone -> accelerate
        -> then orient to the next jump'). On a fresh load we sit in realspace at
        the arrival star with no arrival event to trigger the normal escape, so
        the engage gate would otherwise bob against the star forever.

        One-shot: the pending flag is cleared whether or not we act. Skips when
        docked (nothing to escape) or with no sun probe (can't sense the star).
        Uses brightness sun-avoid regardless of escape_mode — getting clear of
        the star is a prerequisite for leaving; refuel-to-star only matters once
        fuel is actually low, which is handled on real arrivals."""
        self._startup_escape_pending = False
        if status.docked:
            return
        if self.sun_grab is None:
            return
        e = self.config.escape
        sensed: SensedEscapeOutcome = perform_sensed_escape(
            None,
            self.sender,
            mode="brightness",
            compass_reader=self.compass_reader,
            compass_capture=self.frame_grabber,
            sun_capture=self.sun_grab,
            cached_star_class=self.state.last_star_class,
            align_kwargs=self._align_kwargs(),
            sleeper=self.sleeper,
            clock=self.clock,
            bright_thresh=e.sun_bright_thresh,
            present_frac=e.sun_present_frac,
            clear_frac=e.sun_clear_frac,
            pitch_hold=e.sun_pitch_hold_s,
            timeout_s=e.sun_timeout_s,
            clear_throttle=e.clear_throttle,
            clear_s=e.clear_s,
            clear_reenter_frac=e.clear_reenter_frac,
            clear_step_s=e.clear_step_s,
        )
        avoid = sensed.sun_avoid
        self._record_outcome("StartupEscape", {
            "in_supercruise": status.in_supercruise,
            "star_detected": getattr(sensed, "star_detected", None),
            "sun_cleared": avoid.cleared if avoid is not None else None,
            "sun_iterations": avoid.iterations if avoid is not None else None,
            "aligned": sensed.aligned,
            "notes": sensed.notes,
        })

    def _maybe_engage_next_jump(self, status: Status) -> None:
        """Press HyperSuperCombination if we have a safe target + Status
        flags are all clear + we're not already mid-engagement."""
        if not self.auto_engage:
            return
        target = self.state.next_target
        if target is None:
            return
        # Self-heal: if HSC was pressed but StartJump never arrived (slow
        # disk, journal flush stall, missed event), force-clear the flag
        # after the timeout so we don't sit forever in a broken state.
        if self.state.engagement_in_progress:
            started = self.state.engagement_started_at
            timeout = self.config.safety.engagement_debounce_timeout_s
            # Still within the debounce window -> wait. A missing start time is
            # a broken state (flag set, timestamp lost): treat it as expired and
            # fall through to force-clear below, rather than waiting forever.
            if started is not None and (self.clock() - started) < timeout:
                return
            self._record_outcome("EngagementTimeout", {
                "target_system": target.name,
                "elapsed_s": self.clock() - (started or 0.0),
                "timeout_s": timeout,
            })
            self.state.engagement_in_progress = False
            self.state.engagement_started_at = None
        # Refuse to engage danger-class.
        if should_refuse_target(target, danger_classes=self.config.routing.danger_classes):
            return
        # Status must be free of blocking flags.
        if status.docked or status.fsd_charging or status.fsd_cooldown:
            return
        if status.fsd_mass_locked:
            return
        # (overheating + is_in_danger already short-circuit above)
        # Lock the next route star deterministically (H) before aligning —
        # no nav-panel scrolling, and it gives the compass a target to point
        # at. Non-fatal if the bind is absent.
        if self.config.nav.retarget_route_before_engage:
            try:
                self.sender.press("TargetNextRouteSystem", hold=0.05)
                self._record_outcome("RetargetRoute", {"target_system": target.name})
            except KeyError:
                self._record_outcome("RetargetBindMissing",
                                     {"action": "TargetNextRouteSystem"})
        # Orient toward the target before committing the jump. With vision
        # off this is a no-op (returns True); with vision on, a failed
        # alignment blocks the engage so we never fire the FSD pointed at
        # the star / off-target. The next status tick retries.
        if not self._aligned_for_engage():
            return
        # Full throttle is REQUIRED for the FSD to charge and jump — pressing
        # HyperSuperCombination at zero throttle does nothing and we sit at
        # jump 0 forever. Throttle up first, then engage.
        try:
            self.sender.press("SetSpeed100", hold=0.05)
        except KeyError:
            self._record_outcome("ThrottleBindMissing", {"action": "SetSpeed100"})
        try:
            self.sender.press("HyperSuperCombination", hold=0.05)
        except KeyError:
            self._record_outcome("EngageBindMissing", {
                "action": "HyperSuperCombination",
            })
            return
        self.state.engagement_in_progress = True
        self.state.engagement_started_at = self.clock()
        self._record_outcome("EngageJump", {
            "target_system": target.name,
            "star_class": target.star_class,
        })

    def _aligned_for_engage(self) -> bool:
        """Run the compass alignment loop before engaging. Returns True (no
        gate) when vision is disabled or unwired, preserving the blind
        behaviour. When vision is on, returns whether the ship is oriented
        at the target — a False blocks the FSD press."""
        v = self.config.vision
        if not v.enabled or self.compass_reader is None or self.frame_grabber is None:
            return True
        outcome = align_to_target(
            self.compass_reader,
            self.sender,
            capture=self.frame_grabber,
            align_tol=v.align_tol,
            deadzone=v.deadzone,
            gain=v.gain,
            min_press=v.min_press_s,
            max_press=v.max_press_s,
            search_press=v.search_press_s,
            settle_s=v.settle_s,
            max_iters=v.max_iters,
            timeout_s=v.timeout_s,
            clock=self.clock,
            sleeper=self.sleeper,
            samples=v.align_samples,
        )
        self._record_outcome("Align", {
            "aligned": outcome.aligned,
            "iterations": outcome.iterations,
            "reason": outcome.reason,
            "offset_x": outcome.final.offset_x,
            "offset_y": outcome.final.offset_y,
            "in_front": outcome.final.in_front,
            "confidence": outcome.final.confidence,
        })
        return outcome.aligned

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
                except Exception as exc:  # noqa: BLE001
                    # Defensive: log + continue. Overnight bot must survive
                    # transient OS errors (disk hiccup, antivirus lock, etc.)
                    self._record_outcome("TailError", {"error": str(exc)})
                    chunk = []
                if not chunk:
                    self.tick_status()
                    self.tick_navroute()
                    if self.stop_requested:
                        return
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
        elif isinstance(ev, SupercruiseExit):
            self._on_supercruise_exit(ev)
        elif isinstance(ev, HullDamage):
            self._on_hull_damage(ev)
        elif isinstance(ev, FSSDiscoveryScan):
            self._publish_eddn("fssdiscoveryscan", ev)
        elif isinstance(ev, FSSAllBodiesFound):
            self._publish_eddn("fssallbodiesfound", ev)
        # Other events: pass through; state already updated by recorder/journal.

    def _publish_eddn(self, schema_key: str, ev: Event) -> None:
        if self.eddn_publisher is None:
            return
        if not self.config.eddn.publish:
            return
        message = ev.model_dump(mode="json", by_alias=True)
        try:
            self.eddn_publisher.publish(schema_key, message)
        except EddnError as exc:
            self._record_outcome("EddnPublishFailed", {
                "schema": schema_key,
                "error": str(exc),
            })

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
            return
        # Jump-range sanity (warning only — non-blocking).
        expected = self.config.ship.expected_max_jump_range_ly
        if expected > 0 and ev.max_jump_range < expected * 0.9:
            self._record_outcome("LoadoutWarning", {
                "reason": "jump_range_below_expected",
                "actual": ev.max_jump_range,
                "expected": expected,
                "tolerance_ratio": 0.9,
            })

    def _on_fsd_target(self, ev: FSDTarget) -> None:
        self.state.apply_fsd_target(ev)
        if should_refuse_target(ev, danger_classes=self.config.routing.danger_classes):
            self._record_outcome("RefuseTarget", {
                "star_class": ev.star_class,
                "system": ev.name,
            })

    def _on_start_jump(self, ev: StartJump) -> None:
        self.state.apply_start_jump(ev)
        # Clear the engagement debounce — FSD acknowledged our press.
        self.state.engagement_in_progress = False
        self.state.engagement_started_at = None
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
        escape_mode = self.config.escape.escape_mode
        align_kwargs = self._align_kwargs()
        handled_scoop = False

        if escape_mode == "refuel":
            # DEFAULT flow: approach the arrival star, scoop to full (skips
            # straight to the peel-off when already full), engage Supercruise
            # Assist, cancel via TargetNextRouteSystem, depart + align. Replaces
            # both the escape AND the post-jump scoop.
            self._do_refuel(ev, follow_stream, align_kwargs)
            handled_scoop = True
        elif escape_mode != "blind" and self.sun_grab is not None:
            # Vision-sensed escape (brightness / sc_assist): pitch the star off,
            # accelerate, compass-align.
            sensed: SensedEscapeOutcome = perform_sensed_escape(
                ev,
                self.sender,
                mode=escape_mode,
                compass_reader=self.compass_reader,
                compass_capture=self.frame_grabber,
                sun_capture=self.sun_grab,
                cached_star_class=self.state.last_star_class,
                align_kwargs=align_kwargs,
                sleeper=self.sleeper,
                clock=self.clock,
                bright_thresh=self.config.escape.sun_bright_thresh,
                present_frac=self.config.escape.sun_present_frac,
                clear_frac=self.config.escape.sun_clear_frac,
                pitch_hold=self.config.escape.sun_pitch_hold_s,
                timeout_s=self.config.escape.sun_timeout_s,
                clear_throttle=self.config.escape.clear_throttle,
                clear_s=self.config.escape.clear_s,
                clear_reenter_frac=self.config.escape.clear_reenter_frac,
                clear_step_s=self.config.escape.clear_step_s,
            )
            avoid = sensed.sun_avoid
            self._record_outcome("SensedEscape", {
                "mode": sensed.mode,
                "star_class": sensed.star_class,
                "sun_cleared": avoid.cleared if avoid is not None else None,
                "sun_iterations": avoid.iterations if avoid is not None else None,
                "sun_reason": avoid.reason if avoid is not None else None,
                "aligned": sensed.aligned,
                "notes": sensed.notes,
            })
        else:
            # Blind fallback: fixed-duration pitch (legacy behaviour, used when
            # sun_grab is not wired or escape_mode == "blind").
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

        # Plot next route if planner is wired.
        self._maybe_plot_route()

        # Scoop if low fuel + scoopable + has scoop module — unless "refuel"
        # mode already scooped to full at the star (don't double-scoop).
        if not handled_scoop:
            self._maybe_scoop(ev, follow_stream)

        # Honk if enabled.
        self._maybe_honk(follow_stream)

    def _align_kwargs(self) -> dict:
        """Build the align_to_target kwargs from VisionConfig — shared by every
        escape mode that orients toward the next target (brightness/refuel/orbit)."""
        v = self.config.vision
        return dict(
            align_tol=v.align_tol,
            deadzone=v.deadzone,
            gain=v.gain,
            min_press=v.min_press_s,
            max_press=v.max_press_s,
            search_press=v.search_press_s,
            settle_s=v.settle_s,
            max_iters=v.max_iters,
            timeout_s=v.timeout_s,
            samples=v.align_samples,
        )

    def _do_refuel(
        self, ev: FSDJump, follow_stream: Optional[Iterator[Event]], align_kwargs: dict
    ) -> None:
        """Default flow: approach the arrival star, scoop to full (journal-driven —
        cut throttle at the FuelScoop rate plateau, fill to capacity), engage
        Supercruise Assist via the nav-panel macro, cancel + depart with
        TargetNextRouteSystem, then orient. An already-full tank skips the scoop
        and goes straight to the clean peel-off."""
        loadout = self.state.loadout
        cfg = self.config.escape
        outcome: RefuelOutcome = perform_refuel_on_star(
            self.sender,
            follow_stream if follow_stream is not None else iter([]),
            fuel_capacity_t=loadout.fuel_capacity.main if loadout is not None else 0.0,
            initial_fuel_t=ev.fuel_level,
            compass_reader=self.compass_reader,
            compass_capture=self.frame_grabber,
            align_kwargs=align_kwargs,
            approach_throttle=cfg.refuel_approach_throttle,
            orbit_s=cfg.refuel_orbit_s,
            post_depart_wait_s=cfg.refuel_post_depart_wait_s,
            rate_epsilon=cfg.refuel_rate_epsilon,
            sleeper=self.sleeper,
            clock=self.clock,
        )
        self._record_outcome("RefuelOutcome", {
            "initial_fuel_t": outcome.initial_fuel_t,
            "final_fuel_t": outcome.final_fuel_t,
            "was_full": outcome.was_full,
            "throttle_cut": outcome.throttle_cut,
            "assist_engaged": outcome.assist_engaged,
            "saw_scoop": outcome.saw_scoop,
            "aligned": outcome.aligned,
            "notes": outcome.notes,
        })

    def _on_supercruise_exit(self, ev: SupercruiseExit) -> None:
        """An emergency drop INTO the arrival star (BodyType 'Star') means we
        smacked it — the FSD is on a ~45 s cooldown. Run smack recovery: orient
        away immediately, wait out the cooldown, re-engage the FSD, then target
        the next hop and orient. Gated behind the smack_recovery config flag."""
        if not self.config.escape.smack_recovery:
            return
        if (ev.body_type or "").lower() != "star":
            return
        outcome: SmackRecoveryOutcome = perform_smack_recovery(
            self.sender,
            compass_reader=self.compass_reader,
            compass_capture=self.frame_grabber,
            align_kwargs=self._align_kwargs(),
            cooldown_s=self.config.escape.smack_cooldown_s,
            post_sc_wait_s=self.config.escape.smack_post_sc_wait_s,
            sleeper=self.sleeper,
            clock=self.clock,
        )
        self._record_outcome("SmackRecovery", {
            "pitches": outcome.pitches,
            "star_cleared": outcome.star_cleared,
            "cooldown_waited_s": outcome.cooldown_waited_s,
            "triggered_fsd": outcome.triggered_fsd,
            "aligned": outcome.aligned,
            "notes": outcome.notes,
        })

    def _maybe_scoop(self, ev: FSDJump, follow_stream: Optional[Iterator[Event]]) -> None:
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

    def _maybe_honk(self, follow_stream: Optional[Iterator[Event]]) -> None:
        """Run perform_honk if enabled. Called after escape (and after
        any scoop completes). Follow-stream is the same event source as
        scoop so the honk routine can watch for the matching FSSDiscoveryScan."""
        if not self.config.exploration.honk:
            return
        honk_events: Iterable[Event] = follow_stream if follow_stream is not None else iter([])
        outcome: HonkOutcome = perform_honk(
            self.sender,
            honk_events,
            clock=self.clock,
            sleeper=self.sleeper,
        )
        self._record_outcome("HonkOutcome", {
            "result": outcome.result.name,
            "held_for_s": outcome.held_for_s,
            "waited_for_s": outcome.waited_for_s,
            "mode_toggled": outcome.mode_toggled,
            "retried": outcome.retried,
        })

    def _maybe_plot_route(self) -> None:
        """Trigger a Spansh plot if conditions are met.

        Called after every FSDJump arrival. Conditions:
        - route_planner injected
        - Loadout known (need max_jump_range)
        - current_system known
        - destination configured
        - NavRoute is empty/unknown (when nav-reader is wired; skip plot
          if we already have a plotted route).
        """
        if self.route_planner is None:
            return
        if self.state.loadout is None:
            return
        if not self.state.current_system:
            return
        dest = self.config.routing.destination
        if not dest:
            return
        # If we have a navroute reader and it shows a non-empty route,
        # skip — the user (or a prior plot) already gave us one.
        if (
            self.navroute_reader is not None
            and self.state.last_navroute is not None
            and not self.state.last_navroute.empty
        ):
            return
        range_ly = self.state.loadout.max_jump_range
        try:
            result = self.route_planner(self.state.current_system, dest, range_ly)
        except Exception as exc:  # noqa: BLE001
            self._record_outcome("RoutePlotFailed", {
                "source": self.state.current_system,
                "destination": dest,
                "error": str(exc),
            })
            return
        if result is None:
            return
        self._record_outcome("RoutePlotted", {
            "source": self.state.current_system,
            "destination": dest,
            "total_jumps": result.total_jumps,
            "total_distance_ly": result.total_distance_ly,
        })

    def _on_hull_damage(self, ev: HullDamage) -> None:
        self._record_outcome("SafetyAbort", {
            "reason": "hull_damage",
            "health": ev.health,
        })
        self.request_stop()
