# In-system exploration — Stage-1 design spec (council-ratified baseline)

**Date:** 2026-06-16
**Council:** wf_a29b643c (3rd run). Decision: route_back -> **stage1** — meaning the Stage-0
SPEC is SOUND (spec-conformance PASS); only candidate-local implementation bugs remained, so
no single candidate reached unanimous commit. Supersedes the 2026-06-15 route_back status doc.

**Status:** DESIGN baseline — committable. gen-opus-r2-1 is the implementation baseline (4/5
lenses PASS). BUILD is NOT done; it is gated on the OPEN items below + nav-panel CV calibration.

**Apply these 3 fixes at BUILD time (arbiter ledger):**
1. **dwell pacing:** bind to ctx.body_tour_dwell_s (context.py:149). gen-opus-r2-1 §A.5
   references an unbound dwell_s -> NameError on first body scan (PIN-G: the tour must NEVER raise).
2. **strand-recovery flags:** use StatusFlags.{Docked,Landed,Supercruise}.value from
   ed_core.status.status, NOT magic literals (Docked=0x01, Landed=0x02, Supercruise=0x10). Two
   losing candidates hard-coded 0x02=Docked, which misdiagnoses a docked ship and fires SC-engage
   keypresses at it.
3. **filter sub-screen GuiFocus value** is unverified (1 vs 2). Needs ONE operator in-game read;
   fail-closed in the interim.

**OPEN (operator / game-truth — do not code around):**
- **OPEN-3:** authoritative per-row body-KIND source (station/POI vs planet/moon). Until pinned,
  every row defaults to ORBIT (conservative); a misclassified drop-target costs one wasted
  SC-assist approach that times out into the per-tour exclusion set E. The DROP/S6 branch is dead
  in live flight until the drop-correlation journal signal is pinned.
- The filter sub-screen GuiFocus integer (see fix 3).

---

RATIFIED STAGE-1 SPEC — ED-AFK IN-SYSTEM EXPLORATION TRAVERSAL (ed_explore step_explore)

Status: DESIGN-ONLY, committable as the Stage-1 baseline. Re-run after a Stage-0 route_back. The design CORE (identity-based traversal, one-time permanent filters, journal-only visited-set, fail-closed-per-state, zero wall-clock gates) is RATIFIED. Q1–Q9 are answered (see grounding doc). This spec folds in the seven BINDING contract pins (PIN-A..G) and the SALVAGE set, and supersedes the legacy `step_body_tour` (steps_body_tour.py), which it EVOLVES from. Where legacy contradicts a pin, the pin wins; legacy defects are called out explicitly below.

GROUNDING FACTS (verified by read, do not contradict):
- navpanel_reader.py:178-198 — `_scan_key(name) == _normalize(name)` (canonical-name key); `next_unexplored(bodies, scanned)` returns the first NavBody whose `_scan_key(name)` is NOT in the scanned-set, else None. NavBody is frozen with fields {row_index, name, designator, raw}. `parse_nav_panel_rows` row_index is the ABSOLUTE on-screen index (drives the cursor walk). The READ/OCR layer is CALIBRATION-PENDING; PARSE/SELECT are tested.
- navpanel.py — `engage_supercruise_assist_row(sender, *, sleeper, settle_s, row, pin_to_top, pin_hold_s, panel_focus_action)`: opens panel, `_target_pin_and_walk` (tap-down-once then HOLD-up = the M4 pin; taps WRAP at edges), UI_Select (detail pane), UI_Right (onto LOCK AND SUPERCRUISE), UI_Select (engage), close. Raises KeyError on an unbound action.
- context.py:20 — `StepContext` is `@dataclass` (NON-frozen). It has NO `settle_s`, NO `pin_hold_s`, NO filter-latch field. It HAS body_tour_* tunables and the suppliers: autoscan_supplier()->(seq, frozenset[BodyName]), scex_seq_supplier()->int, drop_seq_supplier()->int, fss_*; plus nav_panel_reader / nav_panel_grabber, exclusive_guard (factory), status_supplier, current_system_supplier, should_abort, event_waiter, sleeper, clock, log, record.
- boot_routes.py:368 — `_route_sc_exit` returns None unless `ev.body_type == 'Star'`. A station/POI SupercruiseExit during exploration has NO automatic recovery route. Confirmed gap → PIN-C.
- steps_body_tour.py LEGACY DEFECTS this spec MUST fix: line 190 `if ctx.scex_seq_supplier() > scex0: outcome='dropped'` is a bare count bump on the ORBIT branch (the BANNED pattern, PIN-A/PIN-B); line 194 `if seq > seq0: outcome='scanned'` is count-based, NOT name-correlated (PIN-A); lines 118/120 `row >= max_rows` / `bodies_toured >= max_bodies` use `>=` (PIN-D wants strict `>` and these must be BACKSTOPS, not the primary exit); the `with _excl():` helper (steps_body_tour.py:80) wraps only S2/S3/S6 body work, NOT a filter set (PIN-F); a station re-engage failure (line 228) only `return True` with NO station-strand recovery (PIN-C).

