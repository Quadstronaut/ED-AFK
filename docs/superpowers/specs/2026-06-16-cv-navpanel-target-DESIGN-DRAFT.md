# CV-Driven Nav-Panel TARGET — DESIGN DRAFT (for operator review)

**Status:** DRAFT — not implemented, not ratified, no code/TOML changed.
**Author:** design agent, 2026-06-16. **Decides nothing live.**
**Operator mandate (2026-06-16):** the blind nav-panel TARGET macro "was supposed to be
replaced ENTIRELY by computer vision... I don't want you blindly trying, I want to see it."
This doc grounds that replacement in the actual code + spec so you can read and direct it.
Every claim below is cited to a `file:line`, a memory, or command output. Where the spec
does not settle a thing it is marked **OPEN QUESTION**, not invented.

---

## What's being replaced and why

The thing being retired is the **blind nav-panel TARGET macro** — `step_nav_panel_target`
(`projects/ed-autojump/src/ed_autojump/flow/steps.py:379-512`) driving `target_via_navpanel`
→ `_target_pin_and_walk` (`projects/ed-core/src/ed_core/executor/navpanel.py:160-240`).

How it works today (no row vision — it reads the *compass*, never the *panel*):
- Open the left panel, pin the cursor to row 0 (`UI_Down` once, then **held** `UI_Up` for
  `pin_hold_s = 4.0s`), walk `rows_down` taps down, `UI_Select` x2 to lock
  (`navpanel.py:226-240`).
- Verify the lock *indirectly*: a compass-dot read (`_measure`, layer 1) proves *a* lock
  exists; `Status.Destination.Name` vs the current system (`_destination_is_local_star`,
  layer 2) proves it's the *star*. No read of which panel row the cursor is actually on.
- On a dot-miss it re-runs the **entire** macro on the same row; on a wrong-body it advances
  one row and re-runs. Loop bound: `while row < max_rows and macros < max_rows + max_toggles`
  (`steps.py:464`).

**The over-fire cost (audit FRONT 4, CONFIRMED_BROKEN; defect D2):**
- **Structural duplication.** Every hop fires the macro ≥2× per procedure by design:
  `arrival.toml` at step 1b (line 42, wide bound) **and** step 3 (line 72, `max_rows=3`);
  `startup.toml` at steps 0/2/14 (lines 87/94/111).
- **Unbounded internal retry.** With the wide-bound call (`max_rows=10`, `max_toggles=4`,
  the `step_nav_panel_target` defaults at `steps.py:381-382`) the loop runs **up to 14 full
  blind macros** in a single step invocation (`steps.py:464`).
- **Each macro is slow and partly blind.** A held 4s `UI_Up` pin + `settle_s=0.4s` between
  every key, then up to `verify_reads=4` compass reads (0.4s apart) + up to 4 status reads.
  Audit estimate: ~6-9s per macro+verify cycle; worst-case single invocation ≈ 60-90s —
  matching the operator's "navpanels a thousand times / jumps are too long" complaint
  ([[gatewalk-efficiency-targets]] §1).

**Why CV replaces it (not tunes it):** the macro is blind to *which row the cursor sits on*,
so it brute-forces row identity by re-locking + re-reading Status/compass after each attempt.
Reading the panel directly collapses the retry loop to a single counted walk to a **known row
index**, and lets the caller *skip the lock entirely when the target is already locked* — the
two behaviours that produce the over-fire. The audit explicitly flags tuning levers
(de-dup / `pin_hold_s` / `max_rows`) as the **wrong frame**: per project spec the macro is to
be **replaced entirely by CV** ([[ed-navpanel-target-replace-with-cv]];
[[resume-state-2026-06-16-redemption-audit]] D2).

---

## What already exists

Precise BUILT / STUB / MISSING status — verified against current `master` (d2f1a76), not memory.

### Parser-v2 (the intended CV vehicle) — **LOST WORK, off disk**
The ratified WinRT OCR parser-v2 ([[ed-navpanel-ocr-first-parser]] ROUND 2: `gen-opus-1`
base, `ac_pure 28/28`, `ac_frames 23/23`) was built in worktree `wf_d1765b5a-d6c-2` and
**never committed**. As of this audit it is **gone**:
- `git worktree list` shows only `wf_0884c111-8cc-2/-3` (both at d2f1a76); no `d1765`/`d6c`
  worktree.
- `git branch -a` / reflog: **no** `d1765`/`d6c` branch.
- `git log --all -- **/navpanel_parser.py **/ocr_winrt.py **/navpanel_overlay_map.py`:
  **zero commits** ever introduced these files.
- On-disk `find`: the three files (`navpanel_parser.py`, `ocr_winrt.py`,
  `navpanel_overlay_map.py`) **do not exist** anywhere in the tree.
