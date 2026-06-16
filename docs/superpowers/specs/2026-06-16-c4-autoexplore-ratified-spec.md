REVIEWED-SPEC â€” ed_explore step_explore (in-system autoexploration), IMPLEMENT-READY scaffold

PROVENANCE
Derived from the council-ratified Stage-1 baseline docs/superpowers/specs/2026-06-16-explore-insystem-stage1-spec.md (gen-opus-r2-1). This Stage-0 REVIEWED-SPEC pins file:symbol placement, the 4 stub interfaces, registration, and arrival-lane wiring so a builder can land it directly. It does NOT relax any Stage-1 contract; it makes them buildable. Where the task and the baseline agree, the baseline wins; this doc only adds placement + the inert-ship calibration-pending behavior.

GROUNDING FACTS VERIFIED BY READ (do not contradict):
- navpanel_reader.py: NavBody is frozen {row_index:int (ABSOLUTE on-screen index), name:str (==journal Scan.BodyName), designator, raw}. _scan_key(name)==_normalize(name). next_unexplored(bodies, scanned)->first NavBody whose _scan_key NOT in scanned else None. parse_nav_panel_rows is pure/tested. READ layer (read_nav_panel_lines / NavPanelReader.read/.parse) is CALIBRATION-PENDING; DEFAULT_NAV_REGION=(505,435,410,330) is an ESTIMATE (module docstring lines 16-20, 38-46). There is NO body-KIND field on NavBody â€” KIND source is genuinely absent (grounds STUB-1).
- navpanel.py: engage_supercruise_assist_row(sender, *, sleeper, settle_s, row, pin_to_top, pin_hold_s, panel_focus_action) opens panel -> _target_pin_and_walk (tap-down-once then HOLD UI_Up = M4 pin; taps WRAP at edges) -> UI_Select -> UI_Right -> UI_Select -> close. Raises KeyError via sender on an unbound action. _target_pin_and_walk(sender, sleeper, settle_s, rows_down, pin_to_top, pin_hold_s) is the reusable cursor-walk prelude.
- context.py: StepContext is @dataclass (NON-frozen). It HAS NO settle_s, NO pin_hold_s, NO filter-latch field (PIN-E hazard real). It HAS body_tour_dwell_s=2.0 (line 149) and body_tour_* tunables; suppliers autoscan_supplier()->(seq, frozenset[BodyName]), scex_seq_supplier()->int, drop_seq_supplier()->int, fss_*; nav_panel_reader, nav_panel_grabber, exclusive_guard (FACTORY -> CM), status_supplier, current_system_supplier, should_abort, event_waiter, sleeper, clock, log, record.
- status/status.py: StatusFlags(IntFlag) Docked=1<<0, Landed=1<<1, Supercruise=1<<4. Status.gui_focus is the parsed GuiFocus int (Optional). Status.destination is _Destination{system:int, body:int, name:str}. This is the ground-truth source for STUB-3 (filter-screen focus) and DROP correlation (Status.Destination.Name).
- cv_debug_cli.py:309-311 GuiFocus convention: 0=cockpit, 1=right panel, 2=left/NAV panel, 5=station services, 6=galaxy map, 7=system map, 9=FSS. The SET FILTERS sub-screen GuiFocus value is NOT in this map and is UNVERIFIED -> STUB-3.
- boot_routes.py:515 _route_sc_exit returns None unless ev.body_type=='Star' â€” a station/POI/carrier SupercruiseExit during exploration has NO automatic recovery route. This is the gap that mandates the WIRED station-strand recovery (PIN-C).
- arrival.toml: the arrival lane is TOML-driven. step `body_tour` is step 5b (between sc_assist_orbit/wait and target_next_route). All exploration steps are best-effort (required defaults false). step_explore REPLACES the body_tour slot.
- step_registry.py: register_step(name, fn, *, input_exclusive=False) â€” FAIL-ON-DUPLICATE (raises ValueError on a collision). STEP_REGISTRY is the merged action->fn table the interpreter reads. step_body_tour registers "body_tour" at module import.
- ed_explore/__init__.py: activate() imports steps_body_tour as a side effect to register the step. New module follows the same pattern.
- VERIFIED ABSENT (green-field): no symbol step_explore, no register_step("explore",...), no station_strand/strand_recover anywhere in projects/ â€” everything below is new code.