INTERFACE
  Entry point: `step_explore(ctx: StepContext, *, settle_s=..., pin_hold_s=..., k_start=1, ...) -> bool`
  - Tunables that legacy read off ctx fields that DO NOT exist (settle_s, pin_hold_s) MUST be passed as EXPLICIT keyword args (PIN-E), OR added as real StepContext fields wired in FlowRunner.__init__/_make_context with ONE consistent name each (no ctx.settle_s vs ctx._explore_settle_s split). The spec PERMITS either; it FORBIDS reading a nonexistent attribute.
  - Returns True on every path (best-effort; the tour can NEVER block the resume jump and NEVER raises — PIN-G). False is reserved for nothing the tour itself owns; the only hard-failure SIGNAL the tour emits is a logged event (StationStrandRecover(ok=False), CalibrationFail, etc.), not a return code.
  - Registered into the core step table as `explore` (mirrors register_step("body_tour", ...)).
  - Wired position in the arrival lane: ...arrival-star-orbit -> step_explore -> [station-strand recovery, PIN-C] -> target_next_route -> engage_jump.

PROCEDURE-MATCHER PREDICATES (pure, testable with no frame)
  P-IDENTITY-MODE(ctx) := ctx.nav_panel_reader is not None AND ctx.nav_panel_grabber is not None. Off => spec REQUIRES the tour to no-op-return-True (identity is the only ratified traversal model; the legacy blind row walk is NOT a fallback for exploration — a blind index walk picks the wrong body after a proximity re-sort, Q3).
  P-IS-ORBIT-BODY(kind) := kind in {planet, moon, star, black_hole, wolf_rayet} (Q6). Source of `kind` is OPEN-3 (see OPEN QUESTIONS). Until OPEN-3 resolves, EVERY identity-selected nav row is treated as an ORBIT target (conservative default, PIN-B).
  P-IS-DROP-TARGET(kind) := kind in {station, nav_beacon, poi, carrier} (Q6). DROP targets are present in the panel but, absent OPEN-3, are NOT distinguishable a priori; a misclassified drop-target is handled by the per-tour exclusion set E (PIN-B), never by an orbit-branch scex fallback.
  P-VISIT-COMPLETE-ORBIT(target, seen0, seen_after) := `_scan_key(target.name) in (seen_after - seen0)` (PIN-A; name-correlated per-target snapshot delta). NOT `seq > seq0`. NOT `len(seen_after) > len(seen0)`.
  P-VISIT-COMPLETE-DROP(target, since_snapshot) := a SupercruiseExit event CORRELATED to `target` occurred since the per-target snapshot (PIN-A). A bare `scex_seq > scex0` is INSUFFICIENT and BANNED; correlation requires matching the drop/exit to the locked destination identity (Status.Destination.Name == target.name at drop time, or a SupercruiseDestinationDrop whose body matches target). If correlation cannot be established, treat as NOT-visited (fail-closed → timeout into E).
  P-EXHAUSTED(bodies, scanned, E) := next_unexplored(bodies, scanned | E) is None — i.e. every readable candidate is already scanned OR already in the per-tour exclusion set E. This is the PRIMARY exit (PIN-D), NOT any row count.
  P-CAP-BACKSTOP(n, cap) := n > cap (strict `>`, PIN-D). Applies to max_bodies, max_rows, _iterations_backstop. A backstop firing is logged as an ANOMALY, never the expected terminus.

