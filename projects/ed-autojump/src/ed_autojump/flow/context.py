"""Everything a step function may need, injected (so steps are unit-testable
with fakes and no real game / no real sleeps)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class ShipFuel:
    """Fuel facts from the latest Loadout: tank size and the equipped
    scoop's table max rate (None when no scoop / unknown module — the
    scoop_refuel step skips fail-safe on None rather than guess a rate)."""
    capacity_t: float
    scoop_max_rate_t_s: Optional[float]


@dataclass
class StepContext:
    sender: Any
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep

    # vision (None when vision is off -> compass steps fail closed)
    compass_reader: Optional[Any] = None
    frame_grabber: Optional[Callable[[], Any]] = None
    align_kwargs: dict = field(default_factory=dict)
    compass_samples: int = 7

    # live state suppliers, wired by the FlowRunner
    status_supplier: Callable[[], Optional[Any]] = lambda: None
    event_time: Callable[[str], Optional[float]] = lambda name: None
    # block until `event` is logged or `timeout_s` elapses; True if seen.
    # NB: steps use this with SHORT windows as a poll cadence — never as a
    # success/failure gate (no-arbitrary-timed-waits rule).
    event_waiter: Optional[Callable[[str, float], bool]] = None

    # operator abort (panic switch / stop request). Event-driven loops MUST
    # consult this every iteration — it is the only exit that isn't a game
    # signal, and the operator's backstop now that wall-clock gates are banned.
    should_abort: Callable[[], bool] = lambda: False

    # factory returning a context manager that marks "a UI macro owns input"
    # for its duration (heat watchdog pauses). None when no watchdog is wired.
    exclusive_guard: Optional[Callable[[], Any]] = None

    # (seq, latest FSDTarget event) — seq advances once per FSDTarget the
    # tail has seen, so target_next_route can tell a NEW target from a stale
    # one. Wired by FlowRunner; the default means "no journal wiring".
    fsd_target_supplier: Callable[[], tuple] = lambda: (0, None)

    # Latest parsed NavRoute.json (or None). target_next_route uses it to
    # danger-check a hop that was ALREADY locked before the bot started —
    # that press emits no new FSDTarget event, so Status.Destination +
    # the route's StarClass are the only confirmation (2026-06-06 dead run).
    navroute_supplier: Callable[[], Optional[Any]] = lambda: None

    # StarClass of the CURRENT system's arrival star — the FlowRunner tracks
    # the last Hyperspace StartJump (supercruise StartJumps carry null and
    # never clobber it). None = not wired / no jump seen -> scoop_refuel
    # skips fail-safe (g2).
    arrival_star_class_supplier: Callable[[], Optional[str]] = lambda: None

    # Name of the CURRENT system (last FSDJump/Location StarSystem). The
    # star-lock identity check (2026-06-07 council: nav_panel_target locked
    # the NAV BEACON and "verified" it) compares Status.Destination.Name
    # against this — the primary star carries the bare system name, the
    # secondaries "<system> A".."<system> D". None = not wired -> identity
    # check degrades to dot-only verification, loudly.
    current_system_supplier: Callable[[], Optional[str]] = lambda: None

    # ShipFuel from the latest Loadout (capacity + scoop max rate). None =
    # not wired / no Loadout seen -> scoop_refuel skips fail-safe (g1).
    ship_fuel_supplier: Callable[[], Optional[Any]] = lambda: None

    # Seconds since the last FSDJump per the EVENT'S OWN journal timestamp (NOT
    # a monotonic-clock stamp, which reads a stale backlog restart as fresh).
    # None = no jump seen / not wired. scoop_refuel's stale-arrival classifier
    # uses it: a fresh hyperspace exit is the nose-into-star pose the scoop
    # relies on; an N-minute loiter (the 25-min restart incident) is not.
    jump_age_supplier: Callable[[], Optional[float]] = lambda: None

    # Reason string of the most recent DockingDenied event seen by the
    # FlowRunner (or None). step_dock_request reads it to tell a retryable
    # "Distance" denial (re-approach) from an abort-to-human denial
    # (NoSpace/TooLarge/Hostile/...). The name-only event_waiter can't carry
    # the Reason field, so the runner stashes it and exposes it here. None =
    # not wired / no denial.
    docking_denied_supplier: Callable[[], Optional[str]] = lambda: None

    # Reset the runner's stashed DockingDenied reason to None. step_dock_request
    # calls this when it ARMS so it only ever acts on a denial earned by ITS OWN
    # request — the dispatcher clears on grant/dock but NOT when a new request
    # begins, so a stale reason (e.g. the out-of-range Distance denial that
    # step_dock_target_station's Contacts fallback deliberately earns) would
    # otherwise poison the next in-range request's grant loop. No-op default
    # (unit tests with no runner wiring).
    clear_docking_denied: Callable[[], None] = lambda: None

    # outcome logging (recorder.record_outcome), optional
    record: Optional[Callable[[str, Any], None]] = None

    # diagnostic frame sink (name, frame) -> None. cli wires it to write the
    # orient loop's compass crops as PNGs next to the session jsonl, so a
    # failing orient is replayable offline against the reader (2026-06-06:
    # the oscillation could only be root-caused because ED happened to still
    # be running with the ship parked). None -> frames are not retained.
    frame_sink: Optional[Callable[[str, Any], None]] = None

    # widget-ring fine-align vision. widget_frame_grabber is the CENTRE-CROP
    # .grab — distinct from frame_grabber, which is the compass-region crop.
    # widget_ring_on_miss: "degrade" (default — a miss skips the fine pass,
    # compass-only jump proceeds; operator decision 2026-06-06, issue #1) or
    # "fail_closed" (a miss fails the required step, gating the jump).
    widget_ring_enabled: bool = False
    widget_ring_reader: Optional[Any] = None
    widget_frame_grabber: Optional[Callable[[], Any]] = None
    widget_ring_on_miss: str = "degrade"

    # cosmetic EDMCOverlay status writer (None -> no overlay). Duck-typed:
    # .step(proc, action, idx, total). Fail-soft; never blocks a step.
    overlay: Optional[Any] = None

    def log(self, outcome_type: str, payload: Any) -> None:
        if self.record is not None:
            self.record(outcome_type, payload)
