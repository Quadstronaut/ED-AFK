# GATEWALK TRIGGER CATALOG

**Purpose.** This is the authoritative live trigger map for the ED-AFK auto-jump bot. Every row is a journal event, Status.json flag, NavRoute file change, or derived condition that the bot acts on — quoted to file:line. Use it to decide where to graft new behavior, change routing logic, or close known gaps. If a trigger fires but the bot ignores it, it appears in section G and the GAPS section with the consequence noted.

---

## A. Startup / `_maybe_startup` priorities

These run ONCE per session at the point the live loop first drains its backlog queue (first empty poll in `run_live`). `_startup_done` latches prevent a second fire. `dispatcher.py:1186-1190` is the call site.

| Trigger (event / flag / condition) | Where read (file:line) | Current effect / dispatch | Observed 2026-06-09? | Candidate hook? |
|---|---|---|---|---|
| `_startup_done` already True | dispatcher.py:1017 | Early return — `_maybe_startup` is a one-shot | N (internal guard) | - |
| `status.docked == True` at launch | dispatcher.py:1023-1024 | **Idle** — docked-on-load does nothing | N | Hook: could dispatch `dock_resume` if a route is already plotted (current gap: requires a fresh NavRoute event to trigger instead) |
| `in_supercruise == True` + `_is_parked_terminal()` (empty route + local-star Destination or no lock) | dispatcher.py:1032-1036 | **Idle** — `RouteCompleteIdleOnRestart` record, no procedure | N | Hook: operator notification; could refresh overlay |
| `in_supercruise == True` + `_destination_is_local_star()` returns **True** | dispatcher.py:1080-1086 | P2: dispatch `arrival` — orbit get-around needed | **Y — 19:48:59** (system Tyroopps OT-X b48-0, near_star=True) | - |
| `in_supercruise == True` + dest/system **indeterminate** (None) | dispatcher.py:1071-1077 | P1 fail-safe: dispatch `arrival` | N | - |
| `in_supercruise == True` + jump_age ≤ 30 s (FRESH_ARRIVAL_WINDOW_S) | dispatcher.py:1093-1100 | P3 smack guard: dispatch `arrival` even when Destination looks non-local (ED pre-loads next hop before scene settles) | N | - |
| `in_supercruise == True` + jump_age > 30 s + Destination **not** local star | dispatcher.py:1104-1112 | P4: dispatch `sc_resume` (fast resume, no orbit) | **Y — 18:46:44** (jump_age 2489 s) and **19:16:48** (jump_age 62 s) | Hook: log the loiter context (was it a genuine wait or a near-star misclassification?) |
| `_smacked == True` + `status.fsd_cooldown == True` (normal space, smack cooldown still burning) | dispatcher.py:1114-1130 | Dispatch `smack_recovery` | **Y — 21:58:46** (smacked=True, startup dispatched; cooldown gate may have been clear by that run — see session data) | - |
| Normal space + route empty (`route is None or []`) | dispatcher.py:1148-1159 | `NoRouteOnStartup` record, overlay alert, **no procedure** — bot idles | N | Hook: arm a NavRoute-watcher so a subsequent plot auto-dispatches without a relaunch (current gap: requires relaunch) |
| Normal space + route non-empty | dispatcher.py:1160 | Dispatch `startup` | **Y — 21:58:46 / 22:05:03 / 22:22:04** (all three: smacked=True, normal space, route_len=248) | - |

---

## B. In-flight `dispatch()` events (live loop only, after `_caught_up`)

`dispatch()` is called at `dispatcher.py:518` for every live event after catch-up. Backlog events only update state via `_on_tail_event` → `_apply_state`; they do NOT call `dispatch()`.

