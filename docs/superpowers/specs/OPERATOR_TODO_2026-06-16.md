# OPERATOR TODO — game-truth gaps Operator fills (2026-06-16)

All 4 councils LANDED (C1-C4) + the stale-editable fix. NO answer was guessed — every gap is STUBBED
fail-closed with a TODO. Operator answered 1/2/3 on 2026-06-16 (applied below); only the FRAMES remain.

## Autoexploration

### RESOLVED by Operator (2026-06-16) — applied as game-truth, not guessed
1. **Body kind / selection (was OPEN-3).** The per-row marker is EXPLORED vs UNEXPLORED (unexplored =
   a small box inside a hollow box), NOT a kind icon. Stars self-explore via the honk, so the tour
   targets UNEXPLORED PLANETS/MOONS — all the ORBIT case. So `classify_kind -> ORBIT` (current default)
   is CORRECT and the DROP branch is intentionally unused for autoexplore. Selection runs off the journal
   scan-set (next_unexplored), which already skips honked stars. STUB-1 resolved (doc only, no behavior change).
2. **DROP-target visited signal.** No special signal needed — SC-assist drops automatically at a targeted
   station unless a body blocks the path (edge case); the journal shows `SupercruiseExit body_type = Station`
   (vs Star). Claude scrapes the exact field from REAL logs (no guess). Secondary, since the tour is
   planets/moons (ORBIT). STUB-2 direction set.
3. **SET FILTERS GuiFocus.** There is NO special filter screen — it is part of the left/NAV panel,
   GuiFocus = 2 (already known). FOLLOW-ON (code, not operator): the council's automated SET-FILTERS pass
   (step_explore S0) is over-built — Operator sets nav filters manually; the bot just reads the panel. S0 must
   be SIMPLIFIED so it does not perpetually self-block the tour (today S0 fails closed -> tour never runs).

### REMAINING autoexplore blocker (operator — frames)
4. **Nav-panel calibration frame.** One screenshot of the NAVIGATION tab with a few bodies listed (ideally
   an UNEXPLORED system showing the box-in-hollow-box markers) — to calibrate the reader's region + row/
   column crops. THE last blocker for the tour to actually run. Operator grabs one next time in-game (not
   launching now). Until then the reader is calibration-pending and step_explore fail-closes (no keypresses).

## Smack (your correction)
5. **Escape-vector frames** for `detect_escape_vector`: (a) a BLUE star-smack escape vector, (b) a
   PURPLE planet-smack escape vector, (c) a deliberate drop showing NO vector. And confirm the model:
   escape vector PRESENT = smacked; color = body (blue=star, purple=planet); no vector = deliberate
   drop. Anything else that distinguishes a smack from a deliberate drop?