- `git fsck` dangling commits are unrelated (route-complete / smack-retry WIPs).

**Conclusion: parser-v2 is unrecoverable from git — it was uncommitted worktree content and
the worktree is pruned.** D5 / audit FRONT 7 assume it can be "harvested from the worktree";
that assumption is now **false** and must be flagged to the operator. The validated *design*
survives in [[ed-navpanel-ocr-first-parser]] (whole-region WinRT OCR + journal-grounding +
OCR-word-bbox anchoring, no homography), but the **code must be rebuilt**, not harvested.

### Current on-disk parser — `navpanel_reader.py` (pytesseract, CALIBRATION-PENDING)
`projects/ed-vision/src/ed_vision/navpanel_reader.py` (from commit 8ed3ba8, BC1). Three
layers, deliberately split so the selection brain is testable headless:
- **PARSE** (`parse_nav_panel_rows`, pure, BUILT + real-frame-tested): OCR lines → `NavBody[]`
  carrying **`row_index`** (absolute on-screen position — drives `UI_Down` walks),
  canonical `name` == journal `Scan.BodyName`, `designator`, `raw`. Keeps current-system Ls
  rows, drops Ly/other-system rows via `_system_prefix_match` with a **space-boundary** guard
  (real-frame hardening, `steps.py`/commit 4d73423: stops `...B47-10` reading as body "0" of
  `...B47-1`). Tested against two pinned 1920×1080 frames.
- **SELECT** (`next_unexplored`, pure, BUILT + tested): first `NavBody` whose name is not in
  the journal scanned-set. (This is the *explore* selector, not a *target-the-star* selector —
  see gap below.)
- **READ** (`read_nav_panel_lines` / `NavPanelReader.read`, **STUB-QUALITY / CALIBRATION-
  PENDING**): lazy cv2 + **pytesseract** (`navpanel_reader.py:205-243`). This is the
  **pytesseract engine the ratified design explicitly demotes** in favour of WinRT
  ([[ed-navpanel-ocr-first-parser]]: "cheap WinRT over +300MB EasyOCR"; pytesseract on the HUD
  font is "hit-or-miss", [[ed-navpanel-cv-prior-art]] §5). Region `DEFAULT_NAV_REGION =
  (505,435,410,330)` is a measured *estimate*, not validated on a planet-rich frame. Fails
  open (read failure → tour ends), never tested live.

### The config gate — OFF
`projects/ed-core/src/ed_core/config.py:90` `nav_panel_ocr_enabled: bool = False`;
`:91` `nav_panel_region = (505,435,410,330)`. While False, `build_navpanel_vision` returns
`(None, None)` (`capture.py:326-327`), so the reader/grabber are **never constructed**.

### The reader/grabber plumbing — BUILT (but currently inert because the gate is OFF)
- `cli.py:415-431`: under `--engage-keys`, `build_navpanel_vision(cfg)` →
  `(nav_panel_reader, nav_panel_grabber)`; prints `[CALIBRATION-PENDING]`.
- `capture.py:313-338` `build_navpanel_vision`: constructs `NavPanelReader(region=...)` + a
  `ScreenGrabber(region).grab`; degrades to `(None, None)` on any failure, never raises.
- `cli.py:591-592`: passed into the FlowRunner → `StepContext.nav_panel_reader` /
  `.nav_panel_grabber` (`ed_core/flow/context.py:177-181`).

### Consumers today — explore/body_tour only; TARGET does **not** use the reader
- `step_explore` (`ed-explore/src/ed_explore/steps_explore.py`) uses it fully: `_s1_read_select`
  grabs a frame → `reader.parse(frame, system)` → `next_unexplored` (`:166-169`); `_s3_engage`
  drives the cursor to the picked body via `engage_supercruise_assist_row(row=target.row_index)`
  (`:242-249`). This is the **working precedent** for CV-driven selection.
- `step_body_tour` (`steps_body_tour.py:35-40,104`) uses the same `parse → next_unexplored` +
  `row_index` walk; falls back to the blind walk when reader/grabber are None.
- **`step_nav_panel_target` (the TARGET flow) ignores `nav_panel_reader` entirely** — it only
  reads `compass_reader`/`frame_grabber` (`steps.py:437,471`). So even with the gate ON, the
  TARGET over-fire is untouched. **The CV-target work is new wiring, not a flip of the gate.**

---

## Proposed CV-driven nav-panel TARGET flow

Mirror the proven `step_explore` selection model (parse → identity-select → walk to
`row_index`), but the selector picks **THE route destination / arrival star**, not "next
unexplored". This is a design for operator critique — every contestable choice is also listed
under OPEN QUESTIONS.

