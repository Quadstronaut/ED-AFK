# BC1 Body-Touring — SPEC BLOCKED: open questions for Operator

**Status:** spec gate did NOT pass (3-council, 0/3 approve). The council stopped
BEFORE plan/implement (by design) and surfaced blockers instead of guessing.
Authored 2026-06-08. BC1 resumes once Operator answers the in-game tests below.

The blockers are real and several depend on **unverified ED mechanics** — per the
standing rule ([[ask-kyle-to-test-game-mechanics]], [[no-assumptions-ever]]), these
must be tested in-game, not coded against an assumption.

---

## A. In-game tests I need from Operator (numbered — the gating mechanics)

**TEST 1 — THE CRITICAL ONE. SC-Assist toward a planet: drop or orbit?**
Lock a planet/moon as your target and engage Supercruise Assist toward it. Does the
ship **auto-drop into normal space at the body** (a `SupercruiseExit`), or does it
**stay in supercruise and orbit/hold** near it?
→ This reshapes the entire per-body loop. If it DROPS, each next body needs a fresh
supercruise re-engage (from normal space); if it ORBITS, the loop is just
lock→assist→dwell→next, all in supercruise. The spec cannot be correct without this.

**TEST 2 — Does the SC-Assist control even exist for a non-star body?**
Open the left nav panel, `UI_Select` a planet/moon row. Does its detail pane have the
**"LOCK AND SUPERCRUISE"** (assist) option in the same position a star's does (one
`UI_Right` then `UI_Select`)? Or is the planet pane laid out differently?
→ The existing `engage_supercruise_assist` macro assumes the star layout; if planets
differ, the engage primitive needs a different walk.

**TEST 3 — Nav-panel cursor after close/reopen (confirm the memory).**
After honking, open the nav panel, walk DOWN to a non-top body (say row 3), then CLOSE
and immediately RE-OPEN the panel. Is the cursor still on that body, or reset to the
top? (Existing memory [[ed-navpanel-cursor-mechanics]] says the cursor *persists* — this
test confirms it persists at the **last-walked row** after our `target_via_navpanel`
close sequence specifically, which decides whether lock-then-engage can re-open safely
or needs a single combined open.)

**TEST 4 (journal-checkable — I can do this if you point me at a session).**
When you drop at a planet, does `SupercruiseDestinationDrop` carry the **body name** in
its `Type` field (like it does the station name)? Determines the arrive-at-body gate.

## B. Design decisions for Operator (not mechanics — your call)

- **Far-star arrival skips the tour.** `arrival.toml`'s bounded star-lock vaults to
  `target_next_route` when the primary star is far (the skip_to gate). That means in a
  system where you arrive far from the primary, the body tour would never run. Intended
  (tour only when the star is close), or should the tour run regardless?
- **7-second dwell** — is ~7s confirmed enough at each body to register the proximity
  auto-scan (the explorer data), or does it need tuning per body size/distance?

## C. Implementation-completeness gaps (for the fast re-spec, once A is answered)

The council also flagged spec gaps that are mechanical to close once the mechanics are
known — folding these into the next spec round:

1. **Combined lock+engage primitive.** `engage_supercruise_assist` opens the panel and
   acts on the highlighted row; if the cursor doesn't reliably sit on the just-locked
   body, the lock and the engage must happen in **one** panel open (walk to row k →
   `UI_Select` → `UI_Right` → `UI_Select`) rather than two — a new `navpanel.py` fn.
2. **`FSSDiscoveryScan` gate must read a latched flag, not an event-waiter.** The honk
   event is consumed by the hub fan-out before `body_tour` runs; set
   `self._fss_discovered` in `_apply_state` (like `_arrival_star_class`) and expose it
   via a supplier — an `event_waiter` query would miss the already-consumed event and
   could deadlock.
3. **Enumerate the new wiring explicitly:** `STEP_REGISTRY.update({"body_tour": ...})`,
   `INPUT_EXCLUSIVE_ACTIONS += body_tour`, new `StepContext` fields + `FlowRunner`
   params + `_make_context` lambdas (and the test-call-site surface that touches).
4. **Per-body arrival gate** uses `SupercruiseDestinationDrop.type` matched to the body
   name (pending Test 4).

## D. Why this is the right call

BC1 is the marquee feature and opt-in by design — it deserves verified mechanics, not an
overnight implementation committed against a guess about how SC-Assist treats planets. The
gate worked: it caught a design that would have engaged the assist toward the wrong body
or mis-modeled the drop/orbit behavior. Answer §A and the re-spec → plan → implement runs
clean.

