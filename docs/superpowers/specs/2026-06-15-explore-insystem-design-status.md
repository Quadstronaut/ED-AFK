# In-system exploration — design council status (route_back)

**Date:** 2026-06-15
**Council:** run `wf_87ed00ff-68a` (arch, design-only spec + plan)
**Decision:** **route_back → Stage 1** (no committable spec; 0 of 4 candidates reached
unanimous pass). This is the gate working — the review caught real liveness/recovery defects
in the candidate specs before they hit disk. **The Stage-0 baseline design is sound** (passed
spec-conformance unanimously); the route-back is about candidate-local bugs, not the design.

## Sound design core (keep)
- **Identity-based traversal** (NOT index-based): the nav list re-sorts by proximity mid-walk,
  so each iteration re-reads + selects the next-unvisited body by canonical NAME against a
  per-session visited-set; a row index is used only transiently to drive the cursor.
- **Filter set F1–F6** (Stars / Planets+moons / Landfall / Stations / Systems / POI),
  data-driven + future-extensible (megaships). Each filter uses a **read-before-write +
  read-after-write CONFIRM** contract — never assumes the toggle gesture's polarity.
- **Per-entry visit gate:** orbital circle SOLID=clear→assist / BROKEN=obscured→defer-or-skip /
  MISS=fail-closed-never-assist; plus 'unexplored' OCR with the **journal scanned-set (AutoScan)
  as the authoritative backstop** — the one place a CV miss falls back to a stronger non-CV
  signal instead of failing closed.
- **Zero wall-clock dwell** — gates are AutoScan / SupercruiseExit events only; the legacy blind
  30s SC-Assist dead-pause is explicitly replaced.
- Every nav-panel state S0–S6 has a forward edge AND a fail-closed edge to EXIT_TO_TRAVERSAL;
  exit conditions are identity/event-defined, never a row count.

## Stage-1 regeneration MERGE-MAP (for the next council run)
- **Seed:** gen-opus-2 (strongest concurrency + CV6-identity-confirm posture, clean security +
  spec-conformance).
- **MUST inherit:** (1) gen-opus-1's *BROKEN-marks-tried + anti-spin terminator* (so a
  persistently-broken body enters the E1 exclusion set and exhaustion can fire); (2)
  gen-sonnet-4's *re-engage-fail → log → S6* branch + named failure log-events (so a failed SC
  re-engage doesn't strand the ship in real space).
- **MUST drop:** gen-sonnet-4's CV6-miss→stale-row_index fallback (fires SC-assist at the wrong
  body after re-sort) and its E5/E6 count-based exits (contradict the identity-based exit rule).

## BLOCKED on 9 operator in-game questions (the real unblock)
Implementation cannot proceed — and a regenerated spec stays full of the same unknowns — until
these are answered in-game:
- **Q1** filter-screen layout + per-filter toggle gesture
- **Q2** filter persistence across jumps + the clear-button semantics
- **Q3** how proximity re-sort interacts with an in-progress transit
- **Q4** scroll-window enumeration + down-past-bottom behavior
- **Q5** the top-to-bottom span shortcut key
- **Q6** SC-Assist behavior per body type for the exploration goal
- **Q7** the 'already visited' marker — does a station/POI emit a journal signal the visited-set
  can key on?
- **Q8** the back → cursor → 'D'-to-list cursor landing row
- **Q9** the orbital 'circle' on-screen semantics + which HUD element renders it

**Recommended next-session order:** answer Q1–Q9 in-game FIRST, THEN re-run the Stage-1
regeneration against real answers (regenerating before the answers just reproduces the unknowns).

## Carry-forward dissent
- The candidate self-check shell scripts (`spec_completeness_check.sh` etc.) are CIRCULAR (grep
  text markers, not semantics) — Stage-1 should replace them with semantic checks (assert no
  CV-miss row contains a `row_index` fallback; assert no exit row uses `>=` against a count).
- Confident-but-wrong CV reads from an uncalibrated reader are NOT caught by the
  fail-closed-on-MISS guards — mitigation is P0 real-frame calibration; make calibration-QUALITY
  an explicit acceptance gate, not a yes/no flag.
- Mandate the PD7 'drop-fires-before-SupercruiseExit' event ordering explicitly rather than
  inheriting it implicitly from body_tour.