| Trigger (event / flag / condition) | Where read (file:line) | Current effect / dispatch | Observed 2026-06-09? | Candidate hook? |
|---|---|---|---|---|
| `FSDJump` — general (not final hop) | dispatcher.py:521-541 | Dispatch `arrival`; overlay "Jump N: System" | **Y — 22:33:58** (FSDJump → arrival; jump_age 0.52 s) | Hook: per-jump telemetry accumulation (jump count already tracked in `_jumps:523`) |
| `FSDJump` — `_is_route_complete()` returns True | dispatcher.py:534-540 | Consumes `_navroute_cleared` latch; calls `dispatch_route_complete()` | **Y — 22:35:16** (→ `route_complete_park`) | Hook: route-completion celebration / notification |
| `SupercruiseExit` with `body_type == "Star"` | dispatcher.py:542-544 | Sets `_event_times["drop"]`; dispatches `smack_recovery` | N (live; session had no live smack) | Hook: smack counter / analytics |
| `NavRoute` event while `_docked == True` + route non-empty | dispatcher.py:545-560 | `DockPitStopResume` record; dispatches `dock_resume` | **Y — 22:45:08** (NavRoute plotted at Tortooga; dock_resume fired) | Hook: ETA estimate for pit-stop resume |
| `NavRoute` event while `_docked == True` + route empty | dispatcher.py:553-556 | No dispatch — `_run("dock_resume")` gated on `if route:` | N | Hook: could idle-notify operator that a clear-while-docked happened |

---

## C. Route-complete detection

`_is_route_complete()` is a pure predicate evaluated inside `dispatch()` on every `FSDJump`. Four conditions must ALL hold.

| Trigger (event / flag / condition) | Where read (file:line) | Current effect | Observed 2026-06-09? | Candidate hook? |
|---|---|---|---|---|
| `_navroute_cleared == True` (set by `NavRouteClear` event) | dispatcher.py:604 | Gate 1 of 4: must be latched | N (implicitly required) | - |
| `_navroute_cleared_utc` within `_CLEAR_JOIN_WINDOW_S` (60 s) of `FSDJump` journal timestamp | dispatcher.py:609-614 | Gate 2 of 4: correlation window prevents false fire from a manual re-plot | N | Hook: could log near-misses (gap 55-60 s = worryingly close to the window) |
| `_resolve_final_waypoint()` non-None — from event cache OR NavRoute.json file | dispatcher.py:605-606, 563-588 | Gate 3 of 4: final destination known | N | - |
| `FSDJump.system_address == final_waypoint[0]` (int match) | dispatcher.py:615-616 | Gate 4 of 4: arrival IS the destination | N | - |
| `dispatch_route_complete` — `_fresh_status().destination` read at call time, Body == 0 or local-star name | dispatcher.py:632-660 | Routes to **`route_complete_park`** | **Y — 22:35:16** (destination="Wredguia UH-U c16-10", Body=0 read at call time → park path) | **BUG**: single too-early Status read; station target invisible if ED hasn't overwritten the star yet. Design direction: let game settle, then read; add background re-watcher for Destination changes |
| `dispatch_route_complete` — `_dock_target` captured at plot time, `body != 0`, `system_address` matches | dispatcher.py:641-649 | Routes to **`dock`** (station dock flow) — CAPTURE-AT-PLOT path | N (mechanic UNCONFIRMED: Status.Destination.Body!=0 at NavRoute event never observed for station plot) | **UNCONFIRMED**: test by plotting to a named station, reading Status.json before any TargetNextRouteSystem |
| `dispatch_route_complete` — live Status path: `dest.body != 0` AND `dest.system == arrival_addr` AND `local_star is False` | dispatcher.py:654-659 | Routes to **`dock`** (legacy live-status path) | N | Covers single-hop station routes where no TargetNextRouteSystem was pressed |

---

## D. Dock / undock lifecycle

