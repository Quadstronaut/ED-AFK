# IN-GAME CAPTURE / TEST SHEET — one session, start to finish (2026-06-21)

> ✅ **STATUS 2026-06-21 — SESSION DONE except #8.** All C1 detail frames (#1–#4) + the bonus station-assist
> frame + #7 (km + REQUEST DOCKING) are CAPTURED and committed. **#5 (D1) and #6 (unexplored) are RESOLVED**
> (`D1-DESTINATION-DISCRIMINATOR-FINDING.md`, `2026-06-21-COUNCIL-INCONSISTENCY-REGISTER.md`). Only **#8**
> (planet purple-vector + no-vector smack) remains, deferred by the operator. CORRECTION: the SC-assist OFF
> label is `SUPERCRUISE ASSIST AND ORBIT` (body) / plain `SUPERCRUISE ASSIST` (station), **NOT** `ACTIVATE
> SUPERCRUISE ASSIST`; and a locked STAR is `Body!=0` too (D1 rule = `Body!=0 AND Name!=system`).

Everything the flow-redesign is currently blocked on, in **one runnable ED session**, ordered by
game state so you fly a single natural loop. Sources: the C1 keystone checklist
(`C1-DETAIL-PAGE-FRAME-CAPTURE.md`) + the locked flow-redesign blockers
(resume-state-2026-06-18) + the smack G2 negative cases.

Chair-side buildable work is exhausted — **nothing more compiles into the live CV loop until these land.**

---

## SETUP — do once before starting

