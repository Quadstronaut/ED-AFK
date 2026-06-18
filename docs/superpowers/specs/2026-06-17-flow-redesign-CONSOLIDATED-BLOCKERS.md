# Flow Redesign — CONSOLIDATED BLOCKER LIST (7 councils, 2026-06-17)

All 7 section councils ran (design-only, no-guessing). **C3 Arrival ratified (commit); the other 6
route_back'd** — almost entirely on legitimate frame / game-truth / intent gaps the councils refused to
guess (exactly the "ask Operator, no guessing" discipline). Per-council designs are committed as drafts
(`2026-06-17-flow-redesign-C*-DESIGN.md`); ledger in `.claude/council-ledger.jsonl`. They are
**build-blocked** on the items below. C5 Traversal was re-fired with fixes (running).

---

## A · FRAMES to capture in-game (the biggest unblock)

1. **Detail-page button-bar frames — THE keystone.** A nav-panel row's DETAIL page in BOTH states:
   (a) the lock button reading `LOCK DESTINATION` and `UNLOCK DESTINATION`, (b) the supercruise button
   reading `ACTIVATE` and `DEACTIVATE SUPERCRUISE ASSIST` — showing the full button bar + button order.
   Unblocks **C1** (the entire `nav_target_star` / `nav_supercruise_*` family), which **C3/C4/C6/C7 all
   build on.** (Same ask as G1 BK-6.) Nothing CV in the redesign builds without this.
2. **Contacts-tab station-distance frame** — the station distance at approach range, to see whether it
   reads in `km`/`Mm` and on which tab. The READ layer only parses `Ls`/`Ly` today, so the `< 7.5 km`
   docking gate (C7) needs a new km-parser calibrated against a real frame. None exists.
3. **Smack no-vector deliberate-drop frame** (G2 detector negative case — the one we already discussed).
   Later, a **purple planet-smack** frame (refines star-vs-planet; also C4 planet-smack).
   - *(Column-0 icon classifier, C6: I can likely build + validate this on the EXISTING
     `lhs2509_unexplored` + `shinrarta_populated` frames — they show all three glyphs. No new frame
     needed unless validation fails. I'll attempt it.)*

## B · Game-truth to test in-game (quick live checks)

4. **Status.Destination station schema** (C2 D1): undock → plot a route to a **station** → read
   `Status.json`; confirm `Destination.Body != 0` (+ Name) distinguishes a station from a system/star
   (Body = 0). The whole system-vs-station branch (Arrival → Docking vs Traversal) hinges on this.
5. **Does SC-assist on an UNEXPLORED row DROP the ship, or ORBIT it?** (C6 B3/B4): if it drops you into
   normal space, the explore loop needs a re-engage branch or it strands. (Memory says SC-assist orbits
   bodies in SC and only drops at stations/POI — but unexplored bodies are the untested case.)

## ✅ C · Intent / design decisions — ALL LOCKED (operator 2026-06-18)

Recorded in [[resume-state-2026-06-18-flow-redesign]]. Summary:
- **#9 exploration flag** → reuse `body_tour_enabled` (VERIFIED wired; C2 predicate must read
  `ctx.body_tour_enabled`, not the phantom `runner._exploration_mode` at boot_routes.py:84).
- **#6 smack step-6** → `engage_supercruise` (Key_J). **LOCKED/SETTLED** (the prior "moot pending the C4
  deviation" caveat is struck — the deviation was a Claude error, see the resolved block below).
- **#8 planet-smack** → same as star-smack, parameterized, EXCEPT compass usable the WHOLE time (no
  flip-about: planet has no superbright source / no glare).
- **#11 docking sequence** → OCR-gated, NOT blind. `1`→`E`→`E`→`D`→`space`→throttle 0; OCR-confirm the
  REQUEST DOCKING button before pressing. Full open-from-scratch.
- **#12 NFZ gate** → NFZ-entry ≠ docking-ready. Proximity (<7.5 km OCR) is the docking trigger; the NFZ
  journal event is a SEPARATE fire-safety concern. Keep both, distinct.
- **#13 station-name** → `Status.Destination.Name`, logging-only.
- **#7 nav_supercruise_star** → assumes ALREADY in SC; it is only the final SC-assist-on-star press.

### ✅ C4 SMACK ROUTINE IS LAW — LOCKED/RESOLVED (operator 2026-06-18)
The earlier "DEVIATION-BLOCKED" framing (an alleged conflict between a verbal routine and the authored
master spec) was a **CLAUDE ERROR** and is struck — there was never a real contradiction, the routine is
not re-fire-blocked, and step 6 is settled. The authored 8-step law is confirmed, in order:
`set_throttle 100` → `nav_target_star` → `pitch_compass` (keep the smack-glare guards) → `target_ahead` →
`wait_cooldown_clear` → `engage_supercruise` (Key_J — re-enter SC; spawns the escape vector; align-and-hold
to `SupercruiseEntry`; **NOT** `engage_jump_clearance`/Key_K) → `nav_supercruise_star` → **Traversal**.
The compass-glare guards and the escape-vector ALIGN-AND-HOLD mechanic are retained ship-safety, not
deleted by this resolution. Full breakdown in the resume-state memory.