S0–S6 STATE TABLE (identity-based exits; every state has a fail-closed edge; NO row-count primary exit)
  S0 ENTER / FILTER-GATE (one-time permanent set, PIN-F + the filter contract below)
    - On first-ever run: enter the SET FILTERS screen and run the read-confirm-toggle pass (filter contract). Entire navigation+toggle sequence wrapped in `with _excl(ctx)` (PIN-F). After every re-pin-to-top, reset current_row=0 (PIN-F). On success, persist a one-time "filters established" latch (journal/disk side-state, NOT a StepContext field) so subsequent systems SKIP S0 (Q2 permanent).
    - Forward edge: filters confirmed at desired polarity (ground-truth per-row accuracy gate, PIN-G) -> S1.
    - Fail-closed edge: open_filter / any CV stub raises NotImplementedError or any Exception -> CATCH, log FilterGateFail / CalibrationFail, return True (skip the tour entirely; never raise — PIN-G). A calibration accuracy below threshold (ground-truth, not self-reported confidence) -> same fail-closed exit; the polarity is NOT toggled on an unproven reader (PIN-G).
  S1 READ / SELECT (identity)
    - Grab nav-panel frame, parse rows for current_system, compute target = next_unexplored(bodies, scanned | E).
    - Forward edge: target is not None -> snapshot per-target latches (seen0 = autoscan frozenset, scex0, drop0; capture Status.Destination context for correlation) -> S2.
    - Primary EXIT edge: P-EXHAUSTED true (target is None) -> S_DONE (clean end; log ExploreComplete{bodies_toured}). This is the identity-exhaustion terminator, the intended exit.
    - Fail-closed edge: read/OCR/grab error -> CATCH, log ExploreReadFail, treat as exhaustion-equivalent -> S_DONE (never raise; never fall through to a blind walk).
  S2 PIN + TARGET (cursor walk to target.row_index)
    - Wrapped in `with _excl(ctx)`. Ensure cockpit focus; if focus desync -> log ExploreFocusFail -> S_DONE (best-effort). Pin-to-top then walk to target.row_index (M4 pin; taps WRAP, Q4/Q5).
    - SALVAGE (sonnet-r2-4): if the post-pin fresh row read returns None (the row vanished mid-walk via re-sort) `if fresh_row_index is None: return True`-equivalent — abandon THIS target cleanly, add target.name to E, -> S1 (re-read picks the next; never crash).
    - Forward edge: cursor positioned -> S3.
    - Fail-closed edge: KeyError (unbound action) -> log ExploreBindMissing -> S_DONE. Any read returning null mid-walk -> handle as above (return False from the read helper, NEVER crash — SALVAGE opus-r2-2).
  S3 ENGAGE (lock + SC-Assist, combined single panel open)
    - Wrapped in `with _excl(ctx)`. Per-row focus re-check (SALVAGE opus-r2-2) and filter-focus gate gui_focus==2 where the screen requires it (SALVAGE opus-r2-1). Call engage_supercruise_assist_row(... row=target.row_index, pin_to_top=True, pin_hold_s=<explicit>). Guard RELEASED after the macro so the heat watchdog runs during the gate.
    - Forward edge: macro completed -> S4.
    - Fail-closed edge: KeyError / focus fail -> log + add target to E -> S1 (best-effort; never raise).
  S4 ORBIT-GATE (name-correlated; PIN-A + PIN-B)
    - Poll loop (event_waiter as a poll CADENCE only, never a gate — no wall-clock success gate). Each iteration consults should_abort.
    - Forward edge (ORBIT branch, the ONLY forward edge for an orbit body): P-VISIT-COMPLETE-ORBIT true, i.e. `_scan_key(target.name) in (seen_after - seen0)` -> S5. There is NO scex fallback on this branch (PIN-B): a correctly-classified planet that emits an ambient SupercruiseExit must NOT route to S6.
    - DROP branch (only reachable when target is/becomes a drop-target): P-VISIT-COMPLETE-DROP true (a SupercruiseExit CORRELATED to target) -> S5/S6-drop handling. A bare scex>scex0 is BANNED (PIN-A).
    - Fail-closed / backstop edge: orbit_timeout_s backstop OR _iterations_backstop > _MAX_ITERATIONS (strict `>`, PIN-D) -> log ExploreBodyTimeout, add target.name to E (best-effort; one wasted approach for a misclassified drop-target, PIN-B) -> S1. abort -> log ExploreAborted -> S_DONE.
  S5 RECORD (name-specific; PIN-A)
    - Record the visit ONLY for target.name. The recorded scanned-name MUST be target.name (or the specific member(s) of (seen_after - seen0) that equal _scan_key(target.name)); an INCIDENTAL AutoScan of a non-target body in the same window must NOT record the transit target as scanned nor bump bodies_toured (PIN-A). bodies_toured += 1 only when the target's own scan landed. Add target.name to E (so re-read advances). Optional pacing dwell (NOT a gate). -> S1.
  S6 DROP-RECOVER (station/POI/carrier drop handling + re-engage)
    - Reached when a CORRELATED drop is confirmed for a drop-target. Re-engage supercruise inside `with _excl(ctx)`.
    - Forward edge: re-engage ok -> add target to E -> S1.
    - Fail-closed edge: re-engage NOT ok -> the ship is stranded in real space and boot_routes _route_sc_exit owns ONLY Star drops (PIN-C) -> hand off to the WIRED station-strand recovery step (below); log StationStrandRecover(ok=False) on its failure -> S_DONE.
  S_DONE: log ExploreComplete; return True. Control passes to the wired station-strand recovery sweep, then target_next_route.

