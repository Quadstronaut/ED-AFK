# BC2 — Galaxy-Map Programmatic Access: Teaching-Session Plan

**Status:** PLAN (agenda for an interactive session Operator drives). Authored 2026-06-08.
**Why it exists:** BC3 (secondary fuel-system) needs to *re-route* via the galaxy map,
and future map-driven features will too. Before I can build that, I need to learn — from
Operator, live — how ED's galaxy map responds to programmatic input. This is the session that
captures that knowledge and turns it into reusable primitives.

> Operator's framing (BC3 message, 2026-06-08): "you'll open galmap with `*` (above the
> 10-keypad, not Shift+8), I'll give you the coordinates to click, paste our destination
> name, click a new coordinate, click another new coordinate, `*` … at only expedient
> human speeds, the game UI sucks."

---

## 1. The unknowns this session resolves

The bot today drives **keyboard only** (DirectInput scancodes via the `ED-AFK.4.2.binds`
preset). The galaxy map is **mouse + text** driven. So the gaps are:

1. **Map open/close** — confirm the bind. Operator says numpad `*` (Multiply), *not* Shift+8.
   Is `GalaxyMap` bound in the ED-AFK preset? What scancode does numpad-`*` deliver?
2. **OS mouse control** — the bot has **no mouse-move/click primitive today**. The
   widget-ring fine-align *reads* a HUD reticle via vision; it does not move the cursor.
   Galmap clicking needs synthetic **SendInput mouse** (absolute move + left click). This
   is the main thing to build.
3. **Text entry** — paste a system name into the map's search box. Clipboard paste
   (set clipboard → focus search → `Ctrl+V`) vs. per-char synthetic typing. Operator says
   "paste," so clipboard-first.
4. **The click coordinate set** — at Operator's resolution/UI-scale, where are the search
   box, the search result, and the plot control on screen? These are resolution-specific
   (like the `[vision].region`) and must be captured + stored, not hard-coded blindly.
5. **State confirmation without arbitrary waits** — how do we *know* the map opened, the
   search resolved, and the route plotted? (See §4 — `GuiFocus` + `NavRoute`.)

## 2. Session format

Operator drives ED's galaxy map by hand at human speed; I observe and instrument. No bot
keypresses to the game during capture — I only record.

- **I run** a capture harness that, on each of Operator's "mark" cues, grabs: a full-screen
  PNG, the current `Status.json` (esp. `GuiFocus`), and the tail of the journal +
  `NavRoute.json`.
- **Operator narrates** each action ("opening map now", "clicking the search box here",
  "pasting the name", "clicking the result", "clicking PLOT ROUTE", "closing map").
- We do **one full plot, start to finish**, twice (once to capture, once to verify the
  captured coordinates + sequence reproduce it).

## 3. Data to capture (the session's output)

| Capture | Purpose |
|---|---|
| Screen resolution + UI scale | every coordinate is relative to these |
| PNG at each step | locate click targets (search box, result row, PLOT control) |
| Pixel coords of each click target | the coordinate set BC3 will feed back at runtime |
| `GuiFocus` value when map is open | the open/close gate (expected `6` = GalaxyMap) |
| Key/mouse sequence, in order | the exact macro to replay |
| Journal + `NavRoute.json` deltas per step | which steps emit confirmable events |
| Settle behaviour (how long the UI lags) | so gates wait on *state*, never a fixed clock |

## 4. Gating — no arbitrary timed waits (house rule)

The map flow MUST gate on observable state, not `sleep()`:

- **Map open/closed** → `Status.json` `GuiFocus`. ED publishes the focused UI panel here;
  `GuiFocus == 6` is the Galaxy Map (`7` = System Map). So `galmap_open()` presses the
  bind and **waits until `GuiFocus == 6`**; `galmap_close()` waits until it leaves `6`.
  *(Confirm the exact value live — this is the single most important capture.)*
- **Route plotted** → a **`NavRoute` journal event fires and `NavRoute.json` rewrites**
  the moment a plot succeeds. So "plot confirmed" gates on a NavRoute change, never a
  timer. (This is the same signal the jump loop already consumes.)
- **Search-result appeared** → likely no journal signal; gate on a **vision check** of the
  result region (capture tells us if there's a reliable pixel cue) or fall back to the
  human-in-the-loop confirm (Operator clicks; I verify the eventual NavRoute).

## 5. Primitives to build (deliverables — what BC3 depends on)

Enumerated so BC3's plan can name them as hard dependencies:

1. `mouse_move_abs(x, y)` + `mouse_click(button="left")` — SendInput mouse (NEW capability).
2. `set_clipboard(text)` + `paste()` — clipboard set + `Ctrl+V` (or ED's paste bind).
3. `galmap_open()` / `galmap_close()` — press the `*` bind, gate on `GuiFocus`.
4. `galmap_search_and_plot(system_name, coords)` — focus search → paste name → select
   result → click PLOT → **confirm via NavRoute** → close map. `coords` is the captured,
   resolution-specific coordinate set (stored in config, like `[vision].region`).
5. **Human-in-the-loop coordinate channel** — a way for Operator to hand the bot the click
   coordinates *at runtime* (BC3's lethal-fuel detour), and for the bot to wait on Operator's
   input (gate on the input arriving, not a clock).

## 6. Open questions for Operator (bring answers to the session)

- Is `GalaxyMap` already bound to numpad-`*` in the ED-AFK preset, or do we add it?
- Do you want clicks (resolution-fragile, fast) or keyboard UI-nav of the map
  (resolution-robust, slower)? Your BC3 description says clicks — confirming that's the
  design and accepting the per-resolution coordinate capture it requires.
- Does the search box accept a clipboard `Ctrl+V`, or must we synth-type the name?
- After PLOT, does the map stay open (needing an explicit close `*`) or auto-close?

## 7. Acceptance criteria

The session succeeds when I can, unattended: open the map (gated on `GuiFocus`), paste a
named system Operator supplies, plot to it, and **confirm the new route via a `NavRoute`
event** — then close the map — reproducibly, on Operator's resolution, with every wait gated
on state. That set of primitives is the BC2 deliverable BC3 consumes.
