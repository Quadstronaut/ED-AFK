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

    # True while the ship is in the hyperspace tunnel (witchspace loading screen).
    # SET on a Hyperspace StartJump, CLEARED on FSDJump (~18s window,
    # journal-confirmed). The interpreter PAUSES every step while this holds —
    # the nav panel / orient scene is invalid during the tunnel and any input is
    # wasted or harmful (operator: "we should NOT move during that screen").
    # Default False = "not wired / never in witchspace" — unit tests proceed.
    in_witchspace: Callable[[], bool] = lambda: False

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

    # True once the no-fire-zone entry text has been seen since the last
    # approach was armed. Set by the dispatcher on
    # ReceiveText Message="$STATION_NoFireZone_entered;" (live-verified
    # 2026-06-07: this fires the instant the ship crosses inside 7.5km).
    # Cleared by clear_no_fire_zone at the start of each dock_approach so the
    # gate only acts on an entry earned by THIS approach run. None supplier
    # (unit tests): falls back to False -> no-fire-zone not yet seen.
    no_fire_zone_supplier: Callable[[], bool] = lambda: False

    # Reset the runner's no-fire-zone flag. step_dock_approach calls this on
    # entry so a stale flag from a prior approach cannot skip the closing leg.
    # No-op default (unit tests with no runner wiring).
    clear_no_fire_zone: Callable[[], None] = lambda: None

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

    # body_tour (opt-in body-touring subsystem). enabled OFF == byte-identical
    # to the pre-feature loop: step_body_tour returns True before any keypress
    # or supplier read. All defaulted -> zero impact on existing test call
    # sites (every test builds StepContext(sender=...)).
    body_tour_enabled: bool = False
    body_tour_dwell_s: float = 2.0
    body_tour_max_bodies: int = 5
    body_tour_max_rows: int = 8
    body_tour_orbit_timeout_s: float = 120.0
    # min FSSDiscoveryScan BodyCount to tour a system (0 = tour every system).
    body_tour_min_bodies: int = 0
    # Latched journal state for the tour, wired by FlowRunner as SUPPLIERS
    # (NOT event_waiters): the hub poll clears a subscriber's queue, so two
    # sequential waiters would lose a drop arriving in the same batch as a
    # Scan miss (PD1). The gate reads these snapshots instead.
    # fss_discovered (D4): advisory honk-complete latch.
    fss_discovered_supplier: Callable[[], bool] = lambda: False
    # FSSDiscoveryScan total BodyCount this system — the body_tour_min_bodies gate.
    fss_body_count_supplier: Callable[[], int] = lambda: 0
    # (seq, frozenset[BodyName]) of AutoScan bodies seen THIS system (D5).
    autoscan_supplier: Callable[[], tuple] = lambda: (0, frozenset())
    # monotone SupercruiseDestinationDrop counter (station/POI drop hint, D2).
    drop_seq_supplier: Callable[[], int] = lambda: 0
    # monotone SupercruiseExit counter — the station-drop re-engage trigger
    # (PD7: re-engage on SupercruiseExit, NOT the drop, so the press happens
    # from real normal space and step_engage_supercruise's in_supercruise
    # short-circuit does not no-op it).
    scex_seq_supplier: Callable[[], int] = lambda: 0

    # IDENTITY targeting (task #45): when BOTH are wired, step_body_tour reads
    # the NAVIGATION panel and targets the next UNEXPLORED in-system body by
    # NAME (cross-ref the autoscan_supplier scanned-set) instead of walking by
    # blind row index. Either one None -> the legacy blind row walk (every unit
    # test). nav_panel_reader is a NavPanelReader (.parse / .read); the grabber
    # returns the nav-panel-region frame. CALIBRATION-PENDING: needs [cv] +
    # tesseract + a live region (see vision/navpanel_reader.py).
    nav_panel_reader: Optional[Any] = None
    nav_panel_grabber: Optional[Callable[[], Any]] = None

    # NAME-DRIVEN dock re-target (Q2 / route-complete redesign): the station NAME
    # the route-complete decision identified (capture-at-plot _dock_target[2], or
    # the settle-loop's resolved Destination.Name). step_dock_target_station OCRs
    # the nav panel (nav_panel_reader+nav_panel_grabber) and targets the ROW whose
    # name best-matches this (navpanel_reader.match_row_by_name), so the bot can
    # temp-target the arrival star to get around it then re-acquire the TRUE
    # station by name. None / no reader / no match -> the legacy SelectTarget ->
    # confirm -> Contacts walk (every existing unit test stays on that path).
    dock_target_name_supplier: Callable[[], Optional[str]] = lambda: None

    # CV-ACTION FAMILY frame sources (#3/#4/#5/#6). BOTH are a FULL-frame
    # (1920x1080, nav panel OPEN) .grab — each consumer crops its OWN region, so
    # cli wires ONE bare full-frame grab to both:
    #   navpanel_detail_grabber -> the detail-page #8 label confirm. nav_target_star
    #     / nav_supercruise_star/_target/_unexplored read the highlighted button's
    #     label (SC_ASSIST / LOCK / UNLOCK) off it BEFORE pressing (the press closes
    #     the detail window, so the confirm must be pre-press). read_detail_button_label
    #     crops LABEL_REGION_FRAC itself.
    #   navpanel_frame_grabber  -> nav_supercruise_unexplored's find_first_unexplored,
    #     which OCRs + crops the nav-LIST region itself (NOT the compass-crop
    #     frame_grabber). None -> "unreadable", never a blind walk.
    # None on both -> the actions fall back (blind press / unreadable), no regression.
    navpanel_detail_grabber: Optional[Callable[[], Any]] = None
    navpanel_frame_grabber: Optional[Callable[[], Any]] = None

    # DOCKED-MENU detector frame source (full-frame BGR .grab). When wired,
    # confirm_menu_item / station_services_macro read the live docked menu via
    # vision.station_menu.detect_menu_item to know which item is highlighted
    # (the undock safety gate + the pad pit-stop entry gate). None -> both steps
    # fail closed (no way to confirm what's highlighted). Wiring the dispatch
    # TRIGGERS (when undock/service fire in run_live) is a separate follow-up;
    # this supplier just lets the steps read the menu when they run.
    station_menu_grabber: Optional[Callable[[], Any]] = None

    # Current ship model — the lowercase journal "Ship" token from the latest
    # LoadGame/Loadout (FlowRunner._current_ship latch). dock_blind_maneuver
    # maps it to a pad-size class for its pitch duration (ship_sizes). None =
    # not wired / no journal yet -> the step uses the MEDIUM default, loudly.
    ship_supplier: Callable[[], Optional[str]] = lambda: None

    # Council B (docking rebuild): the RIGHT-SIDE cockpit target-panel distance
    # in km, or None if unread. step_dock_close_to_range polls this each cycle —
    # a plain numeric poll, mirroring docking_denied_supplier/no_fire_zone_supplier
    # (steps consume a READING; the CV frame-grab + OCR happens upstream, in the
    # FlowRunner wiring, via ed_vision.target_panel_distance.read_target_panel_km).
    # Default (unwired, every unit test) -> None -> every read is "unread" ->
    # step_dock_close_to_range fails closed after its bounded poll ceiling, same
    # as a live unread frame. NEVER the journal NoFireZone signal (operator
    # correction: NFZ is a weapons-off zone, larger than 7.5km, never the
    # docking-range gate) — see docs/superpowers/specs/C7-DOCKING-DISTANCE-FINDING.md.
    dock_distance_km_supplier: Callable[[], Optional[float]] = lambda: None

    # cosmetic EDMCOverlay status writer (None -> no overlay). Duck-typed:
    # .step(proc, action, idx, total). Fail-soft; never blocks a step.
    overlay: Optional[Any] = None

    def log(self, outcome_type: str, payload: Any) -> None:
        if self.record is not None:
            self.record(outcome_type, payload)