| Trigger (event / flag / condition) | Where read (file:line) | Current effect | Observed 2026-06-09? | Candidate hook? |
|---|---|---|---|---|
| `Docked` journal event | dispatcher.py:874-878 (\_apply\_state) | Sets `_docked=True`, clears `_docking_denied_reason`, captures `_docked_station` | **Y — 22:45:08** (implicit; DockPitStopResume record shows station="Tortooga") | **GAP**: no dispatch from the `Docked` event alone — if the bot was running `dock` procedure it succeeds, but a Docked event outside a procedure is silently swallowed |
| `Undocked` journal event | dispatcher.py:879-880 (\_apply\_state) | Sets `_docked=False` | N | - |
| `DockingGranted` journal event | dispatcher.py:871-873 (\_apply\_state) | Clears `_docking_denied_reason` | N (procedure-internal) | - |
| `DockingDenied` journal event | dispatcher.py:866-870 (\_apply\_state) | Stores `_docking_denied_reason` (Reason field) | N | - |
| `ReceiveText` with `"$STATION_NoFireZone_entered;"` in message | dispatcher.py:858-865 (\_apply\_state); steps.py:1541-1553 (consumer) | Sets `_no_fire_zone_entered = True`; `step_dock_approach` gates on this | N | Hook: log distance/time-to-approach as an efficiency metric |
| `status.docked == True` (state fallback) | steps.py:1614-1616, 1665-1667 | `step_dock_request` and `step_dock_await_docked` accept already-docked as success | N | - |
| `FsdMassLocked` flag clear | steps.py:1814 (`step_wait_masslock_clear`) | `dock_resume` gate: unblocks jump after auto-launch fly-out | **Y — 22:45:08** (dock_resume snapshot shows `fsd_mass_locked=True` at dispatch; step gates on clearing) | - |
| `Undocked` event / `status.docked == False` (state fallback) | steps.py:1781-1787 (`step_auto_launch`) | `dock_resume` gate: auto-launch success | N (procedure-internal) | - |

---

## E. Smack / witchspace latches

These are maintained in `_record_event_time` and `_apply_state`, called exactly once per event by the hub (backlog AND live).

