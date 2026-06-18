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

## C · Intent / design decisions (your call)

6. **Smack step-6: `engage_supercruise` (Key_J) or `engage_jump_clearance` (Key_K)?** (C4 #1, C2 D3).
   Council's strong read: you meant **`engage_supercruise`** (re-enter SC → the escape-vector
   align-and-hold path), and `engage_jump_clearance` was a token copied from another scene. Confirm.
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
12. **Docking NFZ gate regression** (C7): the authored `< 7.5 km` OCR loop **replaces your
    live-verified (2026-06-07) `ReceiveText $STATION_NoFireZone_entered` journal gate** with a
    calibration-pending OCR loop. Keep the proven journal gate (instead of / alongside the OCR)?
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