---

## Round 2 (2026-06-08) — v2 spec ALSO blocked (1/3): new mechanic + a latent bug

The re-spec (drop-or-orbit-agnostic loop, combined lock+engage primitive, latched FSS
flag — all the round-1 directives applied) hit a NEW unverified mechanic and surfaced a
real latent safety gap. BC1 clearly depends on a *cluster* of interlocking ED mechanics;
rather than grind more spec rounds one-unknown-at-a-time, **BC1 needs ONE in-game test
session answering all of Tests 1-5 together**, then spec->plan->implement runs clean.

### NEW in-game test for Operator
**TEST 5 — Planet/moon `Body` field when SC-assist-locked.** Lock a planet/moon via the
nav panel (LOCK AND SUPERCRUISE), then read `Status.json` -> `Destination.Body`. Is it
**non-zero** (like a station) or **0** (like a star / FSD route hop)?
-> The per-body arrival-confirm predicate `_dest_is_named_station` requires `Body != 0`.
If planets lock with `Body = 0`, that predicate fails on EVERY body and the tour
terminates on the first one — we'd need a name-only confirm (no Body gate). Load-bearing;
verify before building. (Confirmed for stations only today; dispatcher.py:241 even marks
the station case "UNCONFIRMED from journals".)

### LATENT SAFETY GAP (independent of BC1 — [[ed-fsd-masslock-realspace]] already flagged it)
**`step_engage_jump` (steps.py:149) does not check `in_supercruise`.** It gates on
docked / fsd_charging / fsd_cooldown / fsd_mass_locked / overheating — but NOT supercruise.
In the drop model (or any normal-space-at-jump edge), it would press SetSpeed100 +
Hyperspace in normal space; ED silently refuses and the route stalls. Correct fix: add an
`in_supercruise` guard to `engage_jump` (you can only hyperspace FROM supercruise, so this
is right in general), OR an `in_supercruise` check at the end of `step_body_tour` before it
returns True. Tracked as its own safety task — deserves a dedicated council, not a hasty
fold-in, and the running bot does not hit it (arrival is always in supercruise at the jump).

### DESIGN DECISION (heat watchdog)
`body_tour` in `INPUT_EXCLUSIVE_ACTIONS` pauses the heat watchdog for the WHOLE multi-minute
tour. Better: wrap each per-body lock+engage individually so heat protection stays live
between bodies. Council's lean; flag for Operator.

### Net
Gating cluster = Tests 1, 2, 3 (round 1) + Test 5 (round 2), plus the engage_jump guard
(do independently). One in-game session clears the cluster; then BC1 builds in one pass.

---

## ANSWERS RECEIVED (2026-06-08) — Tests 1-4 resolved, v3 relaunched

Operator (ground truth) + journal-verified by me:
- **TEST 1:** SC-assist **ORBITS all bodies** in supercruise (no drop). Only stations/POI/
  signal-sources DROP to real space. => PURE ORBIT model; the tour never leaves supercruise.
- **TEST 2:** LOCK-AND-SUPERCRUISE is the same detail-pane spot + keystrokes
  (UI_Select -> UI_Right -> UI_Select) on all bodies.
- **TEST 3:** nav-panel cursor persists until a system jump.
- **TEST 4 (journal-verified):** `SupercruiseDestinationDrop.Type` = station/POI name + a
  `MarketID`; fires for stations/POI ONLY (bodies emit none). Per-body confirm = the
  `Scan` `ScanType:"AutoScan"` event (carries `BodyName`) — which is ALSO the exploration data.
- **TEST 5 (Destination.Body):** deferred by Operator AND now MOOT — gate per-body on the
  AutoScan Scan event, never `Destination.Body`.