## C · Intent / design decisions (original — superseded by the LOCKED block above)

6. **Smack step-6 — RESOLVED/LOCKED (operator 2026-06-18): `engage_supercruise` (Key_J).** (C4 #1, C2 D3.)
   The council's strong read was correct: step 6 is `engage_supercruise` (re-enter SC → the escape-vector
   align-and-hold path); the `engage_jump_clearance` (Key_K) token was copied from another scene and was
   never the chosen step-6 action. No longer open — kept here only as the superseded historical question.
7. **Does `nav_supercruise_star` handle the SC entry itself, or does the escape-vector ALIGN-AND-HOLD
   ladder** (`orient_compass` + `hold_alignment` until `SupercruiseEntry`) **survive between smack steps
   6 and 7?** (C4 #6). If `nav_supercruise_star` subsumes SC entry, those steps drop.
8. **Planet-smack: same procedure as star-smack, or different?** (C4 #7). The authored flow names only
   star actions; `smack_kind` can be `planet`.
9. **`exploration == active` flag source** (C2 D2, C3 BK-2): `_exploration_mode` is **unwired** (always
   False → the Exploration branch is unreachable today). Use the existing `body_tour_enabled` config, or
   a new exploration-mode flag?
10. **Section transitions** — ✅ **RESOLVED (operator 2026-06-18): Python orchestrator.** In
    `ed_autojump`: a `_SECTION_TO_PROC` map + `transition_to(runner, section)` + `run_arrival_then_branch`,
    registered via the existing classifier/event-route surfaces (no core→domain import). Rejected the TOML
    `goto` (net-new core primitive that, after the G12 DAG + the conditional branch, collapses into the
    orchestrator-through-the-interpreter for no benefit). **MANDATORY: abort-recheck** so a smack landing
    between arrival's return and the forward branch can't drive into the exclusion zone — read
    `self._preempt` / `self._smacked` / `should_abort()` at TWO points: (a) in `run_arrival_then_branch`
    BETWEEN `_run("arrival")` and the discriminator read; (b) at the top of `transition_to` before
    `runner._run(section)`. If set → don't branch, yield to run_live → `_route_sc_exit`. Optional stronger
    form: `transition_to` returns the section to run_live (event-route priority handles the smack for free).
11. **Docking `E/E/D/space` precondition** (C7 3a): the literal keys = `CycleNextPanel ×2, UI_Right,
    UI_Select` = the `request_docking` *tail* with **no panel-open and no pin**. Does it assume the panel
    is already open on the Contacts station row, or should it be the full `request_docking` macro?
12. **Docking NFZ vs proximity — RESOLVED/LOCKED (operator 2026-06-18): SEPARATE, both kept.** (C7.)
    The `< 7.5 km` OCR proximity loop and the live-verified (2026-06-07) `ReceiveText
    $STATION_NoFireZone_entered` journal gate are SEPARATE, distinct, both kept — NOT a replace
    (the earlier "replaces your live-verified NFZ gate" framing was a CONFLATION and is struck; ~~replaces
    NFZ gate~~). NFZ-entry is a fire-safety zone (LARGER than the docking zone); the OCR proximity
    < 7.5 km is the docking-readiness trigger. (See the LOCKED #12 in the decisions block above — already
    correct.)
13. **Station-name source** (C7 3c): `SupercruiseExit` carries no `StationName`. Use
    `Status.Destination.Name` (lowest latency)? And what is the name used for — the flow never consumes
    it after reading (maybe logging only)?

## D · No input needed (I handle / cross-council)

- **C5 Traversal** — re-fired with the round-1 fixes → **RATIFIED (round 2, commit, gen-sonnet-2)**. Frame-independent and buildable now (the first scene that can ship). Leftover flags B1–B3 (C2 entry contract; whether the 5s/3s waits should be event-gated) are minor and don't block.
- **`boost`** is a new step (C7 3b): tap `UseBoostJuice`/Key_B, best-effort. I'll spec it (say if you want a cooldown/heat guard).
- **honk** stays a parallel track in **Arrival only** (not Traversal/Smack) — pinned.
- **The danger-gate "hole"** in `target_next_route` is the unwired/test path only — **not a live bug** (verified `steps.py:94`).

---

## Build order once unblocked
Frame #1 (detail-page) → C1 ratifies → C3 (ready) + C4/C6/C7 designs ratify → then the Python
orchestrator (C2 D4) wires the sections. Traversal (C5, frame-independent) can ratify + build first.