==================================================================
FILE LAYOUT (file:symbol for every piece)
==================================================================
NEW FILE: projects/ed-explore/src/ed_explore/steps_explore.py
  - step_explore(ctx, *, settle_s=0.4, pin_hold_s=4.0, k_start=1, poll_s=0.5, _iterations_backstop=64) -> bool  [ENTRY, the S0..S_DONE driver]
  - _s0_filter_gate(ctx) -> bool                 [S0 ENTER/FILTER-GATE]
  - _s1_read_select(ctx, scanned, E) -> NavBody|None   [S1 READ/SELECT; returns target or None=exhausted]
  - _s2_pin_target(ctx, target, *, settle_s, pin_hold_s) -> bool  [S2 PIN+TARGET; False=abandon-this-target-clean (SALVAGE)]
  - _s3_engage(ctx, target, *, settle_s, pin_hold_s) -> bool      [S3 ENGAGE lock+SC-assist]
  - _s4_visit_gate(ctx, target, snap, *, poll_s) -> str           [S4 ORBIT/DROP gate; returns "scanned"|"dropped"|"timeout"|"abort"]
  - _s5_record(ctx, target, seen_after, seen0)                    [S5 RECORD name-specific]
  - _s6_drop_recover(ctx, target) -> bool                         [S6 DROP-RECOVER re-engage]
  - _excl(ctx) -> contextmanager                                  [guard factory wrapper, identical to steps_body_tour:80-82]
  - _snapshot(ctx, target) -> ExploreSnap                         [per-target latch capture: seen0, scex0, drop0, dest-name]
  - _visit_complete_orbit(target, seen0, seen_after) -> bool      [P-VISIT-COMPLETE-ORBIT predicate]
  - _visit_complete_drop(ctx, target, snap) -> bool               [P-VISIT-COMPLETE-DROP predicate; calls STUB-2]
  - _exhausted(bodies, scanned, E) -> bool                        [P-EXHAUSTED predicate]
  - _identity_mode(ctx) -> bool                                   [P-IDENTITY-MODE predicate]
  - register_step("explore", step_explore)  at module bottom      [REGISTRATION â€” NOT input_exclusive; self-guards per macro so the heat watchdog runs between bodies, mirrors body_tour]

NEW FILE: projects/ed-explore/src/ed_explore/explore_kind.py   [STUB-1 + STUB-2 isolation: operator-blocked CV/journal reads live here, one file to fill]
  - KIND_ORBIT, KIND_DROP        [str sentinels]
  - classify_kind(row: NavBody) -> str   [STUB-1: returns KIND_ORBIT unconditionally + TODO]
  - drop_visited(ctx, target, snap) -> bool  [STUB-2: returns False (fail-closed -> not visited) + TODO]
  - P_IS_ORBIT_BODY(kind)->bool, P_IS_DROP_TARGET(kind)->bool   [pure classifiers over the Q6 kind sets]

NEW FILE: projects/ed-explore/src/ed_explore/explore_filters.py   [STUB-3 isolation: filter-screen focus + the one-time-permanent filter contract]
  - DESIRED_FILTERS: dict[str,bool]   [Stars/Planets and Moons/Landfall Planets and Moons/Stations/Points of Interest/Systems = ON; Asteroid Clusters/Settlements/Carriers/Signal Sources = OFF]
  - filter_screen_focused(ctx) -> bool   [STUB-3: reads Status.gui_focus, compares against an UNVERIFIED FILTER_SCREEN_GUI_FOCUS sentinel = None -> fail-closed False + TODO]
  - read_checkbox_states(ctx) -> dict|None   [CALIBRATION-PENDING CV read; returns None (calibration-pending) by default + TODO]
  - establish_filters(ctx) -> bool   [the read-confirm-toggle pass; returns False (no-op) while reads are calibration-pending]
  - filters_latched() -> bool / mark_filters_latched()   [one-time disk/journal side-state latch, NOT a StepContext field]

NEW FILE: projects/ed-explore/src/ed_explore/steps_strand_recovery.py   [PIN-C: WIRED station-strand recovery step]
  - step_station_strand_recovery(ctx) -> bool   [Status-GATED re-engage sweep]
  - register_step("station_strand_recovery", step_station_strand_recovery)  at module bottom

EDIT: projects/ed-explore/src/ed_explore/__init__.py activate()
  - import steps_explore AND steps_strand_recovery as side effects (register "explore" + "station_strand_recovery"). KEEP the steps_body_tour import (body_tour stays a registered legacy step; the TOML, not the registry, decides which runs).

EDIT: projects/ed-autojump/procedures/arrival.toml
  - replace step 5b `{ action = "body_tour" }` with `{ action = "explore" }`, then INSERT `{ action = "station_strand_recovery" }` immediately after it and BEFORE `target_next_route`. Both best-effort (required defaults false). Net arrival lane: ...sc_assist_orbit -> wait -> explore -> station_strand_recovery -> target_next_route -> set_throttle 100 -> wait -> orient_compass -> orient_widget_ring -> engage_jump -> hold_alignment.