=> **BC1 v3 relaunched** (pure orbit model + AutoScan-gated per-body confirm + station-drop
recovery + combined lock+engage). The `engage_jump` in_supercruise gap (#44) does NOT bite
BC1 (tour stays in supercruise). Confirmed mechanics saved to memory
`sc-assist-orbit-vs-drop-mechanics`.

---

## v3 outcome (2026-06-08): SPEC PASSED (3/3), PLAN blocked 2/3 on the station-drop edge

Big progress: with mechanics confirmed, the **spec passed unanimous** and verified correct
against the real code (executor/navpanel.py path; INPUT_EXCLUSIVE wraps the WHOLE step so
body_tour must NOT be in it; _TailHub broadcasts to all subscribers; arrival.toml skip_to
vaults by NAME so the insertion is safe; StepContext new fields = zero test blast radius;
ExplorationConfig is in the load_config merge list; _apply_state FSDJump is the reset point).
**The pure body-orbit happy path is clean.**

The PLAN stalled at 2/3 TWICE — both times on the **station/POI drop-recovery path (D2)**.
The body-orbit feature itself is fine; SC-assisting a station (which DROPS) is the intricate
edge:
- Round A (fixed): the per-body gate used two event_waiter calls; a drop in the same batch as
  a Scan-miss got consumed by the queue clear. Fixed with a drop-latch supplier (drop_seq).
- Round B (OPEN): `SupercruiseDestinationDrop` fires ~5s BEFORE `SupercruiseExit`, so when the
  drop-latch triggers, `in_supercruise` is STILL true -> `step_engage_supercruise`
  short-circuits (no-op) -> the ship drops 5s later -> the tour continues from NORMAL SPACE,
  breaking the pure-orbit guarantee. **Council's fix:** gate the "dropped" outcome on
  `SupercruiseExit` (a scex_seq supplier), NOT `SupercruiseDestinationDrop`; and wrap
  `_ensure_cockpit_focus` + the re-engage in the exclusive guard.

STOPPED here — three full rounds is the right autonomous limit. The fix is small + clear.
Options for Operator:
- **(A) one focused resume** with the SupercruiseExit-gate fix — likely lands it.
- (B) drop the station-recovery for v1 — but bodies vs stations are not cleanly
  pre-distinguishable in the nav panel, so this is not obviously simpler.
- (C) Operator reviews spec+plan and we implement the drop-recovery together.
Recommend (A) on Operator's go. Spec is locked; only the D2 path needs this one fix.

---

## LIVE TEST RESULT (2026-06-08) — tour macro fires, but the ship never flies to a body

First-ever live run (body_tour_enabled=true, min_bodies=0, "scan everything"). Result:
**the tour does not tour.** On each arrival the body_tour step ran and the lock+engage
macro fired the CORRECT keys (keylog-confirmed: FocusLeftPanel -> UI_Down -> UI_Up pin ->
walk -> UI_Select -> UI_Right -> UI_Select -> FocusLeftPanel = the LOCK-AND-SUPERCRUISE
sequence). But in the 120s per-body window: ZERO AutoScan, ZERO SupercruiseDestinationDrop,
ZERO SC transitions. Every body hit BodyTourBodyTimeout. Operator watching the screen:
"it's just jumping" — the ship never flew out to a body.

LIKELY ROOT CAUSE: SC-assist sets the APPROACH but the ship only MOVES with throttle in the
blue zone. The per-body engage (engage_supercruise_assist_row) does the nav-panel
LOCK-AND-SUPERCRUISE but NEVER sets the throttle, so at throttle ~0 (after the star-orbit +
wait) the body locks but the ship sits still -> no proximity AutoScan -> timeout. The
arrival STAR-orbit "works" only because the star is AT the arrival point (no fly-out needed).

NEXT (needs Operator in-game verification — do NOT guess the mechanic):
1. In-game: lock a planet, LOCK-AND-SUPERCRUISE — does the ship fly to it only when the
   throttle is in the blue zone? What throttle makes SC-assist actually fly out?
2. Fix: the per-body engage sets the throttle to the SC-assist blue zone
   (sc_assist_throttle_action / SetSpeed75) so the ship flies to the body; re-tune
   orbit_timeout_s for real fly-out distances (120s is likely far too short).

DISABLED in config (body_tour_enabled=false) pending the fix. Subsystem code stays (8cf3e26
+ 7558835); only the engage throttle + timeout need the live fix. Early-lock + clean jump
loop unaffected and running.

---

## FIX DIRECTION (Operator 2026-06-08) — CV/OCR unexplored-body targeting

Two gaps to close (body_tour_enabled=false until both land):

**1. TARGETING — read the panel, do not walk by row index.** Replace the blind
positional walk (engage_supercruise_assist_row pins row 0 + UI_Down x k) with a
CV/OCR read of the nav panel:
  - OCR the nav-panel body rows (tesseract is already configured: config.cv.ocr_engine).
  - Cross-reference the journal scanned-set the bot already keeps (_autoscan_bodies).
  - "Unexplored" = a panel body name NOT yet in the scanned-set.
  - Target THAT body's row (naturally skips the star at row 0, stations, and already-
    scanned bodies — no row-index assumptions).

