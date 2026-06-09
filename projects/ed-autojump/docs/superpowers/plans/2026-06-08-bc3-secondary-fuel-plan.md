# Big Change 3 â€” Secondary (Emergency) Fuel System: Synthesis Plan

**Status:** PLAN ONLY. No code edits. Built interactively with Operator when the galaxy-map step is reached.
**Synthesis seat (opus).** Merges the three design seats after a fresh re-read of the live code. Every code claim below was verified in this session against the cited file+line.

---

## 0. Code-verified ground truth (the corrections that shaped this plan)

| Claim | Verdict (verified this session) |
|---|---|
| `Loadout.unladen_mass` exists in the parsed model | TRUE â€” `journal/events.py:47` (`alias="UnladenMass"`). Seat-3's worry that it may be absent from the model is WRONG. The real gap: **FlowRunner never STORES it** â€” `_apply_state`'s Loadout branch (dispatcher.py:831-840) reads only `fuel_capacity.main` + the scoop item. |
| `FSDJump` carries `fuel_used` / `fuel_level` / `star_pos` / `jump_dist` | TRUE â€” `events.py:84-86, 80`. FlowRunner stores NONE of them today (no `_last_fuel_used`, no last-`StarPos`). |
| `NavRouteWaypoint` carries `star_pos` + `star_class` | TRUE â€” `status/navroute.py:21-22`. Distance between consecutive `star_pos` is computable. |
| `is_scoopable` = KGBFOAM set O B A F G K M | TRUE â€” `fsd/danger.py:16,54`. |
| `fsd/math.py` has `fuel_cost`, `max_jump_range`, `fsd_spec_from_loadout`, `FsdSpec.max_fuel_per_jump` | TRUE â€” `math.py:100,110,92,33`. None are called from FlowRunner today. |
| `_final_waypoint` + `_resolve_final_waypoint()` (durable NavRoute.json fallback) | TRUE â€” `dispatcher.py:214, 521-546`. Reusable to hold the destination. |
| `step_scoop_refuel` reusable, default `refuel_below=0.70`, skip-gates on `is_scoopable(arrival_star_class)` | TRUE â€” `steps.py:1175-1331`. |
| Sender is **keyboard-only** (pydirectinput â†’ SendInput KEYEVENTF_SCANCODE) | TRUE â€” `keys/sender.py:4`. **No mouse primitive exists anywhere in `src/`** (grep clean). |
| `Key_Numpad_Multiply = 0x37` exists | TRUE â€” `keys/scancodes.py:55,60`. But **`GalaxyMapOpen` is NOT in `REQUIRED_ACTIONS`** (binds_validate.py:43-72) and no galmap action is pressed anywhere. |
| The established "human calibrates UI once, bot replays" pattern (`menu_nav`/`calibrate_menu`) is **keyboard press-COUNTS + arrow DIRECTIONS** â€” NOT mouse coordinates | TRUE â€” `launcher/wizard.py:201-313`, `cli.py:666-705`. This is decisive for Â§5 (see the reconciliation). |
| `Status` exposes `low_fuel` (bit 19), `fuel.fuel_main`, `gui_focus`, `destination` | TRUE â€” `status/status.py:46,85,110,115,163`. **No GuiFocus value for the galaxy map is defined** anywhere â†’ genuine open question. |
| A whole alternate flow that FORGETS the route must be runner-dispatched, not a step inside arrival | TRUE â€” interpreter `skip_to` is forward-only within one procedure (interpreter.py:99-118); only `FlowRunner.dispatch*` swaps procedures. Mirrors `dispatch_route_complete` (dispatcher.py:576). |

**Net:** the feature is mostly wiring of primitives that already exist. The genuinely new external capability is galaxy-map manipulation (BC2). The three seats converged on architecture; they diverged on (a) candidate-selection surface and (b) whether the galaxy map needs a mouse. Both are resolved below.

---

## 1. TRIGGER DETECTION â€” "no fuel star this jump or next, AND need fuel by next jump"

### 1.1 Where it runs
A **dispatcher-side gate**, evaluated on each live `FSDJump` in `dispatch()` (dispatcher.py:479), checked AFTER the route-complete check and BEFORE `_run("arrival")`. New method `_needs_secondary_refuel() -> bool`; when True, `dispatch()` calls a new `dispatch_secondary_refuel(ev)` instead of `_run("arrival")`. This placement is unanimous across all three seats once you account for the forget-the-route requirement: the detour is a whole alternate flow, and arrival's `retry_from` machinery must never touch it. (Trigger boundary = post-jump, in the arrival scene â€” see Open Q "trigger boundary".)