FILTER ONE-TIME-PERMANENT-SET CONTRACT (Q1/Q2, PIN-F, PIN-G)
  - Filters are a vertical checkbox list; per-row gesture = select-row + UI_Select toggles in place (filled=on, empty=off). Rows top-to-bottom: Stars, Asteroid Clusters, Planets and Moons, Landfall Planets and Moons, Settlements, Stations, Carriers, Points of Interest, Signal Sources, Systems, BACK.
  - DESIRED ON: Stars, Planets and Moons, Landfall Planets and Moons, Stations, Points of Interest, Systems. DESIRED OFF: Asteroid Clusters, Settlements, Carriers, Signal Sources.
  - Contract = per-row READ-BEFORE-WRITE + READ-AFTER-WRITE CONFIRM: read each checkbox's current polarity; toggle ONLY rows that differ from desired; re-read to confirm. NEVER assume the toggle gesture's polarity.
  - PERMANENT: run ONCE (one-time latch); never re-set per system (Q2). BACK lands on SET FILTERS button; UI_Right (D) re-enters the nav list (Q8).
  - The entire nav+toggle sequence is wrapped in `with _excl(ctx)` (PIN-F: the heat watchdog's DeployHeatSink can interleave mid-walk and desync the cursor over PERMANENT state). Reset current_row=0 after every re-pin-to-top.
  - Any close-screen helper (e.g. a FocusLeftPanel toggle) MUST be DEFINED if called — no undefined symbols (PIN-F).
  - CALIBRATION GATE (PIN-G): a GROUND-TRUTH per-row accuracy measurement of the checkbox-state reader against navpanel_filters_screen.png with KNOWN states — NOT a self-reported frame-confidence score. A systematically-inverted reader passes a confidence gate then latches the wrong polarity permanently; the ground-truth gate is the only defense. Below threshold -> fail-closed, do NOT toggle.

JOURNAL NAME-CORRELATED VISITED-SET CONTRACT (Q7, PIN-A)
  - Visited-set is JOURNAL-ONLY. Bodies: AutoScan `Scan(BodyName)`. Drop-targets: SupercruiseDestinationDrop / Docked / ApproachSettlement.
  - Keying is by CANONICAL NAME via `_scan_key` (== `_normalize(name)`), matching navpanel_reader.next_unexplored. A visit is recorded against the SPECIFIC target identity, never a count.
  - ORBIT completion := `_scan_key(target.name) in (seen_after - seen0)`. DROP completion := a SupercruiseExit/drop CORRELATED to target.name. No count-bump on any branch.
  - The per-tour exclusion set E holds names already attempted (scanned, timed-out, or misclassified) so identity selection ADVANCES instead of re-picking a stuck body; E feeds P-EXHAUSTED.

WIRED STATION-STRAND RECOVERY (PIN-C)
  - A NON-OPTIONAL step, WIRED into the arrival lane BETWEEN step_explore and target_next_route. Rationale: boot_routes._route_sc_exit (boot_routes.py:368) only routes body_type=='Star'; a station/POI/carrier drop during exploration produces no recovery route, so a failed S6 re-engage would strand the ship in real space.
  - It is Status-GATED (reads Status.json flags / journal events, NEVER a wall-clock timed wait): if Status shows the ship in normal space with no active SC-assist after the tour, attempt re-engage; on success continue to target_next_route; on failure log StationStrandRecover(ok=False) and hand the abnormal scene to the existing smack/preempt machinery.
  - It MUST exist whether or not step_explore itself ran a drop (defense in depth) and must be present in the wiring DAG (whole_tree_import_check.py is the authoritative gate).

CALIBRATION + NO-RAISE (PIN-G)
  - open_filter / any CV stub / the nav-panel READ layer must NOT raise out of step_explore: catch NotImplementedError/Exception -> log -> return False from the helper / return True from the step. The tour is best-effort and NEVER raises.
  - The calibration gate is ground-truth per-row accuracy, not frame confidence (see filter contract).

NO ASSUMED ctx FIELDS (PIN-E)
  - The step MUST NOT read ctx.settle_s, ctx.pin_hold_s, or a filter-latch off StepContext — none exist (context.py:20). Pass them as explicit step args, OR add real StepContext fields wired in FlowRunner.__init__/_make_context with ONE consistent name each. The acceptance gate asserts no nonexistent-attribute access.

STRICT `>` ON ALL COUNTERS (PIN-D)
  - max_bodies / max_rows / _iterations_backstop use strict `>` and are BACKSTOPS only. The intended primary exit is P-EXHAUSTED (identity exhaustion / every candidate in E). A backstop firing logs an anomaly.