**2. FLY-OUT — set the throttle to the SC-assist blue zone.** The per-body engage
must SetSpeed75 (sc_assist_throttle_action) so the ship actually flies to the locked
body; re-tune orbit_timeout_s for real fly-out distances (120s was far too short).

Placement is already CORRECT (Operator's sequence: arrival -> refuel if needed -> SC-assist
clear the star -> wait -> target planet 1). The bug is the targeting + the fly-out.

OPEN (in-game, for the spec — do NOT guess):
  - After honk, does the nav panel list ALL bodies by readable name (the OCR target)?
    Does it scroll / how many fit on screen?
  - How does the panel mark explored vs unexplored (or do we rely solely on the journal
    scanned-set + name cross-ref)?
  - Nav-panel CV region calibration (a new `calibrate-navpanel` like calibrate-compass).

This is a proper spec->plan->build (council), building on the existing vision/OCR stack.

---

## IDENTIFY PORTION BUILT (2026-06-09) — commits 8ed3ba8 + 4d73423

Part 1 of the fix (identity targeting) is implemented, wired, and real-frame-validated.
Body_tour stays DISABLED (part 2, the throttle fly-out, is still open).

- `vision/navpanel_reader.py` — three layers: PARSE (lines -> in-system NavBody[]),
  SELECT (`next_unexplored` vs the journal scanned-set), READ (lazy cv2+pytesseract OCR,
  CALIBRATION-PENDING). Canonical body name == journal `Scan.BodyName`, so the
  scanned-set cross-ref is plain equality.
- `step_body_tour` runs IDENTITY mode when `nav_panel_reader` + `nav_panel_grabber` are
  wired (else the legacy blind walk); a `tried` set stops a timed-out body from being
  re-picked; read failure fails OPEN (tour ends, jump resumes). Wired through
  StepContext / FlowRunner / config / capture.build_navpanel_vision / cli, all gated on
  `[exploration].nav_panel_ocr_enabled` (OFF).
- Real frames pinned (`tests/fixtures/navpanel/tyriedgoea_kn-o_b47-1_*.png`). They
  surfaced + hardened the shared-region-prefix case (nearby systems "Tyriedgoea LN-O
  B47-1" vs current "Tyriedgoea KN-O B47-1") and the longer-mass-code sibling ("B47-10"
  vs "B47-1") — `_system_prefix_match` now requires a space boundary. `DEFAULT_NAV_REGION`
  corrected to the measured body-name column (505,435,410,330).
- Tests: 15 reader + 3 body_tour identity, all green; 13 pre-existing reds unchanged.

REMAINING for live use:
1. **Part 2 — throttle fly-out** (the original task #45): per-body engage must SetSpeed75
   (SC-assist blue zone) so the ship actually flies to the locked body; re-tune
   `body_tour_orbit_timeout_s` for real fly-out distances.
2. **OCR live pass** — `pip install -e .[cv]` + a tesseract binary, then validate
   `read_nav_panel_lines` against the pinned region fixture and tune psm/preprocessing.
   Best done on a PLANET-RICH system (both pinned samples are 2-star; a planet-rich
   frame would exercise "A 1"/"A 2"/moon "A 2 a" rows + the in-panel "unexplored" marker).

---

## NAV-PANEL CALIBRATION (2026-06-08, live screenshot + Operator confirmed)

Parked the ship orbiting, opened the NAVIGATION panel, captured navpanel_calib_full.png.
Confirmed (memory ed-navpanel-navigation-tab-format):
- NAVIGATION tab = distance-sorted, INTERLEAVED: in-system bodies (Ls, current-system name +
  designator " A"/"A 1"/...) + nearby SYSTEMS (Ly, other designations). NOT a clean body list.
- CV/OCR keeps the Ls / current-system rows, drops the Ly / other-system rows.
- Planets show in the SAME list, in Ls, as "A 1", "A 2"...
- "unexplored" marker on a body until you are close (readable by CV; journal scanned-set backstop).
- WORTH-TOURING = honk BodyCount > a few -> body_tour_min_bodies gate set LOW (~5-8, not 40).
- Region @1920x1080 (approx): body list x~310-545, y~145-270 (refine with the OCR crop).

CV/OCR reader (task #45): arrival -> if BodyCount >= ~few -> open NAVIGATION -> OCR rows ->
keep Ls/current-system bodies -> next UNEXPLORED planet (marker + journal cross-ref) -> target
it -> (throttle blue-zone fix) fly out -> AutoScan gate -> next.

REMAINING sample: a PLANET-RICH system to see planet rows + the "unexplored" marker live (the
2-star sample could not show them).