Worktree artifacts (ephemeral): candidate specs under
`.claude/worktrees/wf_87ed00ff-68a-*/docs/superpowers/specs/`.

---

## OPERATOR ANSWERS (2026-06-15, in-game) — Q1–Q9 RESOLVED

- **Q1 — filter screen:** a vertical list of **checkbox toggles** (select row + `UI_Select`
  toggles in place; filled box = on, empty = off). Categories top-to-bottom: Stars, Asteroid
  Clusters, Planets and Moons, Landfall Planets and Moons, Settlements, Stations, Carriers,
  Points of Interest, Signal Sources, Systems, then BACK. **Desired set ON:** Stars, Planets and
  Moons, Landfall Planets and Moons, Stations, Points of Interest, Systems (OFF: Asteroid
  Clusters, Settlements, Carriers, Signal Sources). Read-before-write each checkbox; toggle only
  the rows that differ. Frame: `navpanel_filters_screen.png`.
- **Q2 — persistence:** filters are **permanent** — set once, the game manages the state across
  jumps/sessions. The first-time gate only needs to establish the known state once.
- **Q3 — re-sort during transit:** the **cursor stays locked to the selected body by identity**
  even as that body moves around the proximity-sorted list (confirmed: same `<UNEXPLORED>` entry
  tracked from 1,651 Ls → 632 Ls without any input). Frames: `navpanel_resort_premove.png` /
  `navpanel_resort_postmove.png`. Identity-based traversal confirmed; no mid-transit cursor drift.
- **Q4 — scroll/bounds:** cursor moves up/down, **bounds at the edges; a repeated press against a
  boundary WRAPS to the other end** (top↔bottom, either direction). The window scrolls with the
  cursor. (Already in `ed-navpanel-cursor-mechanics`.)
- **Q5 — span shortcut:** there is **no separate key** — the boundary **wrap IS the shortcut**
  (press down at the bottom → wraps to top, and vice-versa).
- **Q6 — SC-Assist per body type (already in `sc-assist-orbit-vs-drop-mechanics`):** ALL bodies
  (planet, moon, star, black hole, wolf-rayet) = **ORBIT**; stations, nav beacons, POI, carriers
  = **DROP**.
- **Q7 — visited marker:** there is **NO HUD/nav-panel visited marker** — only the **galmap,
  logs, and journals** record where we've been. The visited-set is **journal-derived**:
  `Scan(BodyName)` for bodies; `SupercruiseDestinationDrop`/`Docked`/`ApproachSettlement` for
  drop-targets. The nav-panel "UNEXPLORED" label corroborates for bodies only.
- **Q8 — back → cursor:** BACK from the filters screen lands the cursor on the **SET FILTERS
  button** (the button 1 left of the nav list); `D` (right) then enters the nav list.
- **Q9 — the "circle" (CORRECTION):** it is **NOT an orbital indicator** — it's the circular
  artifact around a **JUMP target**: **dashed line = jump blocked/obstructed, solid line =
  unobstructed**. This is the jump-obstruction ring (STARTUP / TRAVERSAL jump points), NOT a
  per-body exploration gate.

## Design corrections from the answers (fold into the Stage-1 regen)
- **DROP the per-body "orbital circle" CV (old CV7).** There is no orbital circle. Reaching a
  body is just SC-Assist (which orbits it — Q6); the visit completes on the AutoScan
  `Scan(BodyName)` event. Obstruction is handled by SC-Assist's own pathing, not a CV gate. The
  jump-ring (Q9, solid/dashed) belongs to the traversal JUMP-OUT, not the body visit. → one fewer
  CV dependency for exploration.
- **Filters = one-time permanent set** (Q2) via per-checkbox read-confirm-toggle (Q1), not re-set
  every system.
- **Traversal cursor is identity-locked through re-sort** (Q3) — the re-read selects the *next*
  body; within a single body's transit the cursor stays put.
- **List traversal: wrap-around is the only navigation primitive** (Q4/Q5) — single-row up/down
  with edge-wrap; no span shortcut key.
- **Visited-set is journal-only** (Q7) — Scan for bodies, drop-events for stations/POI/carriers.

Real frames pinned at repo root: `navpanel_filters_screen.png`, `navpanel_resort_premove.png`,
`navpanel_resort_postmove.png`.
