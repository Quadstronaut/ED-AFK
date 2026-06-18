# COUNCIL C1 — CV ACTION FAMILY (ED-AFK flight-flow redesign, 2026-06-17)

You are a council-v2 instance. **This brief is SELF-CONTAINED and AUTHORITATIVE.** Tier: arch.

## Binding standing rules
- **DESIGN-ONLY.** Produce a ratified design + a Operator-blocker list. *(STATUS 2026-06-18: the no-build
  clause is LIFTED per the MASTER-SPEC standing rules — building is authorized for ratified scenes. This
  bullet records the design-only round this council ran; NO-GUESSING + fail-closed below still bind.)*
- **NO GUESSING.** Any unknown game-mechanic / bind / journal field / screen layout not settled in the
  cited sources → flag `BLOCKED-ON-KYLE: <question>` in the design + ledger. Read repo code/docs AND
  community ED references (journal & Status.json schema, nav-panel behaviour) before asserting a mechanic.
- Ship-safety: every new action fails closed; nothing drives blind on an unread frame.
- Per-ship CV regions (#19): region comes from `navpanel_reader.resolve_nav_region`, not a constant
  (bot is single-ship Mandalay).

## Shared context — read in full FIRST
- `docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md` (operator intent + SETTLED GAME-TRUTHS).
- Settled rules (also inlined in the master spec): memories `ed-navpanel-target-cv-rules`,
  `ed-cv-regions-are-per-ship`, `ed-navpanel-ocr-first-parser`.

## YOUR SCOPE — design these, nothing else
1. **Details-page button-bar CV-navigation primitive** (shared substrate): open a nav-panel ROW's DETAIL
   page, OCR the LABEL above the highlighted button (`LOCK DESTINATION` / `UNLOCK DESTINATION`,
   `ACTIVATE SUPERCRUISE ASSIST` / `DEACTIVATE SUPERCRUISE ASSIST`), move the row-submenu cursor to the
   desired button, press it ONCE.
2. **`nav_target_star`** — ensure the main star is the LOCKED destination. Idempotent **single** select.
   Detect already-locked-on-star via the `UNLOCK DESTINATION` label (a locked row shows UNLOCK). If
   already locked → no-op done; else press lock ONCE. Verify via `Status.Destination` == system. NOT ×2.
3. **`nav_supercruise_star`** & 4. **`nav_supercruise_target`** — open the star / station row detail page
   and press the **SUPERCRUISE ASSIST** button ONCE (not the lock button).
5. **`nav_supercruise_unexplored`** — LOOP: SC-assist the next `UNEXPLORED` row; terminate when the
   star/system glyph (`✦`) appears at the row selector (list bottom). Track by row selector.

## Ground in (read before designing)
- `projects/ed-core/src/ed_core/executor/navpanel.py` (current macros: target_via_navpanel,
  engage_supercruise_assist, _target_pin_and_walk, CycleNextPanel for contacts).
- `projects/ed-vision/src/ed_vision/ocr_winrt.py` + `navpanel_reader.py` (the live READ layer; word bboxes).
- Bundled binds + `ed_core/binds_validate.py` (confirm UI_Select/UI_Right/UI_Left/FocusLeftPanel etc.).
- Pinned frames `projects/ed-autojump/tests/fixtures/navpanel/*.png` (populated + unexplored).
- The operator's detail-page frames are described in the master spec (image #6 = supercruise button
  highlighted/active; image #7 = UNLOCK DESTINATION). **More detail-page frames (LOCK state + exact button
  positions) are likely needed → flag as BLOCKED-ON-KYLE.**

## Deliverable
A ratified DESIGN DOC (modules, action contracts, CV crops + region source, exact key sequences,
fail-closed behaviour, verification predicate) as your candidate output, + a consolidated
`BLOCKED-ON-KYLE:` list. The other councils (C3–C7) consume these action contracts. Do NOT modify flight code.