### Prerequisite (hard dependency): rebuild parser-v2 (WinRT)
The READ layer must be the ratified WinRT whole-region-upscale OCR + journal-grounding +
OCR-word-bbox anchoring, **rebuilt** (parser-v2 is lost — see above), replacing the pytesseract
`read_nav_panel_lines`. The PARSE/SELECT layers (`parse_nav_panel_rows`, `next_unexplored`) are
already real-frame-tested and can stay. This is shared with D5/explore — **one parser serves
both TARGET and explore.** Until it exists + is calibrated, this flow cannot run; the blind
macro stays the fallback (see "What I am NOT proposing").

### Step-by-step (proposed `step_nav_panel_target_cv`, gated, with blind fallback)

1. **Fallback guard.** If `ctx.nav_panel_reader is None or ctx.nav_panel_grabber is None`
   → call the existing blind `_macro(0)` exactly as today (`steps.py:437-438`). CV never being
   wired must degrade to current behaviour, not break flight.

2. **Grab + parse.** `frame = ctx.nav_panel_grabber()`;
   `bodies = ctx.nav_panel_reader.parse(frame, ctx.current_system_supplier())`
   → `NavBody[]` with absolute `row_index` (same call shape as `_s1_read_select`,
   `steps_explore.py:166-168`). On read failure / empty list → log + **fail closed** to the
   blind fallback (do not guess a row).

3. **Identify THE target row** (the new selector — *not* `next_unexplored`):
   - **STATION destination:** the contacts/station case is a different tab (`request_docking`
     uses `CycleNextPanel` x2, `navpanel.py:289-292`). **OPEN QUESTION 4** — out of scope for
     the v1 star-target unless the operator wants it folded in.
   - **SYSTEM destination / arrival star (the hot path):** the target is the **system primary
     star**. Ground identity against the journal: the arrival `Scan` (auto-emitted on the
     hyperspace drop, [[ed-navpanel-ocr-first-parser]]) names the primary; match the parsed
     row whose `name` == that body, or the `NavRoute`/`Status.Destination.Name` when a route is
     plotted. The **selected (bright-orange) row's NAME does not OCR** ([[ed-navpanel-ocr-first-parser]]
     ROUND 2), so identity comes from the *non-selected* rows' clean OCR + journal grounding,
     not from reading the highlight. **OPEN QUESTION 2** covers the ambiguous-journal case.

4. **Drive the cursor to the known row index** (replaces the blind walk-and-verify):
   - Reuse `_target_pin_and_walk(... rows_down=target.row_index ...)` — pin to top, then walk
     **exactly `row_index`** `UI_Down` taps (the mechanism `step_explore` already trusts via
     `engage_supercruise_assist_row(row=...)`, `steps_explore.py:242-249`). No `max_rows`
     scan, no per-row re-lock: one counted walk to a CV-known index.
   - `UI_Select` x2 to lock (the `target_via_navpanel` tail, `navpanel.py:218-221`).

5. **Gate success by re-reading the panel, not the compass** (proposed): after the lock,
   grab + parse again and confirm the **selected/highlighted row's `row_index` == target index**
   (highlight localisation — bright-orange band, [[ed-navpanel-distance-reading]]), and/or that
   `Status.Destination.Name` now == the target body. This replaces the slow compass-dot +
   blind-row-advance loop (`steps.py:468-495`). **OPEN QUESTION 3** — exact success predicate
   (re-read highlight vs Status.Destination vs both) is unsettled.

6. **Eliminate the over-fire** two ways:
   - **No internal retry storm.** A counted walk to a CV-known index removes the
     `max_rows + max_toggles` (≤14) re-run loop entirely; at most one re-read + one corrective
     re-walk on a verify miss (bounded, e.g. 1 retry), then fail closed.
   - **Skip when already locked (de-dup the per-hop double-fire).** Before any keypress, if
     `Status.Destination.Name` already == the intended target (or the parsed selected row is
     already the target), **return True without opening the panel.** This is what kills the
     "fired 2-3×/hop" structural duplication (`arrival.toml` 1b+3, `startup` 0/2/14): the
     second/third call sees the lock already in place and no-ops. **OPEN QUESTION 1** — the
     operator must confirm this skip is desired vs the deliberate belt-and-suspenders re-fires.