==================================================================
INTERFACE (entry point)
==================================================================
step_explore(ctx: StepContext, *, settle_s: float = 0.4, pin_hold_s: float = 4.0, k_start: int = 1, poll_s: float = 0.5, _iterations_backstop: int = 64) -> bool
  - settle_s and pin_hold_s are EXPLICIT keyword args (PIN-E): the step MUST NOT read ctx.settle_s / ctx.pin_hold_s (they do not exist). dwell pacing binds to ctx.body_tour_dwell_s (context.py:149) â€” NOT an unbound dwell_s (build fix 1).
  - Returns True on EVERY path (best-effort; PIN-G). The step NEVER raises and NEVER returns False â€” False is reserved for nothing the step owns. Hard-failure is a logged event (StationStrandRecover(ok=False), FilterGateFail, CalibrationFail), never a return code.
  - The body backstop counter uses ctx.body_tour_max_bodies / ctx.body_tour_max_rows with strict `>` (PIN-D); _iterations_backstop>cap (strict `>`) is the anti-spin terminator. P-EXHAUSTED is the PRIMARY exit; any backstop firing logs an anomaly.

==================================================================
PROCEDURE-MATCHER PREDICATES (pure, testable with no frame)
==================================================================
P-IDENTITY-MODE(ctx) := ctx.nav_panel_reader is not None AND ctx.nav_panel_grabber is not None. Off -> step_explore no-ops and returns True (identity is the only ratified traversal model; NO blind row-walk fallback for exploration).
P-IS-ORBIT-BODY(kind) := kind in {planet, moon, star, black_hole, wolf_rayet} (Q6).
P-IS-DROP-TARGET(kind) := kind in {station, nav_beacon, poi, carrier} (Q6). kind comes from STUB-1 classify_kind; until filled, classify_kind returns KIND_ORBIT for EVERY row (conservative default, PIN-B).
P-VISIT-COMPLETE-ORBIT(target, seen0, seen_after) := _scan_key(target.name) in (seen_after - seen0). NOT seq>seq0. NOT len()>len(). Name-correlated per-target delta (PIN-A).
P-VISIT-COMPLETE-DROP(target, snap) := a SupercruiseExit/drop CORRELATED to target.name since snap (PIN-A). Bare scex_seq>scex0 is BANNED. Correlation requires matching the locked destination identity to target (Status.Destination.Name == target.name at drop, or a drop event whose body matches target). Delegated to STUB-2 drop_visited; STUB-2 returns False (not-visited / fail-closed) until filled.
P-EXHAUSTED(bodies, scanned, E) := next_unexplored(bodies, scanned | E) is None. PRIMARY exit (PIN-D).
P-CAP-BACKSTOP(n, cap) := n > cap (strict >, PIN-D). Applies to max_bodies, max_rows, _iterations_backstop. A backstop firing is an ANOMALY log, never the expected terminus.

==================================================================
S0-S6 STATE TABLE (identity-based exits; every state fail-closed; NO row-count primary exit)
==================================================================
S0 _s0_filter_gate â€” ENTER / FILTER-GATE (one-time permanent set, PIN-F + PIN-G)
  - If filters_latched() already True -> SKIP to S1 (Q2 permanent).
  - Else enter SET FILTERS (gated on STUB-3 filter_screen_focused), run establish_filters (read-before-write + read-after-write confirm). Whole nav+toggle sequence wrapped in `with _excl(ctx)` (PIN-F). Reset current_row=0 after every re-pin-to-top.
  - Forward edge: filters confirmed at desired polarity by a GROUND-TRUTH per-row accuracy gate (NOT frame-confidence) -> mark_filters_latched() -> S1.
  - Fail-closed edge: STUB-3 / any CV stub returns calibration-pending/None OR raises -> CATCH, log FilterGateFail/CalibrationFail, return True from step_explore (skip the whole tour; never raise). Polarity is NOT toggled on an unproven reader.
  - INERT BEHAVIOR (4 stubs unfilled): filter_screen_focused returns False (fail-closed), establish_filters no-ops -> S0 logs FilterGateFail/CalibrationFail and step_explore returns True. The tour cleanly no-ops to TRAVERSAL.

S1 _s1_read_select â€” READ / SELECT (identity)
  - Grab nav-panel frame, parse rows for current_system, target = next_unexplored(bodies, scanned | E).
  - Forward edge: target is not None -> _snapshot(ctx, target) (seen0, scex0, drop0, Status.Destination context) -> S2.
  - PRIMARY EXIT: P-EXHAUSTED true (target None) -> S_DONE, log ExploreComplete{bodies_toured}.
  - Fail-closed edge: read/OCR/grab error -> CATCH, log ExploreReadFail, treat as exhaustion-equivalent -> S_DONE. NEVER raise, NEVER blind-walk.
  - INERT BEHAVIOR: ctx.nav_panel_grabber/reader return calibration-pending frames -> the reader yields no usable rows OR raises -> ExploreReadFail -> S_DONE -> True. (Even with reader wired but region uncalibrated, this fails closed to TRAVERSAL.)