| Setting | Value | Why |
|---|---|---|
| **Ship** | **Mandalay** | CV regions are per-ship; a frame from any other hull is useless (gap #19). |
| **Resolution** | **1920×1080**, same window mode you normally run | Matches every existing `*_1080.png` fixture. |
| **HUD scale / colours** | Same as your existing fixtures | Crops are calibrated to them. |
| **Capture** | **Full-screen PNG, not a crop** | Designs need absolute button/row geometry. |

**Save locations** (already exist in the repo):
- Nav-panel frames → `projects/ed-autojump/tests/fixtures/navpanel/`
- Smack / escape-vector frames → `projects/ed-autojump/tests/fixtures/compass/`

---

## THE 8 ARTIFACTS AT A GLANCE

| # | Artifact | Type | Unblocks | Filename |
|---|---|---|---|---|
| 1 | Detail pane — **LOCK DESTINATION** | frame | C1 (→C3/C4/C6/C7) | `navpanel_detail_lock_1080.png` |
| 2 | Detail pane — **UNLOCK DESTINATION** | frame | C1 | `navpanel_detail_unlock_1080.png` |
| 3 | Detail pane — **SUPERCRUISE ASSIST AND ORBIT** / plain **SUPERCRUISE ASSIST** (station) — ✅CAPTURED | frame | C1 | `navpanel_detail_sc_activate_1080.png` |
| 4 | Detail pane — **DEACTIVATE SUPERCRUISE ASSIST** | frame | C1 | `navpanel_detail_sc_deactivate_1080.png` |
| 5 | **Plot-to-station** Status.json read | test/data | C2 system-vs-station branch (D1) | (paste values / ping me) |
| 6 | **SC-assist on unexplored** — drop vs orbit | test/answer | C6 exploration | (answer in words) |
| 7 | **Station km distance + REQUEST DOCKING** ✅CAPTURED | frame | C7 docking gate | `navpanel_nav_station_km_1080.png` + `navpanel_contacts_request_docking_1080.png` |
| 8 | **Planet-smack purple vector** + **no-vector drop** | frames | G2 smack discriminator | `smack_planet_purple_vector_1080.png`, `smack_no_vector_drop_1080.png` |

The four C1 frames (#1–4) are **the keystone** — capture those first; everything CV in the redesign rides on them.

---

## PHASE 1 — Nav panel, any time (docked is fine) → frames #1 & #2

Detail-pane lock states. No supercruise needed.

1. Open the left nav panel (`1` / FocusLeftPanel).
2. Highlight a row that is **NOT** your current locked target (`UI_Up`/`UI_Down`).
3. Press `UI_Select` (space) → detail pane slides open; cursor lands on the first control.
   - First button reads **LOCK DESTINATION** → **📸 frame #1** → `navpanel_detail_lock_1080.png`
   - ⚠️ Do **NOT** press `UI_Select` again — that activates the button. Just screenshot.
4. Now actually lock a destination, then open **that** row's detail pane.
   - First button reads **UNLOCK DESTINATION** → **📸 frame #2** → `navpanel_detail_unlock_1080.png`

**Flag if:** the button bar is **vertical** (you move with `UI_Up`/`UI_Down`, not `UI_Right`) — the C1 design
assumes a horizontal `UI_Right` walk; a wrong axis means a rework. A bonus shot with the cursor on **each**
button (so the order + `UI_Right` step-count between Lock and Supercruise-Assist is unambiguous) is gold.

---

## PHASE 2 — Plot a station route, then undock → test #5 (D1)

The whole C2 system-vs-station branch hinges on this one discriminator.

1. From the station, open the galaxy/system map and **plot a route whose destination is a STATION**
   (orbital / outpost / settlement) — not just a system.
2. With that route set, the live `Status.json` `Destination` block is what I need:
   `…\Saved Games\Frontier Developments\Elite Dangerous\Status.json`
3. **What confirms it (RESOLVED 2026-06-21 — `D1-DESTINATION-DISCRIMINATOR-FINDING.md`):** station iff
   `Destination.Body != 0` **AND** `Destination.Name != currentSystemName`. A locked STAR is ALSO `Body!=0`
   (Name==system); `Body==0` = a whole-system / next-hop target. The bare "star = `Body==0`" claim is REFUTED.

**Delivery (easiest wins):** either paste the `Destination` object here, **or** just ping me while the
station route is set and I'll read `Status.json` myself.

---

## PHASE 3 — In supercruise → frames #3 & #4 + test #6

The SC-assist button only exists in supercruise, so do these on your way out.

**Frames #3 / #4 — SC-assist button states:**
1. In SC, open a body's detail pane (`1` → highlight → space).
2. `UI_Right` across the bar to the supercruise-assist button.
   - Assist **OFF** → reads **SUPERCRUISE ASSIST AND ORBIT** (body) / plain **SUPERCRUISE ASSIST** (station), NOT `ACTIVATE` → **📸 frame #3** (full bar, CAPTURED) →
     `navpanel_detail_sc_activate_1080.png`
   - Engage assist, reopen the same button, assist **ON** → reads **DEACTIVATE SUPERCRUISE ASSIST**
     → **📸 frame #4** (full bar) → `navpanel_detail_sc_deactivate_1080.png`
   - Screenshot the **full button bar** both times (order + position matter).

**Test #6 — SC-assist on an UNEXPLORED row (the strand-risk test):**
1. In SC, target an **unexplored** planet/moon (the small box-inside-a-hollow-box marker — not yet scanned).
2. Engage SC-assist on it.
3. **Watch and report one thing:** does the ship **DROP to normal space** at the body, or **ORBIT it in SC
   and hold**?
   - If it **drops**, C6 needs a re-engage branch (strand risk). If it **orbits**, the simple path is safe.

---

## PHASE 4 — Approaching a station → frame #7

1. On approach to a station (close enough that distance shows in **km/Mm**, not Ls), open the nav panel →
   page right to the **Contacts** tab (`E`, `E`).
2. Screenshot the station row showing its **km distance** → **📸 frame #7** →
   `navpanel_contacts_station_km_1080.png`

Why: the distance parser only reads `Ls`/`Ly` today; the `< 7.5 km` docking-proximity gate needs a km
parser calibrated to a real frame.

---

## PHASE 5 — Smack discriminator negatives → frame #8 (×2)

We already have the **blue star-smack** escape vector (`tests/fixtures/compass/escape_vector_smack_*.png`).
Missing are the two negatives that let the bot tell a smack from a deliberate drop:

1. **Planet-smack (PURPLE vector):** deliberately get smacked by a **planet** (fly into its exclusion zone
   / drop inside it in SC) → screenshot the **purple** escape vector on the HUD/compass →
   `smack_planet_purple_vector_1080.png`
2. **No-vector deliberate drop:** deliberately drop out of SC at a body the normal way (throttle into the
   blue deceleration zone) → screenshot showing **NO escape vector** (clean drop) →
   `smack_no_vector_drop_1080.png`

**Confirm the model while you're at it:** vector present = smacked; **blue = star, purple = planet**; no
vector = deliberate drop. Anything else that visibly distinguishes a smack from a deliberate drop?

---

## WHEN YOU'RE DONE

Drop the frames into the two fixtures dirs (or just hand them to me and I'll re-home them), paste/ping the
Status.json values for #5, and tell me the drop-vs-orbit answer for #6. That unblocks, in order: **C1 →
C3/C4/C6/C7 build → the full CV loop becomes flight-testable.** I'll take it from there.