7. **Fail closed.** Any read failure, empty parse, identity-ambiguous, or verify miss past the
   single retry → log loudly (mirror `NavPanelTargetUnverified`, `steps.py:507`) and either
   fall back to one blind `_macro(0)` (preserves today's behaviour) or return False per the
   call-site's `required`/`skip_to` contract (`arrival.toml:72` uses `skip_to`). **No silent
   no-op** (fail-closed discipline, [[ed-navpanel-ocr-first-parser]]).

### What this removes from the live path
- The held-4s pin × up-to-14 macros per invocation (`steps.py:464`, `navpanel.py:236`).
- The compass-dot + blind-row-advance verify loop (`steps.py:468-495`) — replaced by a single
  panel re-read.
- The per-hop 2-3× duplicate fires — replaced by the already-locked skip (step 6b).

---

## OPEN QUESTIONS for the operator (ordered most-blocking first)

1. **[BLOCKING — confirms the whole over-fire fix] Is "skip when already locked" wanted?**
   The duplicate fires (arrival 1b+3, startup 0/2/14) appear deliberate (best-effort re-lock
   comments, `arrival.toml:33-42`). The over-fire fix relies on the 2nd/3rd call no-opping when
   `Status.Destination` already names the target. Confirm that early-out is desired, or that
   some re-fires must stay (e.g. after scoop, `arrival.toml:30`).

2. **[BLOCKING — identity rule] When journal/Status is ambiguous, what marks a row as THE
   target?** The selected row's NAME does not OCR ([[ed-navpanel-ocr-first-parser]]); a
   fresh-unscanned system can lack a `Scan` to ground against (the Sol/Beta data gap,
   [[ed-navpanel-ocr-first-parser]] DATA GAP). For the arrival star: fall back to "topmost
   in-system Ls row" (distance-sorted ⇒ star is near top)? Or abstain → blind fallback? Pick the
   rule; do not let me guess.

3. **[BLOCKING — success gate] What is the success predicate?** Re-read the panel and confirm
   the highlighted `row_index` matches? Or trust `Status.Destination.Name` == target (Status.json
   ~1s write latency, `steps.py:487`)? Or both? And the highlight-localisation crop is not yet
   validated on a real frame.

4. **[HIGH — scope] Does v1 cover the STATION/contacts target too, or just the SYSTEM/arrival
   star?** The station path is the Contacts tab (`CycleNextPanel` x2, `navpanel.py:289-292`) —
   a separate parse/region. Proposal scopes v1 to the star; confirm or widen.

5. **[HIGH — SC-assist coupling] Does CV target make the early scoop-grab lock (`arrival.toml`
   step 1b, "early best-effort star lock") moot?** Or does SC-assist still need a lock in hand
   before the scoop window for timing reasons? This decides whether step 1b survives.

6. **[MEDIUM — D2b, post-SC-assist "already in front" short-circuit]** Separate from TARGET:
   the unnecessary post-SC-assist wait when the next target is already ahead
   ([[gatewalk-efficiency-targets]] §2, D2b). Should CV target identity also drive that
   short-circuit, or is it a separate change? (Flagged so it isn't lost — tracked nowhere else.)

7. **[MEDIUM — frames] Which real frames are still needed?** The blocker for *any* of this:
   - A NAVIGATION-tab frame of a **populated/unexplored system** (bodies listed, the box-in-
     hollow-box markers) to calibrate region + row pitch + highlight crop (OPERATOR_TODO item 4
     — "THE last blocker").
   - A frame with a **route plotted to a system** (jumps indicator) to validate the
     route-destination identity path.
   - Confirmation of the highlighted-row crop on a real frame for the step-5 success gate.

8. **[LOW — engine] Confirm the parser-v2 rebuild uses WinRT (ratified) over the current
   pytesseract READ layer.** The ratified decision is WinRT ([[ed-navpanel-ocr-first-parser]]);
   the on-disk code is pytesseract. Confirm the rebuild target (and note: parser-v2 is **lost
   work** — D5/FRONT-7 "harvest from worktree" is no longer possible; it must be rebuilt from
   the design in [[ed-navpanel-ocr-first-parser]]).

---

## What I am NOT proposing

- **NOT tuning the blind macro.** No de-dup / `pin_hold_s` / `max_rows` / `max_toggles`
  changes — the audit and the operator both call those the wrong frame
  ([[ed-navpanel-target-replace-with-cv]]). The macro is to be replaced by CV, full stop.
- **NOT removing the blind macro now.** It stays the **live fallback** (step 1 above + the
  `ctx.nav_panel_reader is None` guard) until the CV path is built, calibrated, operator-
  reviewed, and proven on a live flight — so flight is never broken by an unfinished CV path
  (fail-closed discipline; [[nothing-stays-unwired]] satisfied by shipping CV *wired with the
  blind fallback intact*).
- **NOT implementing anything in this pass.** No code, no `.toml`, no config change, no gate
  flip. This is a reviewable design only.
- **NOT flipping `nav_panel_ocr_enabled` on its own.** That alone does nothing for TARGET
  (TARGET ignores the reader today) and would expose the uncalibrated pytesseract READ layer to
  explore/body_tour. The gate flip is downstream of the parser-v2 rebuild + calibration frame.
