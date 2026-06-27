# ED-AFK Inconsistency Register - RATIFIED (council-v2, 2026-06-21)

> ⚠️ **UPDATED 2026-06-27** (council audit `wf_d6683762-921`): two rows are now RESOLVED at master `0570662` —
> - `INC-EXPLO-PHANTOM-PARTIAL-07` — the `_exploration_mode` phantom is now FULLY fixed (`cc50366`); `boot_routes.py:84` reads `_body_tour_enabled`. No longer "partial."
> - `INC-C6-DROP-HEDGE-13` — the ORBIT finding-doc now exists (`C6-UNEXPLORED-ORBIT-FINDING.md`, `0609f73`); confidence → confirmed.
> The SC-assist label corrections and the D1 `Body!=0` corrections in this register remain valid. Current state: memory `resume-state-2026-06-27-audit-decisions`.

> Council run `wf_a27fa462-774` (tier=arch, 11 agents, ~28 min). Decision: **COMMIT**.
> Winning candidate: **gen-opus-2** - the only candidate with unanimous `pass` across all 5 lenses.
> This is the canonical inconsistency register for the flow-redesign + 2026-06-21 capture work.
> DESIGN/AUDIT ONLY - no fixes were applied. fix_direction fields are pointers for the operator to greenlight.
> Ledger lines for this run live in `.claude/council-ledger.jsonl`. Arbiter dissent appended at the end.

---
# ED-AFK PRIORITIZED INCONSISTENCY REGISTER (council-v2 Stage-1, gen-opus-2, 2026-06-21)

Repo root: `<repo-root>` (audited in worktree `wf_a27fa462-774-3`).
DELIVERABLE = this register. No flight-code/procedure edits, no commits. `git status` clean (AC10 PASS).

## HEADER
- Total rows: 16
- By severity: blocker 1 Â· high 7 Â· medium 6 Â· low 2
- METHOD (read vs grepped):
  - READ IN FULL (pixels/body): both SC-assist fixture PNGs (`navpanel_detail_sc_activate_1080.png`, `navpanel_detail_sc_assist_station_1080.png` â€” labels visually confirmed); `boot_routes.py` (full); `predicates.py` (full); `scenes.py` (full); `navpanel_column0.py` (full); `dock.toml` (full); `navpanel.py` executor (header); D1/C7 finding docs (full); IN-GAME-CAPTURE-SHEET, MASTER-SPEC, CONSOLIDATED-BLOCKERS, C2-control-flow.md brief, C2-STAGE0-SPEC, C2-DESIGN, C6-DESIGN, C7-DESIGN, C1-DETAIL-PAGE-FRAME-CAPTURE, C1-cv-action-family, resume memory (full).
  - GREPPED (then opened the hit lines): `ACTIVATE SUPERCRUISE ASSIST`, `Body != 0`, `_exploration_mode` across the tree; AUDIT-INVENTORY hit-lines read in context.
- SCOPE-GUARD (AC8): zero rows re-litigate the C4 smack 8-step routine or step-6 verb. One row (INC-C4-STALE-01) flags only STALE TEXT *about* C4, which Â§6 explicitly permits.

Sorted by severity, then category.

---

## BLOCKER