### 1.2 Sub-predicate A â€” "no scoopable star this jump or next" (RELIABLE)
Read from the durable `NavRoute.json` via `_navroute_state()` â†’ `NavRoute.route` (list of `NavRouteWaypoint`).
- `route[0]` is the ORIGIN (the system we are sitting in â€” documented convention in `step_target_next_route`, steps.py:78, and navpanel.py). So **this jump = `route[1]`, next jump = `route[2]`**.
- `next_scoopable  = is_scoopable(route[1].star_class)`
- `after_scoopable = len(route) >= 3 and is_scoopable(route[2].star_class)`
- `no_scoop_soon   = not next_scoopable and not after_scoopable`
- Edges: `len(route) <= 1` â†’ at/near route end â†’ NOT the refuel path (fold into route-complete). `route` empty/None â†’ indeterminate â†’ do NOT divert.

### 1.3 Sub-predicate B â€” "need fuel by next jump" (math path preferred, proxy fallback)
Two layers, tried in order, fail-soft to the next:
1. **Predicted (preferred):** `cost = fuel_cost(spec, ship_mass, dist)` where
   - `spec = fsd_spec_from_loadout(loadout)` â€” requires a NEW FlowRunner field `_fsd_spec` populated in `_apply_state`'s Loadout branch.
   - `ship_mass = unladen_mass + fuel_main + cargo` â€” requires storing `_unladen_mass` (NEW field; the value IS in the Loadout model already) and reading `Status.cargo` (already on the model, status.py:112) + `Status.fuel.fuel_main`.
   - `dist` = `â€–route[2].star_pos âˆ’ route[1].star_posâ€–` for the after-next hop, `â€–route[1].star_pos âˆ’ route[0].star_posâ€–` for the next hop (exact vector math on data we hold).
2. **Observed proxy (fallback if spec/mass unavailable):** the last `FSDJump.fuel_used` â€” requires a NEW `_last_fuel_used` field set in `_apply_state`'s FSDJump branch.
3. **Most pessimistic floor:** `spec.max_fuel_per_jump` (the per-jump hard cap).
- **Current fuel** = `Status.fuel.fuel_main` (live, authoritative). Tank capacity already cached as `_ship_fuel.capacity_t`.
- **Rule:** `need_fuel = fuel_main - cost(route[1]) - cost(route[2]) < safety_margin`. `safety_margin` is operator-tunable; default candidate "one full max-cost jump in reserve" (`max_fuel_per_jump`), pending Operator's number. **`Status.low_fuel` (bit 19) is a HARD-OVERRIDE backstop** â€” if set, force `need_fuel = True` regardless of the math (it fires at 25% tank; ED strands you below ~1 jump, so it is a floor, never the primary trigger).

### 1.4 Combined decision rule
```
divert  iff  len(route) >= 3                 # at least this-jump + next-jump onward
        AND  no_scoop_soon                   # neither route[1] nor route[2] is KGBFOAM
        AND  (need_fuel OR status.low_fuel)  # can't safely make 2 hops / game says low
        AND  scoop_equipped                  # _ship_fuel.scoop_max_rate_t_s is not None
        AND  (a reachable refuel-primary exists â€” checked lazily in Â§3, fail-closed)
```
Any indeterminate input (no NavRoute / no spec / no Status fuel) â†’ **do NOT divert** â†’ falls through to today's arrival scoop + danger filter. This matches every existing gate's fail-closed posture (`_is_route_complete`, the scoop skip-gates).

### 1.5 Reliability table
| Signal | Source | Reliability |
|---|---|---|
| upcoming StarClass per hop | `NavRoute.route[].star_class` | RELIABLE |
| scoopable classification | `is_scoopable` | RELIABLE |
| current main fuel | `Status.fuel.fuel_main` | RELIABLE |
| game low-fuel flag | `Status.low_fuel` (bit 19) | RELIABLE |
| tank capacity | `_ship_fuel.capacity_t` (Loadout) | RELIABLE |
| last actual jump cost | `FSDJump.fuel_used` | RELIABLE (once stored) |
| inter-hop distance | `â€–StarPos[i+1]âˆ’StarPos[i]â€–` | RELIABLE (vector math) |
| **predicted** next-hop cost | `fuel_cost` + spec + ship_mass | **NEEDS LIVE TEST** (mass/cargo drift; verify model vs actual `fuel_used`) |
| safety margin value | operator constant | **NEEDS OPERATOR INPUT** |

