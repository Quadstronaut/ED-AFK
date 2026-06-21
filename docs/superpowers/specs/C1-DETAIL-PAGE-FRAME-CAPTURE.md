# C1 KEYSTONE — detail-page button-bar frame capture (for Operator, in-game)

> ✅ **CAPTURED 2026-06-21 — keystone unblocked.** All 4 detail frames committed:
> `navpanel_detail_{lock,unlock,sc_activate,sc_deactivate}_1080.png` (+ bonus `..._sc_assist_station`). Bar is
> HORIZONTAL (UI_Right walk holds). CORRECTION: SC-assist OFF reads `SUPERCRUISE ASSIST AND ORBIT` (body) /
> plain `SUPERCRUISE ASSIST` (station), NOT `ACTIVATE SUPERCRUISE ASSIST`.

**This is the single keystone unblock.** C1 (`nav_target_star` / `nav_supercruise_star` /
`nav_supercruise_target` / `nav_supercruise_unexplored`) and everything that builds on it
(C3 arrival, C4 smack, C6 exploration, C7 docking) are blocked on ONE missing fixture:
a nav-panel row's DETAIL pane showing the button bar, so the CV read layer can be
calibrated to the button-label crop region + button order. No such frame exists in the
repo — every fixture in `tests/fixtures/navpanel/` is a list/full view. Nothing CV in the
redesign builds until these land.

## Capture settings
- **Resolution 1920×1080** — matches the existing `*_1080.png` fixtures. Same window
  mode you normally run.
- **Same ship (Mandalay)** — CV regions are per-ship; a frame from another hull is useless.
- **Full-screen capture, not a crop** — the design needs the absolute button geometry and
  the bar order, sourced later via `resolve_nav_region`.
- Use the same capture tool/HUD scale/colours as the existing fixtures.

## How to open the detail pane (grounded in `executor/navpanel.py`)
1. Open the left nav panel (`FocusLeftPanel`, the `1` key).
2. Highlight a row (`UI_Up` / `UI_Down`).
3. Press `UI_Select` (space) → the row's DETAIL pane slides open to the right of the body
   name; the cursor lands on the FIRST control (Lock Destination).
4. `UI_Right` walks across the button bar (Lock Destination → Supercruise Assist → …).
5. **Do NOT press `UI_Select` again** — that activates the button. Just screenshot.

## The 4 frames needed (all full 1080)

1. **LOCK state — `LOCK DESTINATION`**
   - Pick a row that is NOT your current locked target.
   - Open its detail pane. First button reads **LOCK DESTINATION**.
   - Save → `navpanel_detail_lock_1080.png`

2. **UNLOCK state — `UNLOCK DESTINATION`**
   - Lock a destination first, then open THAT row's detail pane.
   - First button reads **UNLOCK DESTINATION**.
   - Save → `navpanel_detail_unlock_1080.png`

3. **SC-assist OFF — `SUPERCRUISE ASSIST AND ORBIT` (body) / plain `SUPERCRUISE ASSIST` (station)**
   - Must be **in supercruise** (the SC-assist button only exists in SC).
   - Open a body's detail pane, `UI_Right` to the supercruise-assist button (assist OFF).
   - Reads **SUPERCRUISE ASSIST AND ORBIT** (orbitable body) or plain **SUPERCRUISE ASSIST** (station). Screenshot the FULL bar.
   - Save → `navpanel_detail_sc_activate_1080.png`

4. **SC-assist ON — `DEACTIVATE SUPERCRUISE ASSIST`**
   - In supercruise with SC-assist already engaged, open the same button.
   - Reads **DEACTIVATE SUPERCRUISE ASSIST**. Screenshot the full bar.
   - Save → `navpanel_detail_sc_deactivate_1080.png`

## Save to
`projects/ed-autojump/tests/fixtures/navpanel/`

## Nice-to-have (only if easy)
- One shot of the full bar with the cursor on EACH button, so the button ORDER and the
  `UI_Right` step-count between Lock and Supercruise-Assist are unambiguous.
- **If the button bar is VERTICAL** (you move between buttons with `UI_Up`/`UI_Down`, not
  `UI_Right`), say so — the current C1 design assumes a horizontal `UI_Right` walk (DESIGN
  risk B2). A wrong axis means the cursor-walk model needs rework.

## Then
Once these 4 land, C1 unblocks: calibrate the button-bar crop + order → ratify C1 →
C3/C4/C6/C7 build on it → full CV loop becomes flight-testable.