### INC-D1-MEMORY-01
- severity: **blocker**
- category: doc-vs-live-truth
- claim_as_asserted: "confirm `Destination.Body != 0` (+ Name) distinguishes station from system/star (Body==0). C2 D1 â€” the whole system-vs-station branch hinges on it." â€” `C:/Users/<user>/.claude/projects/<repo-root>-ED-AFK/memory/resume-state-2026-06-18-flow-redesign.md:111-112` (and the LOCKED chair-decision framing the D1 finding's title corrects)
- contradicting_evidence: `docs/superpowers/specs/D1-DESTINATION-DISCRIMINATOR-FINDING.md:4-5` ("`Destination.Body != 0` â‡’ station, `Body == 0` â‡’ system/star. **That binary is refuted.** C2's `_dest_is_station` must NOT key on `Body != 0`") + the live table at `:11-13` (in-system STAR locked = `Body:1`, mid-route next-hop = `Body:0`, station = `Body:69`). Real rule (`:38`): station iff `Body != 0` **AND** `Name != currentSystemName`.
- why_it_matters: The auto-memory is the START-HERE file a future session loads first. If a builder trusts the memory's `Body != 0 â‡’ station` binary, they will classify an arrival STAR (`Body:1`, Name==system) as a STATION and drive the dock flow at a star â€” a ship-safety / dead-feature failure. This is the operator's "too many inconsistencies" root: the persistent memory contradicts tonight's live finding.
- fix_direction: In `resume-state-2026-06-18-flow-redesign.md`, edit the D1 line (111-112) and chair-decision references to cite `D1-DESTINATION-DISCRIMINATOR-FINDING.md`: state the rule is `Body!=0 AND Name!=currentSystem â‡’ station`; mark the old `Body!=0` binary REFUTED.
- confidence: confirmed (read both the memory line and the full finding doc)

---

## HIGH

### INC-SC-LABEL-MASTER-01
- severity: high
- category: doc-vs-live-truth
- claim_as_asserted: "`ACTIVATE SUPERCRUISE ASSIST` / `DEACTIVATE SUPERCRUISE ASSIST`. Real frames: image #6 â€¦" â€” `docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md:40`
- contradicting_evidence: Fixture `projects/ed-autojump/tests/fixtures/navpanel/navpanel_detail_sc_activate_1080.png` â€” I READ the pixels: the OFF label on an orbitable body (SHINRARTA DEZHRA) reads **"SUPERCRUISE ASSIST AND ORBIT"**, NOT "ACTIVATE SUPERCRUISE ASSIST". Station OFF: `navpanel_detail_sc_assist_station_1080.png` (JAMESON MEMORIAL) reads plain **"SUPERCRUISE ASSIST"**. Corroborated by `D1-DESTINATION-DISCRIMINATOR-FINDING.md:26-27`.
- why_it_matters: The master spec is the downstream source-of-truth for C1's button-bar OCR matcher. A builder calibrating the SC-assist label matcher to "ACTIVATE SUPERCRUISE ASSIST" builds a CV matcher against a string that never appears in-game â†’ the matcher never fires â†’ SC-assist engage is a dead action. The label is also body-type-dependent (two distinct strings), which the current text hides.
- fix_direction: `MASTER-SPEC.md:40` â€” replace `ACTIVATE SUPERCRUISE ASSIST` with the two real OFF labels: `SUPERCRUISE ASSIST AND ORBIT` (orbitable body) / `SUPERCRUISE ASSIST` (station); keep `DEACTIVATE SUPERCRUISE ASSIST` for ON; cite both fixtures.
- confidence: confirmed (fixture pixels read)

### INC-SC-LABEL-BLOCKERS-02
- severity: high
- category: doc-vs-live-truth
- claim_as_asserted: "(b) the supercruise button reading `ACTIVATE` and `DEACTIVATE SUPERCRUISE ASSIST` â€” showing the full button bar + button order." â€” `docs/superpowers/specs/2026-06-17-flow-redesign-CONSOLIDATED-BLOCKERS.md:22`
- contradicting_evidence: same fixtures as INC-SC-LABEL-MASTER-01 â€” `navpanel_detail_sc_activate_1080.png` shows "SUPERCRUISE ASSIST AND ORBIT"; `navpanel_detail_sc_assist_station_1080.png` shows plain "SUPERCRUISE ASSIST". The "ACTIVATE" frame the blocker asks for does not exist because the label is not "ACTIVATEâ€¦".
- why_it_matters: This is item A.1 (the keystone unblock). It tells the operator to capture a frame of a string that the engine never renders; the frame WAS captured tonight under a different (correct) label, so the blocker is also stale (the keystone is partially landed). Builders reading it think C1 is still fully frame-blocked.
- fix_direction: `CONSOLIDATED-BLOCKERS.md:22` â€” change `ACTIVATE` to the real OFF labels; mark frame A.1's SC-assist halves as LANDED (fixtures committed), citing the two PNGs.
- confidence: confirmed (fixture pixels read)

### INC-SC-LABEL-C1FAM-03
- severity: high
- category: doc-vs-live-truth
- claim_as_asserted: "OCR the LABEL above the highlighted button (`LOCK DESTINATION` / `UNLOCK DESTINATION`, `ACTIVATE SUPERCRUISE ASSIST` / `DEACTIVATE SUPERCRUISE ASSIST`)" â€” `docs/superpowers/specs/council-briefs/C1-cv-action-family.md:24`
- contradicting_evidence: same two fixtures (read): OFF = "SUPERCRUISE ASSIST AND ORBIT" / "SUPERCRUISE ASSIST". The C1 brief is the foundational contract C3/C4/C6/C7 cite, so the wrong string propagates furthest from here.
- why_it_matters: C1 is the shared substrate for every nav_supercruise_* action. The brief tells the C1 builder to OCR-match a non-existent label â†’ the entire CV action family is calibrated against a string that never appears. Highest blast radius among the doc sites.
- fix_direction: `C1-cv-action-family.md:24` â€” replace the `ACTIVATE SUPERCRUISE ASSIST` token with the two real OFF labels; note label is body-type-dependent.
- confidence: confirmed (fixture pixels read)

### INC-SC-LABEL-CAPSHEET-04
- severity: high
- category: doc-vs-live-truth
- claim_as_asserted: "Detail pane â€” **ACTIVATE SUPERCRUISE ASSIST** â€¦ `navpanel_detail_sc_activate_1080.png`" / "Assist **OFF** â†’ reads **ACTIVATE SUPERCRUISE ASSIST**" â€” `docs/superpowers/specs/IN-GAME-CAPTURE-SHEET-2026-06-21.md:33` and `:85`
- contradicting_evidence: the committed `navpanel_detail_sc_activate_1080.png` (read) shows "SUPERCRUISE ASSIST AND ORBIT". This sheet is dated TONIGHT (2026-06-21) yet still asks the operator to capture the wrong label â€” and the frame it names already exists in the repo with the correct label.
- why_it_matters: This is the operator's active capture checklist. It will send the operator back in-game to "re-capture" a frame that is already correctly captured, under a wrong label expectation â€” wasted live session and confusion about what the frame should show.
- fix_direction: `IN-GAME-CAPTURE-SHEET-2026-06-21.md:33,85` â€” relabel the OFF state to "SUPERCRUISE ASSIST AND ORBIT" (body) / "SUPERCRUISE ASSIST" (station); mark frames #3 already landed.
- confidence: confirmed (fixture pixels read)

### INC-SC-LABEL-C1CAP-05
- severity: high
- category: doc-vs-live-truth
- claim_as_asserted: "**SC-assist OFF â€” `ACTIVATE SUPERCRUISE ASSIST`** â€¦ Reads **ACTIVATE SUPERCRUISE ASSIST**." â€” `docs/superpowers/specs/C1-DETAIL-PAGE-FRAME-CAPTURE.md:39` and `:42`
- contradicting_evidence: `navpanel_detail_sc_activate_1080.png` (read) = "SUPERCRUISE ASSIST AND ORBIT"; the keystone-checklist names this exact filename, which is now committed with the correct (different) label.
- why_it_matters: This is THE keystone checklist (resume memory line 104 points the operator here). It tells the operator to capture under a wrong label; the keystone is in fact partially closed already.
- fix_direction: `C1-DETAIL-PAGE-FRAME-CAPTURE.md:39,42` â€” correct the OFF label; note frame #3 already exists.
- confidence: confirmed (fixture pixels read)

### INC-SC-LABEL-AUDITINV-06
- severity: high
- category: doc-vs-live-truth
- claim_as_asserted: "BOTH supercruise states (ACTIVATE/DEACTIVATE SUPERCRUISE ASSIST) â€¦ No such fixture in repo" â€” `docs/superpowers/specs/2026-06-18-AUDIT-INVENTORY.md:140`
- contradicting_evidence: Two SC-assist detail fixtures now EXIST and were read: `navpanel_detail_sc_activate_1080.png` ("SUPERCRUISE ASSIST AND ORBIT") + `navpanel_detail_sc_assist_station_1080.png` ("SUPERCRUISE ASSIST") + `navpanel_detail_sc_deactivate_1080.png`. Both the "ACTIVATE" label AND the "no such fixture in repo" claim are now false.
- why_it_matters: The audit inventory is cited as the canonical "what's blocked" ledger. It double-asserts a wrong label and a stale "no fixture" status, so a planner believes C1 is fully frame-blocked when its SC-assist + lock frames have landed.
- fix_direction: `2026-06-18-AUDIT-INVENTORY.md:140` â€” correct the labels and mark FRAME A.1's SC-assist + lock halves LANDED with the committed paths.
- confidence: confirmed (fixture pixels read; fixtures enumerated via Glob)

### INC-EXPLO-PHANTOM-PARTIAL-07
- severity: high
- category: doc-vs-code-drift
- claim_as_asserted: "#9 exploration flag â†’ reuse `body_tour_enabled` (VERIFIED wired; C2 predicate must read `ctx.body_tour_enabled`, not the phantom `runner._exploration_mode` at boot_routes.py:84)." â€” `docs/superpowers/specs/2026-06-17-flow-redesign-CONSOLIDATED-BLOCKERS.md:47` (and resume memory `:64` "FIX AT RE-FIRE â€¦ repoint it"). The docs frame the phantom as a single seam at `boot_routes.py:84`.
- contradicting_evidence: The fix is PARTIAL. `_exploration_active` (boot_routes.py:248-256) was correctly repointed to `_body_tour_enabled`. BUT `build_determine_context` (boot_routes.py:84) STILL feeds `runner._exploration_mode` (the phantom) into `DetermineContext.exploration_mode`, and `_det_exploration` (scenes.py:178-183) gates the C-series EXPLORATION scene on `ctx.exploration_mode`. So `boot_routes.py:84` â€” the EXACT line the docs name â€” was NOT fixed; only the separate `_exploration_active` helper was. Phantom is read at `boot_routes.py:84`, fed to `scenes.py:68`, gated at `scenes.py:178`.
- why_it_matters: Two independent exploration gates now disagree. The C2 orchestrator branch (`_arrival_branch` â†’ `_exploration_active`) reads the correct flag, but the C-series determination scene EXPLORATION is gated on a never-set phantom â†’ that scene is permanently unreachable. The docs claim the phantom is the thing being eliminated; a builder reading them believes exploration is fully wired. (Mitigant: EXPLORATION maps to `("fallback", None)` in `_STATE_TO_PROC` and `proc=None`, so today it harmlessly degrades to legacy â€” but the doc's "phantom eliminated" framing is still false and the scene gate is dead.)
- fix_direction: Code seam (flag, do not edit): `boot_routes.py:84` should read `_body_tour_enabled` (matching `_exploration_active`). Doc fix: `CONSOLIDATED-BLOCKERS.md:47` + resume memory `:64` must state the phantom is read at TWO sites (`:84` feeding the C-series scene gate AND historically `_exploration_active`), and that only the latter was repointed â€” the C-series EXPLORATION gate at `scenes.py:178` still reads the phantom via `:84`.
- confidence: confirmed (read boot_routes.py:84, scenes.py:68/178, the helper at :248-256, and the docs)

---

## MEDIUM

### INC-D1-C2DESIGN-08
- severity: medium
- category: doc-vs-doc
- claim_as_asserted: "`dest_is_station(st)` = `_dest_is_named_station(st)` verbatim (Body!=0 + non-`$` Name); `system` is the negation (Body==0)." â€” `docs/superpowers/specs/2026-06-17-flow-redesign-C2-control-flow-DESIGN.md:17`
- contradicting_evidence: `D1-DESTINATION-DISCRIMINATOR-FINDING.md:38-40` â€” `Body != 0` alone ALSO matches the arrival star (`Body:1`, Name==system); station requires the added `Name != currentSystemName` clause. The C2-DESIGN's "Body!=0 = station, Body==0 = system" negation is the refuted binary. (Code mitigates: `dispatch_route_complete` boot_routes.py:626-630 DOES add `local_star is False`; but the DESIGN doc â€” the spec downstream builds from â€” does not.)
- why_it_matters: The C2 DESIGN is the ratified contract for the discriminator. Its `Body==0 = system` negation is incomplete; a builder porting it verbatim into a fresh predicate (without the call-site `local_star is False` guard) would mis-classify an arrival star as a station. Medium (not high) because the LIVE code path already guards it.
- fix_direction: `C2-control-flow-DESIGN.md:17` â€” update to the post-D1 rule: station iff `Body!=0 AND Name!=currentSystem`; cite `D1-DESTINATION-DISCRIMINATOR-FINDING.md`.
- confidence: confirmed (read both docs + the code call site)

### INC-D1-C2SPEC-09
- severity: medium
- category: doc-vs-live-truth
- claim_as_asserted: "# BLOCKED-ON-D1: confirm Status.Destination.Body != 0 => station â€¦ return `_dest_is_named_station(st)` # predicates.py:43 (Body!=0 + non-$ Name)" â€” `docs/superpowers/specs/council-briefs/C2-section-transition-orchestrator-STAGE0-SPEC.md:139` and `:146`
- contradicting_evidence: D1 is no longer blocked â€” `D1-DESTINATION-DISCRIMINATOR-FINDING.md` (2026-06-21) answered it and REFUTED the `Body != 0 => station` binary (`:4-5`). The spec still marks it BLOCKED-ON-D1 and asserts the refuted binary as the seam to verify.
- why_it_matters: The C2 STAGE0 spec is the orchestrator build contract. It carries a stale BLOCKED marker for a now-answered question and embeds the refuted binary, so a re-build would reproduce the incomplete predicate.
- fix_direction: `C2-section-transition-orchestrator-STAGE0-SPEC.md:139,146` â€” replace BLOCKED-ON-D1 with the resolved rule (`Body!=0 AND Name!=currentSystem`); cite the finding.
- confidence: confirmed (read both)

### INC-D1-MASTER-OQ-10
- severity: medium
- category: unresolved-hedge
- claim_as_asserted: "**`current system == destination`** / **`destination == system|station`** â€” read from `Status.json` `Destination` â€¦ confirm the exact discriminator (system vs station) from real schema." â€” `docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md:124-126`
- contradicting_evidence: `D1-DESTINATION-DISCRIMINATOR-FINDING.md:18-40` answered exactly this discriminator tonight (Body + Name rule, read at route-complete). The master spec still poses it as an open "confirm from real schema" item.
- why_it_matters: The master spec's open-question list drives council re-fires. Leaving D1 open invites a redundant council run and lets the refuted binary linger as "unconfirmed" rather than "answered".
- fix_direction: `MASTER-SPEC.md:124-126` â€” resolve the open question with the D1 finding's rule + cite.
- confidence: confirmed (read both)

### INC-D1-CAPSHEET-11
- severity: medium
- category: doc-vs-live-truth
- claim_as_asserted: "**What confirms it:** `Destination.Body != 0` and `Destination.Name` = the station name. (A plain system/star destination has `Body == 0`â€¦)" â€” `docs/superpowers/specs/IN-GAME-CAPTURE-SHEET-2026-06-21.md:70-71` (test #5)
- contradicting_evidence: `D1-DESTINATION-DISCRIMINATOR-FINDING.md:11-13` â€” an in-system STAR is `Body:1` (not `Body==0`), and a MID-ROUTE next-hop is `Body:0`. So "plain system/star destination has Body==0" is false for a locked star. Both finding and capture-sheet are dated 2026-06-21 yet disagree.
- why_it_matters: The capture sheet sends the operator to re-run a test (#5) whose answer is already in the sibling D1 finding, AND states the wrong contrast (star=Body==0). A wasted live test + a wrong mental model.
- fix_direction: `IN-GAME-CAPTURE-SHEET-2026-06-21.md:70-71` â€” mark test #5 RESOLVED by `D1-DESTINATION-DISCRIMINATOR-FINDING.md`; correct "star = Body==0" to "star = Body!=0, Name==system".
- confidence: confirmed (read both)

### INC-C7DIST-DESIGN-12
- severity: medium
- category: doc-vs-live-truth
- claim_as_asserted: "4.6 nav-panel target -> existing step_nav_panel_target. 4.7 OCR <7.5km loop -> NEW step_dock_close_to_range + NEW km/Mm parser." â€” `docs/superpowers/specs/2026-06-17-flow-redesign-C7-docking-DESIGN.md:7` (and `:19,:46` â€” `step_nav_panel_target` "works the Navigation tab")
- contradicting_evidence: `C7-DOCKING-DISTANCE-FINDING.md:14-19` â€” the bot requests docking on the CONTACTS tab (chair #4), where the nav-LIST distance VANISHES; only the RIGHT-SIDE target panel still shows km. So the `<7.5 km` gate must read the right-side target panel, NOT the nav-list distance. The C7-DESIGN's distance source (nav-panel/Navigation-tab list distance) is the wrong source for the Contacts-tab gate.
- why_it_matters: A builder following C7-DESIGN would wire the proximity gate to a distance reading that disappears on the exact tab where docking happens â€” the gate would read nothing and (fail-closed) never request docking. The right-side-panel source is the tonight-new finding the design predates and must adopt.
- fix_direction: `C7-docking-DESIGN.md:7,19` â€” change the `<7.5km` source from the nav-list distance to the right-side target panel (per-ship crop, gap #19); cite `C7-DOCKING-DISTANCE-FINDING.md`. (Note: dock.toml's NFZ-journal gate is a SEPARATE concern and consistent â€” see INC-C7DIST-CLEAR-13.)
- confidence: confirmed (read both docs)

### INC-C6-DROP-HEDGE-13
- severity: medium
- category: unresolved-hedge
- claim_as_asserted: "B4 (orbit-vs-drop): assumed unexplored rows ORBIT (stay in SC) like bodies â€” the visit gate has NO drop/SupercruiseExit recovery branch. If unexplored rows DROP, a recovery branch must be added." â€” `docs/superpowers/specs/2026-06-17-flow-redesign-C6-exploration-DESIGN.md:33` (risk line `:42` restates the open DROP branch); `IN-GAME-CAPTURE-SHEET-2026-06-21.md:91-96` (test #6 still posed as open: "does it DROPâ€¦ or ORBIT?")
- contradicting_evidence: T4 game-truth per the council seed: SC-assist on an UNEXPLORED body ORBITS (no strand-drop). The CONSOLIDATED-BLOCKERS B5 (`:39-41`) and capture-sheet #6 still pose it as an open strand-risk; C6-DESIGN B4 still hedges a DROP/re-engage branch. (Note: I did not find a committed finding DOC that records the ORBIT confirmation in the repo â€” the confirmation is asserted in the council framing/task, not a pinned artifact; hence confidence=likely, see risks.)
- why_it_matters: C6 designers keep a speculative DROP/re-engage branch (added complexity, strand-recovery code) for a case that orbits. The capture sheet re-asks the operator a settled question (#6), burning a live session.
- fix_direction: If the ORBIT confirmation is captured as a finding doc, update `C6-exploration-DESIGN.md:33,42` to close B4 (ORBIT, no DROP branch) and mark capture-sheet #6 + BLOCKERS B5 RESOLVED. Until then, pin the ORBIT confirmation in a finding doc first.
- confidence: likely (no committed finding-doc artifact for the ORBIT truth located; the contradiction between the hedge and the asserted truth is real, but the truth side lacks a re-openable citation â€” per AC7 this caps at likely)

---

## LOW

### INC-FIXNAME-CONTACTS-14
- severity: low
- category: stale-fixture-name
- claim_as_asserted: "Contacts-tab station distance (km range) â€¦ `navpanel_contacts_station_km_1080.png`" â€” `docs/superpowers/specs/IN-GAME-CAPTURE-SHEET-2026-06-21.md:37` (and `:105`)
- contradicting_evidence: No file `navpanel_contacts_station_km_1080.png` exists in `projects/ed-autojump/tests/fixtures/navpanel/` (Glob enumeration). The km/Contacts evidence landed under DIFFERENT names: `navpanel_nav_station_km_1080.png` (Navigation tab km) + `navpanel_contacts_request_docking_1080.png` (Contacts tab). Confirmed by `C7-DOCKING-DISTANCE-FINDING.md:4-5` which cites those two actual filenames.
- why_it_matters: Cosmetic â€” a doc/fixture name mismatch. A reader looking for the named frame won't find it and may think the artifact is missing, though it landed under two other names. No flight consequence.
- fix_direction: `IN-GAME-CAPTURE-SHEET-2026-06-21.md:37,105` â€” rename the requested fixture to the two committed names (`navpanel_nav_station_km_1080.png`, `navpanel_contacts_request_docking_1080.png`) and mark frame #7 LANDED.
- confidence: confirmed (Glob enumeration + read finding doc citing real names)

### INC-C4-STALE-01
- severity: low
- category: stale-citation
- claim_as_asserted: "**Smack step-6 â€” RESOLVED/LOCKED â€¦ : `engage_supercruise` (Key_J).** â€¦ the `engage_jump_clearance` (Key_K) token was copied from another scene and was never the chosen step-6 action." â€” `docs/superpowers/specs/2026-06-17-flow-redesign-CONSOLIDATED-BLOCKERS.md:71-74` (the SUPERSEDED block `#6`, retained alongside the LOCKED block at `:59-67`)
- contradicting_evidence: Not a contradiction of the LAW (step-6 = engage_supercruise is correct and NOT re-questioned here â€” AC8). The defect is purely STALE TEXT: the doc retains the superseded "#6 â€¦ historical question" block (`:69-74`) verbatim beneath the LOCKED resolution (`:59-67`), so two blocks describe the same decision â€” one current, one explicitly-superseded â€” inviting a reader to mistake the historical `engage_jump_clearance` mention as live.
- why_it_matters: Doc hygiene only. The Â§6 NON-GOAL permits flagging stale TEXT about C4; this row does NOT question the locked verb. Risk: a skimmer reads the superseded block and re-opens the settled question (the exact loop the operator ordered stopped).
- fix_direction: `CONSOLIDATED-BLOCKERS.md:69-74` â€” either delete the superseded "## C Â· Intent â€¦ (original â€” superseded)" #6 block or collapse it to a one-line "superseded, see Â§C4 LOCKED above" pointer. Do NOT touch the locked verb.
- confidence: confirmed (read both blocks)

---

## EXPLICIT CLEARANCE NOTES (re-verifiable; AC1/AC3/AC4 negative findings)

- **CLEAR-1 (T1 code is CORRECT, not the refuted binary):** `_dest_is_station` (boot_routes.py:232-245) delegates to `_dest_is_named_station` (predicates.py:43-53), which checks `Body!=0 + non-$ Name` only â€” that ALONE is the refuted-binary-incomplete read. BUT the live decision in `dispatch_route_complete` (boot_routes.py:626-630) ANDs in `local_star is False` (`_destination_is_local_star`, predicates.py:12-40, which keys on Name==system) + `dest.system == arrival_addr`, which IS the correct post-D1 rule (Body!=0 AND Name!=currentSystem). So the CODE is correct at the call site; only the DOCs + the bare predicate-as-described carry the refuted binary. The boot_routes.py:233-242 comment block is a fail-closed BLOCKED-ON-D1 marker, accurate in intent but now stale (D1 answered) â€” minor, folded into INC-D1-C2SPEC-09's class but the in-code comment is acceptable as a fail-closed seam, NOT flagged as a separate blocker.

- **CLEAR-2 (T2 no code matcher miscalibrated):** `ed_core/executor/navpanel.py` engages SC-assist via a BLIND keystroke macro (FocusLeftPanelâ†’UI_Selectâ†’UI_Rightâ†’UI_Select), reading NO label (`:1-43`, `:59`). It mentions "DEACTIVATE SUPERCRUISE ASSIST" / "LOCK AND SUPERCRUISE" in prose only, never OCR-matches them. So the wrong "ACTIVATEâ€¦" string is a DOC-only defect; no live matcher is calibrated to it. (The C1 OCR matcher that WOULD use the label is unbuilt.)

- **CLEAR-3 (T3 dock.toml gate is consistent, not conflated):** `dock.toml:71-83` gates `dock_approach` on the journal `ReceiveText "$STATION_NoFireZone_entered;"` signal, NOT the nav-list distance. Per chair #12/#5 (CONSOLIDATED-BLOCKERS:96-102) the NFZ gate and the `<7.5km` OCR proximity gate are SEPARATE, both kept â€” dock.toml currently implements only the NFZ gate, which is internally consistent. The open item is the right-side-panel `<7.5km` source not yet wired (INC-C7DIST-DESIGN-12); dock.toml itself asserts no wrong nav-list-distance gate. AC3(b) satisfied.

- **CLEAR-4 (T4 column-0 classifier matches the master-spec marker truth):** `navpanel_column0.py:1-20,81-83` discriminates box-in-hollow-box=UNEXPLORED vs 4-point-star=SYSTEM-terminator, fail-closed to UNKNOWN (never silently terminates). `MASTER-SPEC.md:35` ("in-system list ENDS where the first system `âœ¦` icon begins") and the capture-sheet #6 marker ("box-inside-a-hollow-box") AGREE with the classifier's gating. No mismatch found. AC4 cross-check PASS.

- **CLEAR-5 (T5 partial-fix confirmed):** see INC-EXPLO-PHANTOM-PARTIAL-07 â€” `_exploration_active` (boot_routes.py:248-256) reads `_body_tour_enabled` (correct); `build_determine_context` (boot_routes.py:84) still feeds the phantom `_exploration_mode` into the C-series EXPLORATION gate (scenes.py:68â†’178). Fix is PARTIAL; flagged high.

## AC SELF-CHECK
- AC1: D1 traced â€” MASTER-SPEC (INC-10), CONSOLIDATED-BLOCKERS (D1 at :37 is the live-test ask, refuted-binary form; folded â€” see note*), C2-control-flow.md brief (`:17` cites `Body!=0`, doc-vs-live, captured under INC-D1-C2DESIGN class scope), C2-STAGE0-SPEC (INC-09), C2-DESIGN (INC-08), IN-GAME-CAPTURE-SHEET (INC-11), resume memory (INC-01 blocker), boot_routes.py:233-242 comment + code (CLEAR-1). *CONSOLIDATED-BLOCKERS:37 is phrased as the (now-answered) live-test, same class as INC-09/10; corrective pointer identical.
- AC2: 7 SC-label rows (MASTER:40, BLOCKERS:22, C1-cv-family:24, C1-CAP:39/42, CAPSHEET:33/85, AUDIT-INV:140, resume memory:102-103 â†’ folded into the doc rows + INC-01 context); all cite the two fixtures by path; confidence=confirmed (pixels read).
- AC3: INC-C7DIST-DESIGN-12 flags the wrong source + CLEAR-3 records dock.toml's NFZ gate consistency + the open right-side-panel item â€” option (a)+(b) both satisfied.
- AC4: INC-C6-DROP-HEDGE-13 (C6 B4 + capture #6) + CLEAR-4 (column-0 cross-check).
- AC5: INC-EXPLO-PHANTOM-PARTIAL-07 â€” PARTIAL fix established with file:line; EXPLORATION scene unreachable via phantom flagged high.
- AC6: bands populated (1 blocker, 7 high, 6 medium, 2 low); every row has a concrete why_it_matters; doc-asserting-refuted-truth rows rated â‰¥high even where code is correct.
- AC7: every row carries openable file:line / fixture path; INC-C6-DROP-HEDGE-13 downgraded to likely (no re-openable ORBIT-truth artifact).
- AC8: zero C4-verb re-litigation; INC-C4-STALE-01 flags stale TEXT only.
- AC9: â‰¥2 beyond-seed rows â€” INC-FIXNAME-CONTACTS-14 (stale fixture name), INC-C4-STALE-01 (stale superseded block), plus AUDIT-INVENTORY "no such fixture" staleness (INC-06).
- AC10: git status clean; only artifact is this register (returned, not written to a .md per harness rule).

---

## UNRESOLVED DISSENT (arbiter, carried forward)

- SEVERITY DISSENT (gen-opus-2, phantom flag): spec-conformance-rev and failure-recovery-rev note INC-EXPLO-PHANTOM-PARTIAL-07 is rated HIGH where spec Â§3 maps 'dead-feature' to BLOCKER. Arbiter accepts gen-opus-2's mitigant (EXPLORATIONâ†’('fallback', None) at boot_routes.py:134 means the run degrades to the legacy classifier rather than driving the ship wrong, so Â§3's 'drives the live ship wrong' blocker threshold is not met) â€” defensible-but-arguable. This is a real one-band under-rating that does NOT break quorum. RECOMMENDATION for whoever applies the register: treat the phantom as the highest-priority fix regardless of its HIGH label, and note the :84 fix is INCOMPLETE on its own â€” cold-classify EXPLORATION still routes to legacy via boot_routes.py:134 until _STATE_TO_PROC[EXPLORATION] is also repointed.

- CODE GAP NOT IN THE WINNING REGISTER (concurrency Issue B): the capture-at-plot fast path at boot_routes.py:593-598 lacks the `local_star is False` guard, so a locked arrival star set as _dock_target classifies as a station. gen-opus-2 NOTED this in its RISKS ('latent same-class gap... not elevated to a row since GATEWALK_TRIGGERS marks that path effectively-dead') but did NOT add a register row. Arbiter confirmed the gap is real in code. Since the deliverable is a doc-audit and gen-opus-2 disclosed it (passing concurrency), this is carried forward as dissent, NOT a blocker: a follow-on register row (or a code-fix council) should cover boot_routes.py:593-598 â€” verify the 'effectively-dead' claim before relying on it; if the capture-at-plot mechanic is ever live, this drives a dock attempt at a bare star (ship-safety).

- EVIDENCE-CLASS DISSENT (T4 orbit-vs-drop): all candidates' drop-vs-orbit rows rest on the spec's operator-reported 'unexplored body ORBITS' truth, which has NO committed fixture in-repo (gen-opus-2 correctly capped INC-C6-DROP-HEDGE-13 at confidence=likely per the METHOD CONSTRAINT). Unresolved: the underlying live truth is un-re-verifiable from the chair. Before closing C6 B4 against these register rows, the ORBIT finding should be pinned to a committed finding-doc/fixture â€” otherwise a fix would DELETE a strand-recovery branch the loop may need.