| Trigger (event / flag / condition) | Where read (file:line) | Current effect | Observed 2026-06-09? | Candidate hook? |
|---|---|---|---|---|
| `SupercruiseExit` with `body_type == "Star"` | dispatcher.py:736-745, 755-757 | Sets `_smacked=True`; sets `_event_times["drop"]`; if current proc is in `_PREEMPT_ON_SMACK`, sets `_preempt="star_smack"` | **Y — backlog** (smacked=True at 21:58:46 / 22:05:03 / 22:22:04 startup dispatches) | Hook: smack-count session metric |
| `SupercruiseExit` with `body_type != "Star"` | dispatcher.py:755-757 | Sets `_smacked=False` | N | - |
| `SupercruiseEntry` | dispatcher.py:757-758 | Clears `_smacked=False`; also clears `_in_witchspace` (belt-and-suspenders) | N (live clears) | - |
| `FSDJump` (any) | dispatcher.py:757-758 | Clears `_smacked=False` | N | - |
| `StartJump` with `jump_type == "Hyperspace"` | dispatcher.py:764-765 | Sets `_in_witchspace=True` | N | - |
| `FSDJump` / `SupercruiseEntry` / `Docked` | dispatcher.py:766-767 | Clears `_in_witchspace=False` (belt-and-suspenders: missed FSDJump can't permanently wedge) | N | - |
| `_in_witchspace == True` (derived latch) | interpreter.py (via context) | Interpreter PAUSES every step while True — nav panel / orient scene invalid | N (transparent to dispatch) | - |
| `_preempt == "star_smack"` set mid-procedure | dispatcher.py:744-749 | Aborts current procedure at next `_run_abort()` poll; prints `[PREEMPTED]` | N (no live smack this session) | - |
| `status.fsd_cooldown == True` during smacked startup check | dispatcher.py:1114 | Discriminator: REAL smack (cooldown still burning) vs stale journal smack (cooldown gone by boot) | **Y — 22:22:04** (smacked=True but fsd_cooldown=False → fell through to startup, not smack_recovery; confirms cooldown gate worked) | - |
| `SupercruiseDestinationDrop` | dispatcher.py:905-907 (\_apply\_state) | Increments `_drop_seq` (body\_tour station/POI hint) | N | - |
| `SupercruiseExit` (any, not just star) | dispatcher.py:909-912 (\_apply\_state) | Increments `_scex_seq` (body\_tour re-engage trigger PD7) | N | - |

---

## F. Per-step gates inside procedures

These are the event/state gates that individual steps block on. They do not call `dispatch()` — they are polled inside a running procedure.

| Trigger (event / flag / condition) | Step / file:line | Procedure(s) | Observed 2026-06-09? | Candidate hook? |
|---|---|---|---|---|
| `FSDTarget` event (new seq > pre-press snapshot) + StarClass safety check | steps.py:62-147 (`step_target_next_route`) | arrival, startup, sc\_resume, smack\_recovery, dock\_resume | N (internal) | Hook: log dangerous-star refusals (`TargetDangerRefused`) as a session safety metric |
| `Status.Destination` locked on onward route hop (already-locked path) | steps.py:132-147 | same as above | N | - |
| `Status.fsd_charging / fsd_cooldown / fsd_mass_locked / overheating / docked` flags — any True | steps.py:150-159 (`step_engage_jump`) | all jump procedures | N | Hook: a repeated `EngageBlocked` with reason=fsd_cooldown would flag a retry-loop inefficiency |
| `SupercruiseEntry` event OR `status.in_supercruise == True` | steps.py:170-257 (`step_engage_supercruise`) | startup (get-around lane), smack\_recovery | N | - |
| `FsdCharging` True → False without SupercruiseEntry (charge dropped) | steps.py:228-255 | same | N | - |
| `status.analysis_mode` flag (bit 27) | steps.py:267-306 (`step_ensure_analysis_mode`) | honk (parallel track in every procedure) | N | - |
| `FSSDiscoveryScan` event | steps.py:348-390 (`step_hold_until_event`), honk.toml:26 | honk track | N | - |
| `status.fsd_cooldown == False` | steps.py:330-345 (`step_wait_cooldown_clear`) | smack\_recovery | N | - |
| `StartJump` event OR `status.fsd_jump == True` (bit 30) | steps.py:879-997 (`step_hold_alignment`, `until_event="StartJump"`) | arrival, startup, sc\_resume, smack\_recovery, dock\_resume | N | - |
| `SupercruiseEntry` event OR `status.in_supercruise == True` (via hold\_alignment `until_event`) | steps.py:879-997 | smack\_recovery (escape-vector hold to SC entry) | N | - |
| `Status.fsd_charging` True → False without commit (charge dropped mid-hold) | steps.py:966-982 | all procedures with hold\_alignment | N | Hook: `charge_dropped` exits are the signal a retry will fire — log as `HoldAlignmentChargeDrop` for session analysis |
| `Status.GuiFocus == 0` (cockpit focus gate) | steps.py:606-633 (`_ensure_cockpit_focus`) | any step calling sc\_assist\_orbit, nav\_panel\_target, dock\_target\_station, dock\_sc\_assist, dock\_approach, dock\_request, station\_services, orient\_compass, pitch\_compass | N | - |
| `ReceiveText "$STATION_NoFireZone_entered;"` + `_no_fire_zone_entered` latch | steps.py:1541-1553 (`step_dock_approach`) | dock | N | - |
| `Status.Destination.body != 0` + non-symbolic name (`_dest_is_named_station`) | steps.py:1345-1355, 1381-1416 (`step_dock_target_station`) | dock, dispatch_route_complete capture-at-plot (dispatcher.py:836) | N | - |
| `DockingGranted` event OR `status.docked == True` | steps.py:1610-1616 (`step_dock_request`) | dock | N | - |
| `DockingDenied` with Reason via `_docking_denied_reason` supplier | steps.py:1621-1627 | dock | N | - |
| `Docked` event OR `status.docked == True` | steps.py:1675-1681 (`step_dock_await_docked`) | dock | N | - |
| `Status.ScoopingFuel` flag (`scooping_fuel`) | steps.py:1247, 1300, 1306-1309 (`step_scoop_refuel`) | arrival, route\_complete\_park | N | - |
| `Status.Fuel.FuelMain` changing (rate sampling) | steps.py:1288-1295, 1145-1164 | arrival, route\_complete\_park | N | - |
| `Status.OverHeating` flag (bit 20) | dispatcher.py:958-982 (`heat_guard`) | heat watchdog thread (all procedures) | N | - |
| `Scan` event with `scan_type == "AutoScan"` (body name) | dispatcher.py:890-897 (\_apply\_state); steps.py:2009-2019 (body\_tour gate) | arrival (body\_tour, opt-in) | N | Hook: accumulate per-system AutoScan counts for exploration tracking |
| `FSSDiscoveryScan.BodyCount` | dispatcher.py:899-904 (\_apply\_state) | body\_tour min-bodies gate | N | - |
| `SupercruiseExit` seq bump > snapshot (any exit) | steps.py:2015-2017 (body\_tour gate PD7) | arrival (body\_tour, opt-in) | N | - |
| `Loadout` event — `FuelCapacity.Main` + `int_fuelscoop_*` module | dispatcher.py:881-889 (\_apply\_state) | Feeds `_ship_fuel` for `step_scoop_refuel` skip gate | N | - |
| `StartJump` with `jump_type == "Hyperspace"` — `StarClass` field | dispatcher.py:779-785 (\_apply\_state) | Sets `_arrival_star_class` for scoop skip gate in next arrival | N | - |
| `NavRouteClear` event (latch + journal timestamp) | dispatcher.py:850-857 (\_apply\_state) | Arms `_navroute_cleared` for route-complete correlation | N (implicit, required for route-complete) | - |
| `NavRoute` event — re-arm (clears `_navroute_cleared`, caches final waypoint) | dispatcher.py:809-849 (\_apply\_state) | Resets route-complete latch + caches `_final_waypoint` + triggers capture-at-plot | **Y — 22:45:08** (NavRoute while docked → dock_resume) | - |
| `FSDTarget` event — new `system_address` + `star_class` | dispatcher.py:773-777 (\_apply\_state) | Advances `_fsd_target_seq` for `step_target_next_route` danger gate | N | - |
| `Location` event — `StarSystem` field | dispatcher.py:787-790 (\_apply\_state) | Updates `_current_system` (also done by FSDJump) | N | **GAP**: Location(Docked) from a death/rebuy respawn sets system but does NOT clear `_smacked`; see section G |
| `FSDJump` event — `timestamp` field | dispatcher.py:796-801 (\_apply\_state) | Sets `_last_fsdjump_utc` (AWARE-UTC) for jump_age calc and scoop stale-arrival skip | N | - |
| `FsdMassLocked` flag (`status.fsd_mass_locked`) | steps.py:1793-1817 (`step_wait_masslock_clear`) | dock\_resume | **Y — 22:45:08** (fsd_mass_locked=True at dispatch; step clears it) | - |

---

## G. Observed-but-unhandled triggers

Events the game fires that the bot reads into state but never acts on directly (no `dispatch()` call, or the state update is incomplete).

| Trigger | Where it touches the code | What the bot does | Consequence of not dispatching | Candidate hook? |
|---|---|---|---|---|
| `Docked` event (outside a running dock procedure) | dispatcher.py:874-878 (\_apply\_state only) | Sets `_docked=True` and captures station name; **no dispatch** | If the ship docks via player action mid-session, the bot stays in whatever state it was in. It will not idle or service. Only a subsequent NavRoute event (pit-stop trigger) causes any reaction. | Hook: on unexpected Docked (not inside dock procedure), write overlay status "Docked at X — idle until NavRoute" |
| `Location(Docked)` from death/rebuy respawn | dispatcher.py:787-790 (\_apply\_state: Location → `_current_system` only) | Updates `_current_system`; does NOT clear `_smacked`, does NOT update `_docked` | World-state goes stale: `_smacked` retains its pre-death value; `_docked` is False (Undocked is not emitted on respawn). The bot thought it was in Schee Hypa with a 248-jump route while actually docked at Tortooga — the 21:58:46 session start. Gate: the bot only recovered because `_startup_done` had not been set and `_maybe_startup` re-read status. | **KNOWN GAP**: add a `Location(Docked)` branch to `_apply_state` that also sets `_docked=True` and clears `_smacked` |
| `Location(Docked)` — respawn does NOT emit `FSDJump` | n/a | No `_smacked=False` clear | `_maybe_startup` routes to `smack_recovery` on a respawned-docked scene if the last journal SupercruiseExit was a star. The `fsd_cooldown` discriminator (dispatcher.py:1114) saves the clean case (cooldown gone by respawn), but a crash-while-scooping respawn may still carry active cooldown. | Same fix: Location(Docked) should clear `_smacked` |
| `RefuelAll` / `RepairAll` / `BuyAmmo` journal events | steps.py:1736-1740 (`step_station_services` event verify) | Consumed as step-verification only (success log); no state latched on FlowRunner | No fuel/hull/ammo state tracking — the bot has no memory of "did we actually refuel" for the next run | Hook: latch a `_last_refuel_utc` on `RefuelAll` for diagnostics |
| In-system station drop during body\_tour (`SupercruiseDestinationDrop` + `SupercruiseExit`) | dispatcher.py:905-912 (\_apply\_state: seq counters); steps.py:2044-2054 (body\_tour outcome handler) | `_drop_seq` and `_scex_seq` counters advance; body\_tour re-engages SC | Handled within the tour; no dispatch-level hook | - |
| `Interdicted` journal event | not in dispatcher.py or \_apply\_state | **Completely ignored** | An NPC interdiction during a jump burns an FSD charge; the bot keeps holding alignment until `FsdCharging` drops, then retries. In practice the retry recovers, but there is no explicit interdiction handler — the bot cannot submit, cannot escape, cannot distinguish interdiction from a plain charge drop. | Hook: `Interdicted` → `_preempt` the current procedure; could dispatch a dedicated `escape_interdiction` procedure |
| `FuelScoop` journal event (scooping started / rate event) | not present | **Ignored** — `step_scoop_refuel` gates on `Status.ScoopingFuel` flag instead | The journal event would give a higher-resolution trigger than the Status poll cadence; the flag-based approach works but has ~0.5 s latency vs the event | Minor: could replace Status poll with event gate for earlier state detection |
| `HeatDamage` / `HullDamage` journal events | not present | **Completely ignored** | The heat watchdog covers overheating reactively (heatsink ejection); hull damage has no handler. A sustained overheat that doesn't trip the flag in Status would cause hull damage the bot never learns of. | Hook: `HullDamage` / `HeatDamage` → write overlay warning; increment a damage counter; abort if cumulative |
| `Music` event (signals menus / loading) | not present | **Ignored** | No structured way to detect the main menu or loading screen; bot polls journal tail through it | Low priority for this use case |
| `UnderAttack` / `PVPKill` events | not present | **Ignored** | No combat awareness | Low priority for solo AFK run |
| `NavRoute` while NOT docked and `_startup_done == True` | dispatcher.py:545 (gate is `self._docked`) | **Ignored** — player replots a route mid-run while the bot is running a jump | Bot uses `_final_waypoint` cached from the event (`_apply_state:809-849`) and the NavRoute.json file reader; the route completion logic adapts on the NEXT FSDJump. The in-progress procedure is not preempted. | Hook: an operator mid-session re-plot that CHANGES the destination (e.g. adds a new station terminus) should reset `_dock_target`; the code does reset it (dispatcher.py:845-849) but the currently-running procedure doesn't get a re-dispatch signal |

---

## GAPS / UNHANDLED TRIGGERS

A consolidated list of triggers that fire in the game with no bot handler, with the operational consequence:

1. **`Location(Docked)` from respawn** — `_smacked` and `_docked` are not updated; world-state goes stale. A restart after death reads the pre-death scene rather than the real (docked, clear) scene. Consequence: the 21:58:46 Tortooga incident — stale system + stale smack + 248-jump route to nowhere.

2. **`dispatch_route_complete` single Status read** — the Destination is read once at FSDJump time (too early); Status still shows the arrival star (Body=0) before ED updates it to the station target. Consequence: route-complete to a station park instead of dock. Design direction: let the game settle, then read; add a background Destination re-watcher.

3. **Capture-at-plot mechanic unconfirmed** — the code at dispatcher.py:835-849 reads Status.Destination.Body at NavRoute time and caches a station target, but the game mechanic (plot-to-station sets Body!=0 at that instant) has NEVER been confirmed in a live journal. Consequence: `_dock_target` is always None, so the capture-at-plot path in `dispatch_route_complete` is dead code in practice.

4. **`Docked` event outside a procedure** — not dispatched. Consequence: player-initiated or unexpected dock is invisible to the bot until a NavRoute event arrives.

5. **`Interdicted` event** — no handler, no preempt. Consequence: bot can't distinguish an interdiction from a plain charge drop; retries blindly.

6. **No-route docked-on-load** — `_maybe_startup` silently idles on `status.docked == True` (dispatcher.py:1023-1024) even if a route is plotted. Consequence: after a manual dock the bot never launches even when a route is ready; the operator must relaunch. A NavRoute event while docked does trigger `dock_resume`, but only if the bot is already running — not on a fresh launch that happens to be docked with a pre-existing route.

7. **Hull/heat damage events** (`HullDamage`, `HeatDamage`) — no handler. Consequence: no damage awareness; a stuck overheat scenario that doesn't set the Status OverHeating bit is invisible.

8. **`FuelScoop` journal event** — not used; Status.ScoopingFuel flag is polled instead. Minor timing gap only.

---

*Gap count: **8** confirmed unhandled trigger categories.*

*Sources: `projects/ed-autojump/src/ed_autojump/flow/dispatcher.py`, `steps.py`, `procedures/*.toml`, `C:\Users\<user>\ed-afk-sessions\gatewalk_routing_2026-06-09T*.jsonl`.*

---

## GuiFocus reference (operator-confirmed live, 2026-06-09)

Status.json `GuiFocus` values, confirmed by Operator in-game during the gate-walk.
These are the focus touchpoints the dock / undock / galmap procedures read or await.

| GuiFocus | Screen | Notes |
|---|---|---|
| 0 | No map / main view | default flying |
| 1 | Right-hand panel | internal / systems (candidate for future use) |
| 2 | Left-hand panel | NAV + CONTACTS — the docking-request panel |
| 5 | Starport Services | docked services menu |
| 6 | Galaxy map | route plotting |
| (unchanged) | Advanced Maintenance screen | does NOT change GuiFocus -> OCR/vision only |

UNDOCK (operator play-by-play, NOT yet wired in code): AUTO LAUNCH = (S) down once
from home position, then Spacebar to submit; autodock ends ~10 km from the station.
OPEN QUESTION (observe in the undock test): does this docked menu change GuiFocus,
or is it invisible like Advanced Maintenance? If invisible, the bot must locate
AUTO LAUNCH by OCR/vision, not by awaiting a focus value.

---

## UNDOCK touchpoints (operator-confirmed live, 2026-06-09)

Manual undock at Tortooga, keys OFF, observed via gate-walk trace + Status poll.

- AUTO LAUNCH = (S) down once from the docked home position, then Spacebar. Does
  NOT change GuiFocus (stays 0, like Advanced Maintenance) -> the bot must OCR /
  blind-macro it, it cannot await a focus value.
- Autodock fly-out drops the ship at ~3.85 km from the station (NOT 10 km),
  throttle 0, normal space. DockingComputer music runs ~58 s (Undocked 23:11:37 ->
  NoTrack 23:12:35) marking the autodock disengage.
- Ship is STILL FsdMassLocked at 3.85 km (Status Flags bit16=65536, confirmed) ->
  cannot FSD-jump. This is WHY the reference says "throttle to 10.1km, orient, jump"
  -- the throttle-out exists to CLEAR the station mass-lock, not for distance itself.
- UNDOCK JUMP GATE (design): FsdMassLocked flag CLEARS (a Status flag, compliant) --
  NOT a hardcoded 10 km. Throttle out until the flag drops, then orient + jump.
- Bot dispatches NOTHING on Undocked (no Undocked branch in dispatch; checklist
  S4 r4). The undock procedure is unbuilt; this is the spec to build it from.
- MASS-LOCK SOURCE caveat vs memory ed-fsd-masslock-realspace ("mass lock only from
  other ships"): a STATION at 3.85 km appears to mass-lock too. Source not yet proven
  vs a nearby NPC -- confirm before amending that memory. Jump-gate-on-flag holds regardless.

---

## UNDOCK spec refinement (operator, 2026-06-09) — egress is blind + mass-lock-gated

- Drop distance VARIES by station; some may NOT drop at ~3.85 km. The only reliable
  signals are: DockingComputer music STOPS (autodock disengage) and FsdMassLocked is
  present in ALL cases.
- Egress is BLIND and STRAIGHT: thrust 100% + thruster BOOST (B) after the 100%,
  heading straight away from whatever we undocked from. NO turning / vision / orient
  during egress -- turning could point back into the mass-lock zone.
- Sequence: exit mass lock (FsdMassLocked flag CLEARS) -> WAIT 5 s (maneuver buffer to
  gain distance so orienting back does not re-enter mass-lock; a maneuver DURATION,
  not a success gate) -> orient -> jump.
- Rationale: if the destination is BEHIND us, turning back before clearing mass-lock +
  the 5 s buffer would re-enter mass-lock and wreck the jump.

---

## DOCKED SERVICES — pit-stop macro (operator, 2026-06-09)

On confirmed DOCKED (the `Docked` event / docked flag), WAIT 2 s for the services
menu to fully materialize (a UI-settle DURATION, not a success gate -- the gate is
DOCKED), then the blind key sequence:

    W, SPACE, D, SPACE, D, SPACE, S

Effect (operator-stated): refuel, repair, rearm, then reset cursor (the trailing S).
Mapping: W=up, D=right, SPACE=select, S=down/reset. Produces the RefuelAll /
RepairAll / BuyAmmo journal events (see catalog section G). This is the spec for the
dock flow's "service" step (dock.toml ends at dock_await_docked; servicing is unbuilt).

---

## DOCKED MENU — CV slot calibration (live 1920x1080 GDI capture, 2026-06-09)

The docked dashboard menu's HIGHLIGHTED item = one solid bright-orange bar
(x ~784-790, w ~345, h ~40-46, mean RGB ~(165,87,1)). Identify which item is
highlighted by the bar's vertical CENTRE:

  STARPORT SERVICES (top)    ycentre ~822   (bar y=803 h=39)
  AUTO LAUNCH       (middle) ycentre ~873   (bar y=852 h=42)
  DISEMBARK         (bottom) ycentre ~925   (bar y=902 h=46)

~51px even spacing -> decision boundaries y~847 (SERVICES|AUTO) and y~899
(AUTO|DISEMBARK); +/-20px tolerance is clean. NO bar in the region = menu not up.
Detector region: x[760..1160] y[795..955]. Fixtures: tests/fixtures/station_menu_*_live.png.

USES: undock confirms the bar is on AUTO LAUNCH before pressing select;
service-test-1 confirms a bar is present (menu up) before the W,SPACE,D,... macro.
NOTE: pasted-screenshot frames arrive DOWNSCALED (1880x1000); these coords are
from the bot's own 1920x1080 capture path -- calibrate/run against that path, not pastes.

---

## DOCK BLIND-MANEUVER — pitch timing scales with ship size (operator, 2026-06-09)

The blind-maneuver PITCH duration scales with ship AGILITY (a slow ship -- e.g. a
Type-9 -- needs more pitch time to clear the star before throttle/orient). Operator rule:
  LARGE-class  ship -> pitch 7s
  MEDIUM-class ship -> pitch 4s
  SMALL-class  ship -> pitch 3s
Throttle 100% stays 7s; orient + SC-assist unchanged. Exact timings may be refined.
Ship MODEL is detectable from the journal (LoadGame/Loadout "Ship" field, e.g.
"mandalay"); map model -> size class via a table (Mandalay = MEDIUM -> 4s). This
same maneuver is also the SC-assist-disengaged recovery.