S2 _s2_pin_target â€” PIN + TARGET (cursor walk to target.row_index)
  - `with _excl(ctx)`. _ensure_cockpit_focus(ctx); focus desync -> log ExploreFocusFail -> S_DONE. Pin-to-top then walk to target.row_index (M4 pin via engage_supercruise_assist_row's prelude / _target_pin_and_walk; taps WRAP, Q4/Q5).
  - SALVAGE: if a post-pin fresh row read returns None (row vanished mid-walk via re-sort) -> abandon THIS target cleanly, add target.name to E, -> S1 (re-read picks next; never crash). The read helper returns None, the caller does NOT raise.
  - Forward edge: cursor positioned -> S3.
  - Fail-closed edge: KeyError (unbound action) -> log ExploreBindMissing -> S_DONE.

S3 _s3_engage â€” ENGAGE (lock + SC-Assist, single combined panel open)
  - `with _excl(ctx)`. Per-row focus re-check; STUB-3 filter-focus gate where the screen requires it. engage_supercruise_assist_row(ctx.sender, sleeper=ctx.sleeper, settle_s=settle_s, row=target.row_index, pin_to_top=True, pin_hold_s=pin_hold_s). Guard RELEASED after the macro so the heat watchdog runs during the gate.
  - Forward edge: macro completed -> S4.
  - Fail-closed edge: KeyError / focus fail -> log + add target.name to E -> S1 (never raise).

S4 _s4_visit_gate â€” ORBIT-GATE (name-correlated; PIN-A + PIN-B)
  - Poll loop. event_waiter is a poll CADENCE only, never a success gate (no wall-clock success gate). Each iteration consults should_abort. _iterations_backstop > _MAX (strict >, anti-spin terminator) is a backstop only.
  - ORBIT forward edge (the ONLY forward edge for an orbit body): P-VISIT-COMPLETE-ORBIT true -> S5. NO scex fallback on this branch (PIN-B): an ambient SupercruiseExit on a correctly-classified planet must NOT route to S6.
  - DROP branch (only reachable when target is/becomes a drop-target per STUB-1): P-VISIT-COMPLETE-DROP true (correlated drop via STUB-2) -> S6.
  - Fail-closed/backstop: ctx.body_tour_orbit_timeout_s backstop OR iterations>backstop -> log ExploreBodyTimeout, add target.name to E -> S1 (one wasted approach for a misclassified drop-target, PIN-B). abort -> log ExploreAborted -> S_DONE.
  - INERT BEHAVIOR: classify_kind=ORBIT for all rows, so a real station target sits in the ORBIT branch, never scans, times out into E -> S1. The DROP/S6 branch is dead in live flight until STUB-1+STUB-2 are filled â€” by design.

S5 _s5_record â€” RECORD (name-specific; PIN-A)
  - Record the visit ONLY for target.name (or the specific member(s) of (seen_after - seen0) equal to _scan_key(target.name)). An INCIDENTAL AutoScan of a non-target body in the same window must NOT record the transit target as scanned nor bump bodies_toured. bodies_toured += 1 only when the target's own scan landed. Add target.name to E. Optional pacing dwell ctx.body_tour_dwell_s (NOT a gate). -> S1.

S6 _s6_drop_recover â€” DROP-RECOVER (station/POI/carrier drop handling + re-engage)
  - Reached on a CORRELATED drop for a drop-target. Re-engage supercruise inside `with _excl(ctx)` (step_engage_supercruise, presses>1).
  - Forward edge: re-engage ok -> add target.name to E -> S1.
  - Fail-closed edge: re-engage NOT ok -> ship stranded in real space; boot_routes._route_sc_exit owns ONLY Star drops (PIN-C) -> the WIRED station_strand_recovery step (next in the arrival lane) owns recovery; log StationStrandRecover(ok=False) on its failure -> S_DONE.

S_DONE: log ExploreComplete{bodies_toured}; return True. Control passes to station_strand_recovery, then target_next_route.

==================================================================
THE 4 OPERATOR-BLOCKED STUBS (clear interface + TODO; do NOT guess)
==================================================================
STUB-1 (OPEN-3) body-KIND per nav-panel row â€” explore_kind.classify_kind(row: NavBody) -> str
  def classify_kind(row):
      # TODO(operator): how to read body KIND per nav-panel row? planet/moon/star=ORBIT, station/POI/beacon/carrier=DROP (Q6).
      # No KIND field exists on NavBody and no per-row kind source is known. Until pinned, DEFAULT ORBIT-conservative (PIN-B):
      # a misclassified drop-target costs one wasted SC-assist approach that TIMES OUT into the exclusion set E.
      return KIND_ORBIT
  Contract: total function over NavBody -> {KIND_ORBIT, KIND_DROP}; pure; never raises; the ONLY caller is S4's branch selector. The DROP/S6 branch is dead until this returns KIND_DROP for real drop-targets.

STUB-2 DROP-target visited journal signal â€” explore_kind.drop_visited(ctx, target, snap) -> bool
  def drop_visited(ctx, target, snap):
      # TODO(operator): which journal event marks a station/POI/beacon DROP visited? (SupercruiseDestinationDrop / Docked / ApproachSettlement candidates, Q7).
      # Correlation MUST match the locked destination identity to target.name (Status.Destination.Name == target.name at drop), NEVER a bare scex_seq>scex0 (BANNED, PIN-A).
      return False   # fail-closed: not-visited -> S4 times the drop-target out into E
  Contract: pure read over ctx suppliers + snap; never raises; False until filled; a True REQUIRES name correlation.

STUB-3 SET FILTERS sub-screen GuiFocus â€” explore_filters.filter_screen_focused(ctx) -> bool
  FILTER_SCREEN_GUI_FOCUS = None   # TODO(operator): GuiFocus int on the SET FILTERS screen? (verified map: 0 cockpit/2 NAV panel/5 services/6 galaxy/7 system map/9 FSS â€” the filter sub-screen value is UNVERIFIED; build fix 3, baseline says 1-vs-2 unverified)
  def filter_screen_focused(ctx):
      if FILTER_SCREEN_GUI_FOCUS is None:
          return False   # fail-closed in the interim (build fix 3): never toggle PERMANENT state on an unconfirmed screen
      st = ctx.status_supplier()
      return st is not None and getattr(st, "gui_focus", None) == FILTER_SCREEN_GUI_FOCUS
  Contract: never raises; False (fail-closed) until the integer is pinned -> S0 fails closed -> tour no-ops.

STUB-4 nav-panel OCR REGION calibration â€” flagged, not a function stub
  navpanel_reader.DEFAULT_NAV_REGION=(505,435,410,330) is an ESTIMATE. step_explore's reads are CALIBRATION-PENDING. The step logs ExploreCalibrationPending once per run when reads yield no rows under a wired-but-uncalibrated reader, and S1's fail-closed edge routes to S_DONE.
  TODO(operator): provide a live planet-rich nav-panel frame to calibrate the reader region + row/column crops (no such fixture exists; the 2026-06-08 sample was 2 stars).
  Contract: NO code defends a wrong region; calibration is operator-supplied. The step is SAFE inert because S1 fails closed on an unusable read.

INERT-SHIP GUARANTEE (all 4 stubs unfilled): step_explore runs S0 -> S0 fails closed at STUB-3 (filter_screen_focused False) -> log FilterGateFail -> return True. Even if S0 were bypassed, S1 fails closed on the calibration-pending read (STUB-4) -> S_DONE -> True. The tour cleanly no-ops to TRAVERSAL. It becomes live when the operator fills the 4 stubs. It is SAFE TO SHIP INERT.

==================================================================
WIRED STATION-STRAND RECOVERY (PIN-C) â€” steps_strand_recovery.step_station_strand_recovery
==================================================================
- NON-OPTIONAL, WIRED between explore and target_next_route in arrival.toml. Rationale: boot_routes._route_sc_exit (boot_routes.py:515) only routes body_type=='Star'; a station/POI/carrier drop produces no recovery route.
- Status-GATED (Status.json flags / journal events, NEVER a wall-clock wait): read ctx.status_supplier(). Detect stranded-in-normal-space via StatusFlags from ed_core.status.status (NOT magic literals â€” build fix 2): NOT Supercruise AND NOT Docked AND NOT Landed, i.e. ship in normal space with no active SC-assist after the tour.
  stranded := st is not None AND not st.flag(StatusFlags.Supercruise) AND not st.flag(StatusFlags.Docked) AND not st.flag(StatusFlags.Landed)
- If stranded -> attempt re-engage (step_engage_supercruise, presses>1, between_press_s spacers). On success -> continue (return True). On failure -> log StationStrandRecover(ok=False) and hand the abnormal scene to the existing smack/preempt machinery; return True.
- If NOT stranded (the normal in-SC tour exit) -> no-op return True.
- MUST exist whether or not explore ran a drop (defense in depth). MUST be present in the wiring DAG (whole_tree_import_check.py is the authoritative gate). Best-effort: NEVER raises, NEVER returns False.

==================================================================
FILTER ONE-TIME-PERMANENT-SET CONTRACT (Q1/Q2, PIN-F, PIN-G) â€” explore_filters
==================================================================
- Vertical checkbox list; per-row gesture = select-row + UI_Select toggles in place (filled=on, empty=off). Rows top-to-bottom: Stars, Asteroid Clusters, Planets and Moons, Landfall Planets and Moons, Settlements, Stations, Carriers, Points of Interest, Signal Sources, Systems, BACK.
- DESIRED ON: Stars, Planets and Moons, Landfall Planets and Moons, Stations, Points of Interest, Systems. DESIRED OFF: Asteroid Clusters, Settlements, Carriers, Signal Sources.
- Contract = per-row READ-BEFORE-WRITE + READ-AFTER-WRITE CONFIRM: read each checkbox polarity; toggle ONLY rows that differ; re-read to confirm. NEVER assume the toggle gesture's polarity.
- PERMANENT: run ONCE (one-time latch filters_latched/mark_filters_latched, disk/journal side-state, NOT a StepContext field). Never re-set per system (Q2). BACK lands on SET FILTERS button; UI_Right re-enters the nav list (Q8).
- Entire nav+toggle sequence wrapped in `with _excl(ctx)` (PIN-F: heat watchdog DeployHeatSink can interleave and desync the cursor over PERMANENT state). Reset current_row=0 after every re-pin-to-top.
- Any close-screen helper (e.g. a FocusLeftPanel toggle) MUST be DEFINED if called â€” no undefined symbols (PIN-F).
- CALIBRATION GATE (PIN-G): a GROUND-TRUTH per-row accuracy measurement of the checkbox-state reader against navpanel_filters_screen.png with KNOWN states â€” NOT a self-reported frame-confidence score. Below threshold -> fail-closed, do NOT toggle.

==================================================================
JOURNAL NAME-CORRELATED VISITED-SET CONTRACT (Q7, PIN-A)
==================================================================
- Visited-set is JOURNAL-ONLY. Bodies: AutoScan Scan(BodyName) via ctx.autoscan_supplier(). Drop-targets: the STUB-2 event(s) (SupercruiseDestinationDrop / Docked / ApproachSettlement â€” to be pinned).
- Keyed by CANONICAL NAME via _scan_key (==_normalize), matching next_unexplored. A visit is recorded against the SPECIFIC target identity, never a count.
- ORBIT completion := _scan_key(target.name) in (seen_after - seen0). DROP completion := a drop CORRELATED to target.name. No count-bump on any branch.
- Per-tour exclusion set E holds names already attempted (scanned, timed-out, or misclassified) so identity selection ADVANCES; E feeds P-EXHAUSTED.

==================================================================
REGISTRATION + ARRIVAL-LANE WIRING
==================================================================
- register_step("explore", step_explore) at the bottom of steps_explore.py (NOT input_exclusive). register_step("station_strand_recovery", step_station_strand_recovery) at the bottom of steps_strand_recovery.py.
- ed_explore.activate() imports steps_explore + steps_strand_recovery (and keeps steps_body_tour). FAIL-ON-DUPLICATE is satisfied: "explore" and "station_strand_recovery" are new, disjoint names.
- arrival.toml: replace `{ action = "body_tour" }` (step 5b) with `{ action = "explore" }`, INSERT `{ action = "station_strand_recovery" }` after it, before target_next_route. Both best-effort. The far-star automatic skip (arrival step 3 skip_to="target_next_route" with max_rows=3) vaults BOTH explore and strand-recovery on a far star, exactly as it vaulted body_tour â€” preserved by ordering them between sc_assist_orbit and target_next_route.


## INTERFACE

step_explore(ctx: StepContext, *, settle_s: float = 0.4, pin_hold_s: float = 4.0, k_start: int = 1, poll_s: float = 0.5, _iterations_backstop: int = 64) -> bool
  # ed_explore/steps_explore.py. Best-effort: returns True on EVERY path, never raises, never returns False. settle_s/pin_hold_s are EXPLICIT kwargs (PIN-E); dwell binds ctx.body_tour_dwell_s. registered as "explore".

step_station_strand_recovery(ctx: StepContext) -> bool
  # ed_explore/steps_strand_recovery.py. Status-GATED (StatusFlags.{Supercruise,Docked,Landed}.value, not magic literals). registered as "station_strand_recovery". Best-effort: never raises, never False.

# STUBS (operator fills):
classify_kind(row: NavBody) -> str                 # explore_kind.py â€” returns KIND_ORBIT + TODO(body kind per row?)
drop_visited(ctx: StepContext, target: NavBody, snap) -> bool   # explore_kind.py â€” returns False + TODO(which journal event marks a drop visited?)
filter_screen_focused(ctx: StepContext) -> bool    # explore_filters.py â€” FILTER_SCREEN_GUI_FOCUS=None -> False + TODO(GuiFocus int on SET FILTERS?)
# STUB-4: navpanel_reader.DEFAULT_NAV_REGION is an estimate -> reads are CALIBRATION-PENDING; TODO(live planet-rich frame to calibrate region + crops)

# Reused (do not modify): navpanel_reader.next_unexplored / _scan_key / parse_nav_panel_rows; navpanel.engage_supercruise_assist_row; steps_shared._ensure_cockpit_focus / step_engage_supercruise; status.StatusFlags.



## INVARIANTS

### [1]
NEVER-RAISE: step_explore and step_station_strand_recovery return True on every path; every internal failure (CV stub, KeyError, OCR miss, focus desync, NotImplementedError) is caught and logged, never propagated (PIN-G). The tour can never block the onward jump.

### [2]
NEVER-FALSE: neither step returns False; the only hard-failure SIGNAL is a logged event (FilterGateFail, CalibrationFail, ExploreReadFail, StationStrandRecover(ok=False)).

### [3]
IDENTITY-ONLY: with P-IDENTITY-MODE false, step_explore no-ops and returns True; there is NO blind row-index walk fallback for exploration (a blind walk picks the wrong body after a proximity re-sort, Q3).

### [4]
NAME-CORRELATED COMPLETION: ORBIT completion is _scan_key(target.name) in (seen_after-seen0); DROP completion is a drop CORRELATED to target.name. A bare seq>seq0 / scex>scex0 / len()>len() is BANNED on every branch (PIN-A).

### [5]
NO-SCEX-FALLBACK ON ORBIT: a correctly-classified orbit body that emits an ambient SupercruiseExit must NOT route to the DROP/S6 branch (PIN-B).

### [6]
PRIMARY EXIT IS P-EXHAUSTED: next_unexplored(bodies, scanned|E) is None ends the tour; max_bodies/max_rows/_iterations_backstop are strict-`>` BACKSTOPS only and log an anomaly when they fire (PIN-D).

### [7]
NO WALL-CLOCK SUCCESS GATE: event_waiter is a poll cadence; orbit_timeout_s and _iterations_backstop are failure backstops, never success gates; the anti-spin terminator is the iteration backstop with strict `>`.

### [8]
ONE-TIME PERMANENT FILTERS: establish_filters runs once behind a disk/journal latch (NOT a StepContext field); subsequent systems skip S0; the toggle is read-before-write + read-after-write confirm; never toggled on an unproven (ground-truth-failing) reader (PIN-F/PIN-G).

### [9]
NO ASSUMED ctx FIELDS: the step never reads ctx.settle_s / ctx.pin_hold_s / a filter-latch off StepContext (none exist); dwell binds ctx.body_tour_dwell_s (PIN-E + build fix 1).

### [10]
STRAND-RECOVERY USES REAL FLAGS: stranded-detection uses StatusFlags.{Supercruise,Docked,Landed}.value from ed_core.status.status, never magic literals 0x01/0x02/0x10 (build fix 2).

### [11]
EXCLUSIVE-GUARD WRAPS UI WORK ONLY: S0 filter walk + S2 pin + S3 engage + S6 re-engage run inside `with _excl(ctx)`; the guard is RELEASED before the visit gate so the heat watchdog runs during the gate; the explore step itself is NOT input_exclusive.

### [12]
INERT-WHEN-STUBBED: with the 4 stubs unfilled, step_explore fails closed at S0 (STUB-3) or S1 (STUB-4 calibration-pending) and no-ops to TRAVERSAL; it is safe to ship inert and goes live only when the operator fills the stubs.

### [13]
WIRED IN THE DAG: explore + station_strand_recovery are registered and present in arrival.toml between sc_assist_orbit and target_next_route; whole_tree_import_check.py is the authoritative wiring gate; nothing stays unwired.



## ACCEPTANCE_CRITERIA

### [1]
step_explore is registered as 'explore' via register_step (not input_exclusive) and ed_explore.activate() imports steps_explore so the name appears in STEP_REGISTRY; the name is disjoint from 'body_tour' (no fail-on-duplicate).

### [2]
step_station_strand_recovery is registered as 'station_strand_recovery', imported by activate(), and present in arrival.toml between explore and target_next_route.

### [3]
arrival.toml step 5b is `{ action = "explore" }` (replacing body_tour), followed by `{ action = "station_strand_recovery" }`, both best-effort (no required=true); the far-star skip (step 3 skip_to=target_next_route) still vaults both.

### [4]
step_explore reads ctx.body_tour_dwell_s for dwell and takes settle_s/pin_hold_s as explicit kwargs; it never accesses ctx.settle_s, ctx.pin_hold_s, or a StepContext filter-latch (static check / attribute-access test passes).

### [5]
With all 4 stubs unfilled, step_explore returns True and performs NO SC-assist keypress against a body: S0 fails closed at filter_screen_focused()==False (FilterGateFail logged) OR S1 fails closed on a calibration-pending read (ExploreReadFail) -> S_DONE. The arrival lane reaches target_next_route -> engage_jump unobstructed.

### [6]
step_explore never raises and never returns False on any injected-fault path (KeyError from sender, OCR exception, focus desync, stub NotImplementedError) â€” every fault is caught, logged, and the step returns True.

### [7]
ORBIT completion is keyed on _scan_key(target.name) in (seen_after-seen0): an incidental AutoScan of a NON-target body in the gate window does NOT record the target as scanned and does NOT bump bodies_toured; a bare seq-bump never satisfies completion.

### [8]
A correctly-classified ORBIT target that emits an ambient SupercruiseExit does NOT route to S6 (no scex fallback on the orbit branch).

### [9]
DROP completion requires STUB-2 drop_visited returning True with name correlation (Status.Destination.Name == target.name); a bare scex_seq>scex0 never marks a drop visited; with STUB-2 unfilled, a station target sits in the ORBIT branch (STUB-1 default), never scans, and times out into E.

### [10]
P-EXHAUSTED (next_unexplored over scanned|E is None) is the path that logs ExploreComplete; max_bodies/max_rows/_iterations_backstop use strict `>` and, when they fire, log an anomaly distinct from the clean exhaustion exit.

### [11]
The S4 gate uses event_waiter only as a poll cadence and the iteration backstop (strict `>`) as the anti-spin terminator; no branch uses a wall-clock elapsed-time comparison as a SUCCESS gate.

### [12]
establish_filters runs once behind filters_latched(); a second system with the latch set skips S0; the toggle path is read-before-write + read-after-write confirm and is NOT entered when the ground-truth accuracy gate fails.

### [13]
S2 pin/walk re-sort safety: a post-pin fresh-row read returning None abandons the target cleanly (adds target.name to E, returns to S1) without raising.

### [14]
step_station_strand_recovery detects stranded-in-normal-space using StatusFlags.{Supercruise,Docked,Landed}.value (no magic literals); when stranded it attempts re-engage and on failure logs StationStrandRecover(ok=False); when in SC it no-ops; it never raises or returns False.

### [15]
Each of the 4 stubs exposes the specified signature with an inline TODO marker carrying the exact operator question, and defaults to the conservative fail-closed value (classify_kind->KIND_ORBIT, drop_visited->False, filter_screen_focused->False via FILTER_SCREEN_GUI_FOCUS=None, DEFAULT_NAV_REGION flagged calibration-pending).



## OPEN_QUESTIONS

### [1]
OPEN-3 / STUB-1: authoritative per-row body-KIND source on the NAVIGATION tab (planet/moon/star=ORBIT vs station/POI/beacon/carrier=DROP, Q6). NavBody carries no KIND field and no per-row kind read is known. Until pinned, classify_kind defaults ORBIT-conservative and the DROP/S6 branch is dead in live flight. TODO: how to read body kind per nav-panel row?

### [2]
STUB-2: which journal event marks a station/POI/beacon DROP as visited, and how to correlate it to target.name (SupercruiseDestinationDrop / Docked / ApproachSettlement are candidates; correlation must use Status.Destination.Name == target.name, never a bare scex count). TODO: which journal event marks a drop visited?

### [3]
STUB-3 / build fix 3: the SET FILTERS sub-screen GuiFocus integer. Verified map has 0 cockpit / 2 NAV panel / 5 services / 6 galaxy / 7 system map / 9 FSS; the filter sub-screen value is unverified (baseline notes 1-vs-2). Needs ONE operator in-game read; fail-closed in the interim. TODO: GuiFocus int on the SET FILTERS screen?

### [4]
STUB-4: a live planet-rich nav-panel frame to calibrate navpanel_reader's DEFAULT_NAV_REGION=(505,435,410,330) and the row/column crops (the only existing sample was 2 stars). Until provided, step_explore's reads are calibration-pending and fail closed to TRAVERSAL. TODO: operator nav-panel frame to calibrate the reader region + row/column crops.

### [5]
Filter-screen close/re-enter gestures (Q8: BACK lands on SET FILTERS button; UI_Right re-enters the nav list) need confirmation that they hold with the bundled preset's bindings before establish_filters is wired live; any close-screen helper called must be defined (PIN-F).

### [6]
The ground-truth per-row checkbox-state accuracy THRESHOLD for the PIN-G calibration gate (the value below which establish_filters refuses to toggle) is operator/calibration-defined, not specified here; it must be a measured per-row accuracy against navpanel_filters_screen.png, never a frame-confidence score.

