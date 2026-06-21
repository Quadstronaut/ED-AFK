# Flight-Flow Redesign — MASTER SPEC (operator-authored, 2026-06-17)

Single source of truth for the operator's full flight-flow redesign. The sections below are
distributed to parallel **council-v2** workflows. This doc is verbatim operator intent + the already-
settled game-truths; councils design AGAINST it. **It decides the WHAT; councils design the HOW.**

## STANDING RULES (binding on every council)
- **NO GUESSING.** Any unknown game behaviour, bind, journal field, or screen layout that isn't settled
  here or in the cited memories → **flag `BLOCKED-ON-KYLE: <question>`** in the design + ledger. Do NOT
  invent it. (Ref [[no-assumptions-ever]], [[ask-kyle-to-test-game-mechanics]].)
- **Read the documentation** — repo code/docs AND community ED references (journal/Status.json schema,
  nav-panel behaviour) before asserting a mechanic.
- **DESIGN-ONLY / no-build is LIFTED (operator 2026-06-18).** Building is now **authorized** for ratified
  scenes — the C5 Traversal (5512a17) and C2 orchestrator (42f33d0) scenes are BUILT + landed (unit-verified), and the pip steps are restored (daa4bf9). The prior STANDING RULE
  ("Do not build, edit flight code, or commit; the live flight path stays untouched until Operator signs each
  design off") is **superseded**. Only the no-build clause is lifted; **NO GUESSING** (above) and
  fail-closed (below) remain fully in force — a ratified design still gates every unknown on Operator and
  every new action still fails closed.
- Honour [[no-arbitrary-timed-waits]] (gates are journal events / Status flags, not wall-clock) EXCEPT
  where the operator explicitly wrote a `wait Ns` below — those are operator-chosen and kept as written,
  but flag any that SHOULD be an event gate.
- Ship-safety: every new action fails closed; nothing drives blind on an unread frame.

## SETTLED GAME-TRUTHS (do not re-derive — cite these)
- **Nav-panel READ layer is LIVE**: `ed_vision/ocr_winrt.py` (WinRT OCR, landed `c31af28`), validated on
  real frames. Reads every row's name + distance. See [[ed-navpanel-ocr-first-parser]].
- **Star identity = system name** ([[ed-navpanel-target-cv-rules]] §1). The arrival/primary star's row
  name is always the bare system name (LP 389-95 / SHINRARTA DEZHRA / LHS 2509).
- **Star always present** — AUTHORIZED assumption: the Stars nav-filter is ON (§2). No filtered-out case.
- **Highlight-to-read distance** (§3): selecting a row renders it black-on-orange → better distance OCR
  + shows the operator what the bot reads. Find the star by NAME on un-highlighted rows; highlight to
  read distance. Name+distance are Y-offset by panel tilt → pair by ordinal, not Y.
- **Target success gate = `Status.Destination` == intended** (§4 / OQ3).
- **Unexplored bodies render as literal `UNEXPLORED`** (no name until scanned); the in-system list ENDS
  where the first **system `✦` icon** (nearby-systems section) begins. Pinned frames:
  `tests/fixtures/navpanel/{shinrarta_populated_1080.png, lhs2509_unexplored_1080.png}`.
- **Details-page button bar** (operator frames, image refs in this session): a row's detail page has a
  horizontal button bar navigated with the row submenu cursor; the LABEL above the highlighted button
  states its action — `LOCK DESTINATION` / `UNLOCK DESTINATION` (a locked row shows UNLOCK), and the
  SC-assist button whose label is **BODY-TYPE-DEPENDENT** (live-confirmed 2026-06-21): OFF reads
  `SUPERCRUISE ASSIST AND ORBIT` (orbitable body) or plain `SUPERCRUISE ASSIST` (station) — **NOT** the
  assumed `ACTIVATE SUPERCRUISE ASSIST`; ON reads `DEACTIVATE SUPERCRUISE ASSIST`.
  ✅ **CAPTURED 2026-06-21** — `tests/fixtures/navpanel/navpanel_detail_{lock,unlock,sc_activate,sc_deactivate,sc_assist_station}_1080.png`.
  Bar is HORIZONTAL (UI_Right walk holds).

## NEW CV ACTIONS (corrected semantics — operator 2026-06-17)
- **`nav_target_star`** — ensure the MAIN STAR is the locked destination. **Single select, NOT ×2.**
  Check first: is the star already the locked destination? (On the star row's detail page the lock
  button reads **`UNLOCK DESTINATION`** when already locked — image #7.) If already locked → no-op done.
  If not → press the lock button **once**. (Operator: "the first time will select the star… ×2 is not
  required and confuses the watcher.") Verify via `Status.Destination` == system.
- **`nav_supercruise_star`** — open the star row's detail page and press the **SUPERCRUISE ASSIST button
  once** (NOT the lock button) — image #6. Engages SC-assist toward the star. Replaces the old blind
  `sc_assist_orbit` macro.
- **`nav_supercruise_target`** — same as nav_supercruise_star but for the STATION row (docking).
- **`nav_supercruise_unexplored`** — LOOP: SC-assist the next `UNEXPLORED` item down the list (detail
  page → supercruise button). Knows it has reached the list bottom by the **`✦` system icon appearing in
  column 0 at the row selector**. Terminates when the row selector lands on a system-icon row. (Operator:
  "track by row selector — long sessions, but covers all unexplored.")
- **`honk_dscanner`** — fire the discovery scanner (honk) IMMEDIATELY on arrival, **non-blocking** (other
  actions must not wait on it). Today honk is `parallel_tracks=["honk"]` — confirm/extend.
- Shared substrate: **details-page button-bar CV navigation** (read the label, move the cursor to the
  desired button, press once). nav_target_star / nav_supercruise_* all sit on this.

## SCENE FLOWS (operator-authored, verbatim intent)

### Arrival  (flag set by witchspace entries in the journal)
1. `set_throttle 0`
2. `honk_dscanner` — begin immediately, do NOT make other actions wait on it
3. `scoop_refuel` — **new trigger value 50%** (refuel_below 0.70 → 0.50)
4. `nav_supercruise_star`  (image #6)
5. branch: **if** current system == destination → **Docking** ·
   **elif** exploration == active → **Exploration** · **else** → **Traversal**

### Smack Recovery  — **LAW, LOCKED/RESOLVED (operator 2026-06-18)**
The 8-step routine is settled, in order. The earlier "DEVIATION-BLOCKED / target-nothing /
verbal-vs-written contradiction" framing was a **Claude error** and is struck — there is no conflict.
1. `set_throttle 100`
2. `nav_target_star`
3. `pitch_compass`   *(keep the smack-glare guards `behind_confirm_reads` / `behind_fill_max`)*
4. `target_ahead`
5. `wait_cooldown_clear`
6. `engage_supercruise` (Key_J) — **SETTLED.** Re-enter SC from normal space after the smack; this
   spawns the BLUE/CYAN escape vector the ship ALIGN-AND-HOLDs to `SupercruiseEntry`. This is the SC-entry
   mechanic, **NOT** `engage_jump_clearance` (Key_K, the hyperspace jump-clearance loop). No longer a
   `BLOCKED-ON-KYLE`.
7. `nav_supercruise_star`
8. → **Traversal**

### Traversal
1. `wait 5s`
2. `target_next_route`
3. `set_throttle 100`
4. `wait 3s`
5. `orient_compass`
6. `orient_widget_ring`
7. `engage_jump_clearance`

### Exploration
1. `nav_supercruise_unexplored` (loop until the `✦` system icon is at the row selector — see new actions)
2. → **Traversal**

### Docking
1. `wait 1s`
2. Check `Status.json`
3. **if** destination == system → `nav_supercruise_star`; **bot goes IDLE (no more execution).**
4. **elif** destination == station →
   1. `nav_supercruise_target` (station row)
   2. wait for the journal entry that drops out of supercruise (`SupercruiseExit`)
   3. get station name from `Status.json`? journal? — **BLOCKED-ON-KYLE / READ-DOCS:** determine the
      authoritative source (do not guess the field).
   4. `boost`
   5. `set_throttle 50`
   6. nav-panel target the station
   7. nav-panel OCR distance to station, loop until **< 7.5 km**
   8. send `E` bind → `wait 0.5s` → send `E` bind → `wait 0.5s` → send `D` bind → send `spacebar` bind →
      `set_throttle 0`  — **(operator-specified exact sequence; implement as written. Confirm each bind
      exists via binds_validate; flag any missing as BLOCKED-ON-KYLE.)**
   9. done — autodock takes over

## CROSS-CUTTING / OPEN QUESTIONS (flag, don't guess)
- **Section-transition mechanism** ("goto ## Section"): does the dispatcher support procedure→procedure
  transitions, or is this a NEW control-flow concept? (Read `ed_core/flow/dispatcher.py`,
  `ed_autojump/flow/boot_routes.py`, `ed_core/boot/scenes.py`.) Design how Arrival branches and scenes chain.
- **`exploration == active`** flag source (config `body_tour_enabled`? a new exploration-mode flag?).
- **`current system == destination`** / **`destination == system|station`** — read from `Status.json`
  `Destination` (Name/System/Body). ✅ **RESOLVED 2026-06-21 (`D1-DESTINATION-DISCRIMINATOR-FINDING.md`):**
  station iff `Body != 0 AND Name != currentSystemName` (a locked STAR is also `Body!=0`, Name==system;
  `Body==0` = a whole-system / next-hop target). Read at route-complete. The bare `Body!=0` binary is REFUTED.
- Per-ship CV regions (#19) apply to all new CV crops — region must come from the per-ship resolver, not
  a constant. Bot is single-ship Mandalay.

## FILE POINTERS
- Procedures: `projects/ed-autojump/procedures/{arrival,startup,sc_resume,smack_recovery,route_complete_park}.toml`
- Steps: `projects/ed-autojump/src/ed_autojump/flow/steps.py`; shared `ed_core/flow/steps_shared.py`;
  explore `ed_explore/src/ed_explore/steps_explore.py`
- Nav-panel executor (key macros): `ed_core/src/ed_core/executor/navpanel.py`
- READ layer: `ed_vision/src/ed_vision/{ocr_winrt.py, navpanel_reader.py}`
- Dispatch/registry: `ed_core/flow/{dispatcher.py, registry.py, step_registry.py}`; `boot_routes.py`
- Action registry surface: `register_step(...)` calls (run `Grep "register_step\("`)
- Settled rules: [[ed-navpanel-target-cv-rules]], [[ed-cv-regions-are-per-ship]], [[ed-navpanel-ocr-first-parser]]

## COUNCIL DECOMPOSITION (this distribution)
- **C1 (arch)** — NEW CV ACTION FAMILY: details-page button-bar CV nav + nav_target_star +
  nav_supercruise_star + nav_supercruise_target + nav_supercruise_unexplored. Foundational; sections cite it.
- **C2 (arch)** — CONTROL-FLOW / section-transition mechanism (goto, arrival flag, branch conditions,
  scene chaining) + honk_dscanner async + scoop 50%.
- **C3 (feature)** — ARRIVAL scene.
- **C4 (feature)** — SMACK RECOVERY scene.
- **C5 (feature)** — TRAVERSAL scene.
- **C6 (feature)** — EXPLORATION scene.
- **C7 (arch)** — DOCKING scene.
Section councils (C3–C7) design against the C1/C2 contracts and flag any contract assumption.