## Smack — additional confirmations (LOW priority; safe defaults already coded, confirm when free)
6. **Escape-vector PERSISTENCE (OQ1).** After a smack-drop completes, does the escape vector STAY on the
   HUD/compass or clear? For how long / what clears it? Matters for restart-while-smacked: if it clears,
   a cold restart can't CV-confirm a smack → it safely abstains (no auto-recovery). Default today = abstain
   on restart (safe, but won't auto-recover a restart-while-smacked).
7. **Planet-smack recovery mechanic (OQ6).** Does the existing STAR `smack_recovery` dance (nav-panel
   row-0 lock → pitch-180 body-astern → FsdCooldown gate → escape-vector charge → 13s clear) work
   UNCHANGED for a PLANET, or is any step star-specific? Default = reuse the star procedure for planets,
   flagged as a risk until you confirm.
8. **Planet preempt OK? (OQ5).** Widening the mid-scene smack-preempt to planets means a *deliberate*
   planet drop will ABORT a live arrival/dock scene (re-dispatch then continues benign — no recovery).
   Acceptable, or does a real planet-approach flow get disrupted? Default = wide preempt + narrow CV-gated
   recovery (safe, but may briefly abort a benign planet scene).

## Jump flow / clearance loop (C1) — confirmations (safe defaults coded)
9. **Pitch direction to clear an obstruction (OQ2).** When a jump is obstructed and the bot pitches off
   the blocking body to retry, which way? Default coded = pitch DOWN (your existing fixed pick). Does that
   hold for a planet's dark side / a glaring star, or must it be body-aware?
10. **Arrival "let orbit acquire" 13s wait (OQ3).** arrival.toml has a 13s settle AFTER sc-assist orbit
    that is an orbit-ACQUISITION wait, not a jump-clearance wait. Killing it (per "kill the 13s waits") may
    change when the next-hop target locks. I'm HOLDING this one's deletion pending your call — the genuine
    jump-clearance 13s waits ARE being killed. Keep it, or is orbit-acquire reliably done sooner?
11. **Scope: sc_resume.toml + startup.toml have the SAME blind-wait jump tail (OQ4).** Your kill list named
    dock_resume + arrival only. Want the same clearance step in sc_resume + startup too (consistent), or
    leave them? startup's tail has a retry_anchor (step 19) that needs careful re-homing if we touch it.

## Already answered — thank you (no action)
- Q2 jump obstruction: HUD-only, stars+planets block, stations don't. CV-free StartJump-loop chosen.
- STARSMACK fires only on a real star-smack; planet-smack is the separate purple-vector case.

---

## AUDIT 2026-06-16 (workflow wj74tg65i) — newly-tracked defects & stubs

- **D1** launch.ps1 junction-probe misfire: reinstalls ed_autojump every launch from the C: path + offline hard-fail. ✅ **FIXED + pushed (329797d)** — realpath both sides + Resolve-Path $RepoRoot + offline-safe pip (Ensure-BuildTools); verified AT-1 C-junction=0, AT-2 G=0, AT-3 out-of-tree=2.
- **D2** nav-panel target: RETIRE the blind keypress macro and REPLACE ENTIRELY with CV-driven selection per project spec (operator correction 2026-06-16). Do NOT tune the blind macro (de-dup/pin-hold/max_rows were the wrong frame). Vehicle = WinRT parser-v2, which is LOST WORK and must be REBUILT (D5). Design draft: docs/superpowers/specs/2026-06-16-cv-navpanel-target-DESIGN-DRAFT.md — pending operator review. (Build council not yet dispatched — gated on the parser rebuild.)
- **D2b** Unnecessary post-SC-assist wait when target is already in front (gatewalk-efficiency-targets) — was tracked NOWHERE.
- **D3** EDMCOverlay.exe auto-launch crashes -> visible "not found" console window (mistaken for honk; honk is an in-process thread). Fix: repair EDMC OR [overlay] launch_if_absent=false + connect_timeout_s=2.0. Needs operator's exe-run error text.
- **D4** escape_vector.detect_escape_vector hard-returns NONE -> smack_recovery NEVER auto-fires; _escape_vector_grabber unwired. Needs blue/purple/no-vector calibration frames.
- **D5** explore/body_tour BLIND: nav_panel_ocr_enabled=False (config.py:90), parser uses pytesseract not ratified WinRT; WinRT parser-v2 is **LOST WORK** (worktree gone, never committed, unrecoverable from git) — must **REBUILD** navpanel_parser.py + ocr_winrt.py from the ed-navpanel-ocr-first-parser memory design (not harvest). Long pole; gates explore AND nav-panel target.
- **D6** ed_vision/hud_sc_indicators.py module ABSENT (only data .json); ctx.hud_grabber never injected -> confirm_orbiting permanently dead. Build detector + wire grabber OR remove the branch.
- **D7** OQ4: C1 jump-tail kill incomplete — blind wait s=13.0 + engage_jump + hold_alignment still in sc_resume/startup/smack_recovery (only arrival + dock_resume migrated).
- **D8** doc drift (this cleanup) + inert cleanup: 24 orphaned __pycache__ .pyc; v/ pytest cache git-tracked at repo root; stale dist-info (ed_explore records deleted worktree path, ed_autojump records C: origin); pyvenv.cfg records pre-move path.
- 5 registered-but-unreferenced steps (fix-or-delete per nothing-stays-unwired): body_tour, station_services, confirm_menu_item, pitch, press.
- Parallel scoop global python 3.14 editable-installs 4 of 6 packages (a second drift surface) — decide keep-in-sync vs remove.

---

## MAJOR GAP REGISTER — review tonight (consolidated 2026-06-16)

Single review surface. Source: redemption audit (wj74tg65i) + CV candidate register (wr38kdryp).
The desired-function gap-analysis council (w3spwcbaf) will add/confirm when it lands.
**Council status:** ✅ done · ⏳ council running · ◻️ no council dispatched yet.

### 🔴 Ship-safety — live, can crash the ship, no ratified fix · ◻️
- **smack-recovery pitch-astern false-"done"** (README defect #3 / CVR-12) — has thrown the ship INTO the star; C3 did NOT touch the recovery maneuver. No specified CV fix yet.
- **sc_resume star-ram** (README defect #2 / CVR-2) — P4 fast path drives a nose-on-star ship in; the blind nav-panel row-walk is doubling as a fake distance sensor. (Largely killed by OQ4 below once the jump fails closed on obstruction.)

### 🟢 Ship-now, buildable, ~no frames · ◻️ (each needs a go / one decision)
- **OQ4 jump-tail migration** (D7 / CVR-3) — propagate the built engage_jump_clearance into sc_resume/startup/smack_recovery; kills the star-ram backend + D7 + the smack 13s tail at once. #1 register fork; ratified + buildable today.
- **Orient failure-clocks** (CVR-14) — drop the 45s (orient_compass) / 30s & 75s (pitch_compass) / 18s (orient_widget_ring) wall-clock FAILURE gates on already-CV-converged loops to read-count + should_abort. The exact "clunky clock" class.
- **Exploration S0 self-block** (item 3 / CVR-10) — S0 fail-closes → the autoexplore tour NEVER runs today; simplify so it stops self-blocking (you set filters manually, bot just reads).
- **Audio readiness gate** (CVR-15) — SteelSeries Sonar virtual-endpoint timeout; multi-endpoint probe fix already written but unwired. Pure code-wiring.

### 🟡 Inert flagship features — blocked on calibration frames you capture · ◻️
- **Smack determination** (D4 / item 5 / CVR-5/6) — escape_vector returns NONE → recovery never auto-fires; needs blue (have) / purple / no-vector frames.
- **SC-assist HUD detector** (D6 / CVR-11) — hud_sc_indicators.py was never built; unblocks orbit-confirm + the four "orbit-acquire" 13s waits (OQ3). Confirm ORBITING/ALIGN frames exist or recapture.
- **Nav-panel parser-v2 REBUILD** (D5 / CVR-7 + CVR-1) — the LOST WinRT parser; gates BOTH explore AND nav-panel target. The long pole. Needs the planet-rich nav frame (item 4).

### ⚪ Scope questions — yes/no before any council · ◻️
- **FSS + DSS** (CVR-18) — zero implementation; in the >90%-CV push, or stay off indefinitely?
- **Docking / station-services CV** (CVR-9/19/20/21) — deferred later-scope, or pull in now? (station_menu detector + auto_launch already work; open piece is the blind request_docking Contacts-tab macro.)

### Already handled this session
- ✅ **D1** launcher fix (329797d, verified) · ✅ **D8** doc drift + v/ untrack
- ⏳ **gate-split / overlay-says-arrival** — design council w7azy0mcp running
- ⏳ **gap-analysis** desired-function meta-council w3spwcbaf running
- **D2 nav-panel target** — design draft done; build council pending the parser rebuild
- Hygiene (no council): 5 dead unreferenced steps; orphaned __pycache__ .pyc