### 1.6 New FlowRunner state required (all populated in `_apply_state`, backlog + live)
`_last_fuel_used: Optional[float]` (FSDJump), `_fsd_spec: Optional[FsdSpec]` (Loadout), `_unladen_mass: Optional[float]` (Loadout). All fail-soft to None â†’ trigger degrades to the proxy/floor or declines to divert.

---

## 2. HOLD-ORIGINAL-DESTINATION

Reuse the existing `_final_waypoint` machinery, but **snapshot into a dedicated divert-scoped field before any route manipulation**, because the detour's re-plot fires a fresh `NavRoute` event that `_apply_state` (dispatcher.py:760-775) would otherwise use to overwrite `_final_waypoint` with the REFUEL star.

- New field `_held_destination: Optional[tuple[int, str, list[float]]]` = `(system_address, name, star_pos)`. The **name** is mandatory (the galaxy-map re-plot pastes/types the destination name); `star_pos` is a coordinate fallback for duplicate-name disambiguation. Capture all three from `_resolve_final_waypoint()` (durable, survives rotation) plus the final NavRoute waypoint's `star_pos`, **at the moment the divert is decided**.
- New flag `_detour_active: bool`. While set: guard the `_apply_state` NavRoute branch so incoming NavRoute events do NOT overwrite `_final_waypoint` (frozen for the detour's duration). Both seat-1's `_held_destination` and seat-2/3's `_detour_active` are adopted â€” they are complementary, not alternatives (the flag protects the cache; the field holds the goal).
- Clear `_held_destination` and `_detour_active` only AFTER the re-route-back is confirmed (a new NavRoute whose final waypoint SystemAddress == `_held_destination[0]`).

---

## 3. CANDIDATE SELECTION â€” farthest reachable refuel-primary

### 3.1 The reconciled decision (this was the seats' biggest divergence)
- **Seat-1** recommended selecting the farthest reachable KGBFOAM star **in the galaxy map** (it shows star class + the in-range "reachable bubble"), arguing the blind nav panel can't expose star class without CV.
- **Seats 2 & 3** followed the operator's literal "cycle the nav list to the bottom" and tried to drive selection from the nav panel, then conceded the blocking problems: the panel text is unreadable (no CV), and locking a row to read its `FSDTarget.StarClass` is itself uncertain (does cursor-nav even emit FSDTarget?).

**Synthesis verdict: the galaxy map is the correct candidate surface, AND it is the same interaction as the re-route (Â§5).** Grounds:
1. We have **no offline stars database** â†’ we cannot compute KGBFOAM neighbors or in-range systems locally. Confirmed: nothing in `src/` reads a systems DB.
2. The nav panel exposes neither star class nor a reliable in-range filter to the bot (no CV on the panel; `FSDTarget` on a bare cursor move is unverified â€” see Open Q).
3. The galaxy map natively shows star class (filterable) AND draws the reachable bubble at current jump range. Since BC2 gives us galaxy-map control anyway for the re-route, **candidate selection and the detour plot collapse into ONE human-in-the-loop galaxy-map session.** This deletes the unreliable blind-panel-class-reading problem entirely.

The operator's "farthest reachable scoopable" INTENT is preserved exactly â€” it just executes in the galaxy map, where class + range are visible, rather than the blind nav panel. **This is an explicit Open Question for Operator** (his words said "nav list"); the plan recommends galaxy-map selection and flags the divergence loudly.

### 3.2 "Reachable" determination
- **Bot-side ranking estimate:** `max_jump_range(spec, ship_mass, fuel_main)` (math.py:110). A candidate at distance `d` is plausibly reachable iff `d <= max_jump_range(...)`. Used to order attempts farthest-first.
- **Ground truth = the game.** Operator: "Some candidates will error / deny in-game (out of range) â€” skip those down the list until one is reachable." So reachability is CONFIRMED by the plot attempt.

### 3.3 Deny detection + skip-to-next (clean, no CV needed for the deny case)
After a plot attempt, `NavRoute.json` either:
- gains a route toward the requested system (`not NavRoute.empty`) â†’ **success**, OR
- stays empty / unchanged after the macro â†’ **denied / out-of-range** â†’ step to the next-farthest candidate and retry.
This is a pure existing state read. **Open Q:** whether an out-of-range plot also throws an in-game modal that must be dismissed before the next attempt (determines if deny-handling is pure NavRoute-state or needs a clearing keypress).
- Bound the candidate loop at a configurable `max_candidates` (default 10) to prevent an infinite walk in a depleted region.

---

## 4. THE DETOUR EXECUTION

A new runner-dispatched flow `dispatch_secondary_refuel(ev)` orchestrating a new `secondary_refuel` procedure (NOT chained inside arrival). Sequence:

1. **Snapshot & freeze (Â§2):** capture `_held_destination`; set `_detour_active = True`. Announce on overlay + stdout + recorder. **Park the ship** (zero throttle, hold) â€” never fly blind while deciding/waiting.
2. **Pick + plot the refuel star (galaxy map, human-assisted â€” Â§5, Â§6):** open map, select/plot the farthest reachable KGBFOAM (human-provided coordinates OR keyboard search, per the BC2 resolution). On deny â†’ next candidate (Â§3.3).
3. **Jump to the refuel star â€” REUSE the normal loop verbatim.** Once `NavRoute` populates toward the refuel star, `target_next_route` â†’ `orient_compass` â†’ `orient_widget_ring` â†’ `engage_jump` â†’ `hold_alignment` run exactly as in arrival.toml. **The danger filter is NOT bypassed:** each detour hop still passes `target_next_route`'s `is_dangerous` gate. If the refuel star is multiple hops away, the standard loop runs each hop.
4. **Scoop â€” REUSE `step_scoop_refuel` unchanged**, but invoked with a **higher fill threshold** for the emergency: pass `refuel_below=1.0` (fill to capacity; we are here precisely because we ran low). On arrival at the refuel star (KGBFOAM by construction), the scoop's own skip-gate confirms `is_scoopable(arrival_star_class)` and drinks to full. (Seats proposed 0.0 / 1.0; `refuel_below` is a fraction-of-capacity skip threshold, so **1.0 = "never skip, always top off"** is the correct value â€” verified against steps.py:1219.)
5. **Re-route to the held destination (galaxy map, human-assisted â€” Â§5, Â§6):** open map, search/paste `_held_destination[1]`, plot. Identical interaction to step 2.
6. **Verify + resume:** confirm the new route's final waypoint SystemAddress == `_held_destination[0]` (abort-to-human on mismatch â€” Â§7). Clear `_held_destination` + `_detour_active`. The next live FSDJump re-enters the normal arrival loop toward the original destination.

**Reuse summary:** steps 3, 4, 6 reuse existing code verbatim. New code is concentrated in steps 1, 2, 5 (snapshot + galaxy-map plot/replot + candidate iteration). `secondary_refuel` must be added to `_PREEMPT_ON_SMACK` (dispatcher.py:31) because it flies live supercruise scenes (the jump-to-refuel and scoop legs).

---

## 5. RE-ROUTE VIA GALAXY MAP â€” the BC2 dependency

The galaxy-map interaction is used **twice** (plot to refuel star, plot back to held destination) and is identical both times. It is a blind UI macro in the same family as `navpanel.py`. Operator's described sequence: **open with numpad `*`, click coordinates the human provides, paste the destination name, click, click, `*` (close).**

### 5.1 Reconciliation: mouse vs keyboard (resolved, with a flagged Open Q)
All three seats assumed an absolute-mouse primitive. The re-read surfaces a material correction: **the sender is keyboard-only, and the ONLY existing "human calibrates a UI once, bot replays" precedent (`menu_nav`) is keyboard press-counts + arrow directions, not mouse coordinates** (wizard.py:201-313). So there are two viable BC2 shapes:
- **(Mouse path)** add an absolute SendInput mouse move+click primitive; replay human-calibrated screen coordinates. Matches the operator's literal "click coordinates" words. RISK: some titles reject synthetic mouse â€” needs a probe.
- **(Keyboard path â€” recommended to evaluate first)** drive the galaxy-map search box with `UI_Select`/arrow navigation + typed text, mirroring `menu_nav`. Avoids the synthetic-mouse rejection risk and the new mouse primitive entirely, and reuses the proven calibration idiom. RISK: the galaxy-map search field may not be fully keyboard-reachable.

The operator said "click," so the mouse path is the stated intent â€” but the keyboard path is more idiomatic and lower-risk. **This is an Open Question for Operator** (settled together during the build session). BC2 dependencies are enumerated for BOTH so neither blocks.

### 5.2 BC2 must deliver (each is a hard dependency of BC3)
1. **`galmap_open()`** â€” press the galaxy-map bind (numpad `*`; scancode `Key_Numpad_Multiply=0x37` exists). The ED action **`GalaxyMapOpen` must be added to `REQUIRED_ACTIONS`** (binds_validate.py) and bound in the live preset. Gate "map open" on `Status.gui_focus` reaching the galaxy-map value (NOT a clock). **The galaxy-map GuiFocus integer is undefined in the codebase â€” Open Q.**
2. **`galmap_close()`** â€” press the same bind again (or UI_Back). Gate "closed" on `gui_focus` returning to 0 (reuse `_ensure_cockpit_focus`, steps.py:605).
3. **Search-field focus + text entry `galmap_search(name)`** â€” focus the search box and enter the destination name. Keyboard-path: arrow/UI_Select to the field + typed scancodes (or clipboard Ctrl+V). Mouse-path: click the search-box coordinate, then Ctrl+V/typed text. Pasting (clipboard-set + Ctrl+V) is more robust than typing for arbitrary names â€” **Open Q: does Ctrl+V work in the ED galaxy-map field, and is the field keyboard-reachable?**
4. **`galmap_confirm_plot()`** â€” select the searched result and activate "Plot Route" (the "click, click"). Coordinate clicks (mouse path) and/or `UI_Select` (keyboard path).
5. **[Mouse path only] absolute mouse move+click primitive** â€” NEW low-level capability; `keys/sender.py` is keyboard-only. SendInput MOUSEEVENTF_ABSOLUTE move + left click. **Open Q: confirm ED accepts synthetic mouse (probe).**
6. **[Mouse path only] coordinate storage** â€” store human-captured screen coordinates in a `[galmap]` config block, the SAME pattern as `[menu_nav]` calibration (cli.py:666-705). Captured ONCE during the interactive build session.
7. **`galmap_plot_succeeded() -> bool` / deny contract** â€” BC2's macro MUST return control deterministically so BC3 can read the outcome: non-empty NavRoute toward the requested system == success; empty/unchanged route after the macro == out-of-range/denied (â†’ next candidate). BC2 owns making the macro terminate cleanly; BC3 owns the NavRoute read.

---

## 6. HUMAN-IN-THE-LOOP PROTOCOL

The bot needs the human ONLY for the galaxy-map legs (coordinate provision / search-confirm), and the operator is slow relative to the bot. The wait MUST be event/flag-driven, never a wall-clock gate (house rule: no-arbitrary-timed-waits).

### 6.1 Protocol
1. **Announce + park.** On divert decision: write a persistent overlay STATUS line (`[SECONDARY FUEL] No scoopable star for 2 hops, low fuel. Diverting â€” open galaxy map and stand by.`), print to stdout, record an event, and zero throttle / hold. Never fly blind while waiting.
2. **Run as much autonomously as it can.** If coordinates are pre-calibrated in `[galmap]` (or the keyboard path is fully scripted), the bot runs the macro and only asks the human to CONFIRM the plotted result. Otherwise it pauses and asks the human to perform/dictate the clicks.
3. **The wait gate is a human-input flag OR a state change â€” never a timer.** Add a thread-safe `ProceedSwitch` (same shape as the existing `PanicSwitch`: `tripped`/`trip()`/`reset()`), driven by a dedicated hotkey via the existing `HotkeyListener` (`panic_listener.py`). The bot blocks in a cooperative poll loop that exits on ANY of:
   - `proceed.tripped` (human says go), OR
   - `panic.tripped` / `should_abort()` (operator abort â€” checked EVERY iteration), OR
   - a **state signal that the plot landed**: `NavRoute.json` became non-empty toward the refuel star (step 2) / final waypoint == `_held_destination[0]` (step 5). These let the bot auto-advance the instant the plot lands, with the hotkey as the fallback/confirm. No deadline anywhere.
4. **Human input channel for coordinates** (mouse path only): preference is Operator's â€” candidates are (A) a JSON file drop at a known path the bot watches on mtime, (B) a second terminal prompt, (C) other. **Open Q.** The watch is a poll loop on mtime/flag with `should_abort()` every iteration â€” not a sleep.
5. **Minimum two hotkeys:** PROCEED (advance past a human gate) and the existing PANIC (abort to human). Optional NEXT-CANDIDATE hotkey only if deny can't be auto-read from NavRoute (it usually can â€” Â§3.3).

---

## 7. FAIL-SAFES

**Core principle: fail-closed, never strand worse than today.** Today a fuel-poor route runs until ED refuses the jump and the procedure aborts to human. BC3 must be strictly better or equal.

1. **Divert decision indeterminate** (no NavRoute / no spec / no Status fuel) â†’ do NOT divert â†’ today's arrival scoop + danger filter remain the floor. Never worse.
2. **No reachable refuel-primary** (every candidate denies / none in range, `max_candidates` exhausted) â†’ **abort to human**: persistent overlay (`[SECONDARY FUEL] No reachable scoopable star â€” manual intervention required.`), park, zero throttle, hold. Do NOT keep jumping toward a fuel-starved destination. **Keep the heat watchdog alive** â€” `return` from the detour; do NOT call `request_stop()` (that kills heat protection, dispatcher.py:1089/872). `_detour_active` stays set so a manual operator re-route doesn't corrupt `_final_waypoint`.
3. **Galaxy-map macro fails / GuiFocus never reaches the map** â†’ abort to human (can't plot blind). Park.
4. **Detour route has a danger-class hop** â†’ the existing `target_next_route` `is_dangerous` filter refuses it â†’ that hop fails closed â†’ try next candidate or abort to human. The danger filter is NEVER bypassed during the detour.
5. **Scoop fails at the refuel star** (`step_scoop_refuel` â†’ False) â†’ arrival/detour `on_required_fail` retries per policy; on exhaustion, abort to human. Do NOT attempt the re-route (may lack fuel to jump again).
6. **Arrival star at the refuel target is not scoopable** (should be impossible by construction) â†’ `step_scoop_refuel`'s own `is_scoopable(arrival_star_class)` skip-gate returns True-as-skip; the detour then re-routes on a still-low tank â†’ the NEXT fuel-check re-evaluates. Add an explicit guard: if `_detour_active` AND arrival star not scoopable â†’ abort to human immediately (don't loop).
7. **Held-destination lost** (`_resolve_final_waypoint()` returns None at re-route time) â†’ abort to human rather than guess. The early snapshot (Â§2) makes this rare.
8. **Re-route lands on the wrong system** (name paste ambiguous / duplicate names) â†’ the verify step (NavRoute final SystemAddress != `_held_destination[0]`) catches it â†’ abort to human; never resume toward a wrong destination.
9. **Smack / interdiction mid-detour** â†’ existing `_PREEMPT_ON_SMACK` + `smack_recovery` own scene recovery (add `secondary_refuel` to that set). Witchspace pause (interpreter.py:59) still applies to detour steps.
10. **Operator panic** â†’ `should_abort()` polled every loop iteration â†’ immediate clean stop, keys released.
11. **Restart durability:** `_held_destination` + `_detour_active` are runtime-only. On a mid-detour crash + restart, the route plotted to the refuel star is a scoopable arrival â†’ normal arrival+scoop runs â†’ the next fuel-check re-evaluates from scratch. Worst case: scoops, finds itself routed to the refuel star with no held destination, and aborts-to-human for a manual re-plot â€” never a strand. **Open Q: persist `_held_destination` to disk to survive restart, or accept abort-to-human-on-restart?** (Not persisting is simpler and safer.)

---

## 8. OPEN QUESTIONS FOR KYLE

(Carried from all three seats; deduplicated; the two material divergences flagged.)

1. **Fuel-model fidelity (live test):** does `fsd.math.fuel_cost(predicted)` track actual `FSDJump.fuel_used` within a few % on your current ship/loadout? Need one predicted-vs-actual comparison over 2-3 hops before trusting predicted cost. If it drifts, BC3 falls back to `_last_fuel_used` only.
2. **Safety margin:** how many tonnes / jumps of reserve should trigger the divert? "Need fuel by next jump" â€” is the threshold "can't make 2 more hops," or a specific tonnage? Suggested default: one full `max_fuel_per_jump` in the tank. Give a number.
3. **[DIVERGENCE] Candidate surface â€” galaxy map vs nav panel:** synthesis RECOMMENDS selecting the farthest reachable KGBFOAM star **in the galaxy map** (it shows star class + the reachable bubble; the blind nav panel exposes neither to the bot without CV, and we have no offline stars DB). Your words said "cycle the nav list." Is galaxy-map selection acceptable, or do you want literal nav-panel cycling (which would need panel CV or blind trial-and-error)?
4. **[DIVERGENCE] Galaxy map: mouse vs keyboard:** you described "click coordinates," but the sender is keyboard-only today and the existing `menu_nav` calibration idiom is keyboard press-counts, not mouse. Can the galaxy-map search box be driven keyboard-only (UI_Select/arrows + typed text), or is it strictly mouse-click? Keyboard avoids a new mouse primitive AND the synthetic-mouse-rejection risk. (If mouse: BC2 adds absolute SendInput mouse move+click; we probe that ED accepts synthetic mouse.)
5. **Galaxy-map key + GuiFocus value:** confirm the open/close action is `GalaxyMapOpen` on numpad `*`. What integer does `Status.json GuiFocus` report when the galaxy map is open? (Undefined in the codebase; needed to gate open/close on real state, not a clock. Likely 6 per EDCD docs â€” must confirm live.)
6. **Paste vs type:** for the destination name into the search field â€” paste from clipboard (Ctrl+V) or type char-by-char? Confirm Ctrl+V works in the ED galaxy-map search box.
7. **Deny detection:** when a candidate is out of range, does the galaxy map simply fail to draw a route (NavRoute stays empty, no popup), or show a modal we must dismiss? Determines whether deny-handling is pure NavRoute-state or needs a clearing keypress.
8. **Click-coordinate capture (if mouse path):** we capture the search-box / result / Plot-Route coordinates together during the build session and store them in a `[galmap]` config block (like `[menu_nav]`). Confirm screen resolution / layout stable enough for replay.
9. **Human input channel (if mouse path):** for providing galmap coordinates at detour time â€” prefer (A) a JSON file drop the bot watches on mtime, (B) a second terminal prompt, or (C) something else?
10. **Restart durability:** persist `_held_destination` to disk so a mid-detour crash resumes to the right place, or accept abort-to-human-on-restart? (Not persisting is simpler and safer.)
11. **Duplicate system names:** acceptable to plot by pasted name then verify by SystemAddress post-plot and abort-to-human on mismatch (the plan), or do you want coordinate-based plotting to disambiguate?
12. **Trigger boundary â€” post-jump vs pre-jump:** the plan evaluates the trigger AFTER `FSDJump` lands (in the arrival scene, fits existing dispatch), reading `route[1]`/`route[2]` as this-jump/next. A pre-jump check would avoid one wasted transit but shifts the indexing by one. Confirm post-jump is fine (recommended).
13. **Nav-panel mechanics (only if you choose nav-panel candidate selection, Q3):** does HOLD `UI_Down` saturate at the bottom of a long (50+) list (symmetric with HOLD-up-to-top)? Does `UI_Select` on a row emit `FSDTarget` (so the bot could read StarClass), and does `UI_Back` cancel that lock cleanly? (Moot if galaxy-map selection is chosen.)
14. **Multi-star systems:** `NavRoute.json` carries one StarClass per waypoint (the PRIMARY). If a system's primary is non-scoopable (L/T/Y) but it has a scoopable secondary, does the ED planner ever route there, and does `is_scoopable` on the NavRoute primary correctly predict the arrival star will be scoopable? (Affects whether non-scoopable-primary hops are safely skippable.)


---

## Appendix A — BC2 dependencies (canonical, council list)

1. galmap_open() â€” press the GalaxyMapOpen bind (numpad '*', scancode Key_Numpad_Multiply=0x37 already exists). 'GalaxyMapOpen' must be ADDED to binds_validate.REQUIRED_ACTIONS and bound in the live preset. Gate 'map open' on Status.gui_focus reaching the galaxy-map value (UNDEFINED in the codebase â€” must be confirmed live), never a clock.
2. galmap_close() â€” press the GalaxyMapOpen bind again (or UI_Back); gate 'closed' on Status.gui_focus returning to 0 (reuse the existing _ensure_cockpit_focus helper in flow/steps.py).
3. galmap_search(name) â€” focus the galaxy-map search field and enter the destination system name. Keyboard path: UI_Select/arrow navigation to the field + typed scancodes or clipboard Ctrl+V. Mouse path: click the search-box coordinate then Ctrl+V/type. (Confirm with Operator whether the field is keyboard-reachable and whether Ctrl+V works in the ED galaxy-map search box.)
4. galmap_confirm_plot() â€” select the searched result and activate the 'Plot Route' control (the operator's 'click, click'): coordinate clicks (mouse path) and/or UI_Select presses (keyboard path).
5. galmap_plot_succeeded() / deny contract â€” the macro MUST return control deterministically so BC3 can read the outcome from NavRoute.json: a non-empty route toward the requested system = success; an empty/unchanged route after the macro = out-of-range/denied (advance to the next candidate). BC2 owns terminating the macro cleanly so this state read is valid.
6. [MOUSE PATH ONLY] Absolute mouse move + left-click primitive â€” NEW low-level capability; keys/sender.py is keyboard-only today (pydirectinput SendInput KEYEVENTF_SCANCODE, no mouse anywhere in src/). BC2 must add SendInput MOUSEEVENTF_ABSOLUTE move + left click. Required ONLY if the galaxy map cannot be driven keyboard-only â€” to be decided with Operator.
7. [MOUSE PATH ONLY] Clipboard-set + coordinate storage â€” set the Windows clipboard to a system name (ctypes/pywin32) for Ctrl+V paste, and store human-captured search-box / result / Plot-Route screen coordinates in a [galmap] config block, the SAME pattern as the existing [menu_nav] calibration (captured once during the interactive build session).
8. Confirmation (probe) that ED accepts synthetic SendInput mouse for galaxy-map clicks â€” required only on the mouse path; some titles reject synthetic mouse. The keyboard path avoids this risk entirely.

## Appendix B — Open questions for Operator (canonical, council list)

1. Fuel-model fidelity (LIVE TEST): does fsd.math.fuel_cost(predicted) track actual FSDJump.fuel_used within a few % on your current ship/loadout? Need one predicted-vs-actual comparison over 2-3 hops before trusting predicted cost for the divert trigger; else BC3 falls back to last FSDJump.fuel_used only.
2. Safety margin: how many tonnes / jumps of fuel reserve should trigger the divert? Suggested default is one full max_fuel_per_jump in the tank â€” give a number or a rule.
3. [DIVERGENCE] Candidate surface â€” galaxy map vs nav panel: synthesis RECOMMENDS selecting the farthest reachable KGBFOAM star in the GALAXY MAP (it shows star class + the reachable bubble; the blind nav panel exposes neither to the bot without CV, and there is no offline stars DB). Your words said 'cycle the nav list.' Is galaxy-map selection acceptable, or do you want literal nav-panel cycling?
4. [DIVERGENCE] Galaxy map mouse vs keyboard: you described 'click coordinates,' but the sender is keyboard-only and the existing menu_nav calibration idiom is keyboard press-counts, not mouse. Can the galaxy-map search box be driven keyboard-only (UI_Select/arrows + typed text), or is it strictly mouse-click? Keyboard avoids a new mouse primitive and the synthetic-mouse-rejection risk.
5. Galaxy-map key + GuiFocus value: confirm the open/close action is GalaxyMapOpen on numpad '*'. What integer does Status.json GuiFocus report when the galaxy map is open? (Undefined in the codebase; needed to gate open/close on real state, not a clock. Likely 6 per EDCD docs â€” confirm live.)
6. Paste vs type for the destination name into the search field: clipboard Ctrl+V or char-by-char typing? Confirm Ctrl+V works in the ED galaxy-map search box.
7. Deny detection: when a candidate is out of range, does the galaxy map simply fail to draw a route (NavRoute stays empty, no popup) or show a modal that must be dismissed? Determines whether deny-handling is pure NavRoute-state or needs a clearing keypress.
8. Click-coordinate capture (mouse path only): OK to capture the search-box / result / Plot-Route coordinates together during the build session and store them in a [galmap] config block like [menu_nav]? Is screen resolution / layout stable enough to replay?
9. Human input channel (mouse path only): to provide galmap coordinates at detour time, prefer (A) a JSON file drop the bot watches on mtime, (B) a second terminal prompt, or (C) something else?
10. Restart durability: persist the held original destination to disk so a mid-detour crash resumes to the right place, or accept abort-to-human-on-restart? (Not persisting is simpler and safer.)
11. Duplicate system names: acceptable to plot by pasted name then verify by SystemAddress post-plot and abort-to-human on mismatch (the plan), or do you want coordinate-based plotting to disambiguate?
12. Trigger boundary â€” post-jump vs pre-jump: the plan evaluates the trigger AFTER FSDJump lands (in the arrival scene, fits existing dispatch), reading route[1]/route[2] as this-jump/next. A pre-jump check avoids one wasted transit but shifts indexing by one. Confirm post-jump is acceptable (recommended).
13. Nav-panel mechanics (ONLY if you pick nav-panel candidate selection): does HOLD UI_Down saturate at the bottom of a long (50+) list, symmetric with HOLD-up-to-top? Does UI_Select on a row emit FSDTarget (so the bot can read StarClass), and does UI_Back cancel that lock cleanly without poisoning the next candidate?
14. Multi-star systems: NavRoute.json carries one StarClass per waypoint (the PRIMARY). If a system's primary is non-scoopable (L/T/Y) but it has a scoopable secondary, does the ED planner ever route there, and does is_scoopable on the NavRoute primary correctly predict the arrival star will be scoopable?

