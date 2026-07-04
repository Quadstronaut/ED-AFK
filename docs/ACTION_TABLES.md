# ED-AFK — Action tables (every major scene, as wired on master)

Operator evaluation deliverable (requested 2026-07-03: "every major set of actions in
labeled tables so i can evaluate if we got all the basics correct").
Transcribed from the LIVE procedure files on `master @ e90d122` (2026-07-04) — this is
what the bot actually runs, not a design sketch.

Step kinds: **tap** atomic keypress · **gated** keypress with Status/journal precondition
(fails closed) · **wait** passive block on journal event / Status flag · **macro** multi-key
UI sequence, EXCLUSIVE (owns input) · **vision** CV read/steer, EXCLUSIVE · **observe**
reads screen, presses nothing, never gates · **hold** key down → event → key up.

`req` = required (a terminal fail aborts the procedure via its retry policy).
All event gates carry an already-true state fallback ([[event-gates-need-state-check]]).

---

## 0 · When each scene runs (dispatch map)

| Trigger | Scene |
|---|---|
| Live `FSDJump`, route NOT complete | **Arrival** → orchestrator branch |
| Live `FSDJump`, `_is_route_complete` (NavRouteClear latch) | **Route-complete branch** (park vs dock) |
| Bot start: docked + new route plotted while docked | **Undock / pit-stop resume** |
| Bot start: in SC, dest = local star or fresh jump (≤ window) | **Arrival** (restart-in-SC = arrival scene) |
| Bot start: in SC, stale + confident non-local-star lock | **SC resume** |
| Bot start: main menu / normal space etc. | **Startup** |
| `SupercruiseExit` at Star/Planet + escape-vector CV token | **Smack recovery** (CV fail-closed; abstains unwired) |
| Every arrival (parallel track) | **Honk** |

**Arrival → successor branch** (orchestrator `run_arrival_then_branch`, not a toml step):

| Condition (in precedence order) | Next scene |
|---|---|
| destination == current system (terminal arrival) | Docking section |
| exploration active (`[exploration].body_tour_enabled`) | Exploration |
| else | Traversal |

**Route-complete branch** (`dispatch_route_complete`, D1 discriminator, fails closed to park):

| Destination read | Scene |
|---|---|
| `Body != 0` AND `Name != current-system` (a STATION) | Dock (station) |
| `Body == 0`, or Name == system, or unread/ambiguous | Park on star |

---

## 1 · Arrival (`arrival.toml`) — every live jump

Honk rides in parallel. Retry: from `scoop_refuel`, ×3. Jump tail is GONE — Traversal owns
the jump (double-jump fix, #1).

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `set_throttle 0` | tap | | — | in case auto-dethrottle off |
| 2 | *(honk track)* | hold | | `FSSDiscoveryScan` release | parallel, non-blocking |
| 3 | `scoop_refuel` | gated | | fuel < **0.50** AND star KGBFOAM (`StartJump StarClass`); rate/budget guards | best-effort pit stop |
| 4 | `nav_supercruise_star` | macro | req | CV #8 label confirm BEFORE press; verify `Status.Destination` | row-0 blind-fire SC-assist; replaces `sc_assist_orbit` + `nav_panel_target` |
| 5 | *branch* | — | | see dispatch map | orchestrator, not a step |

## 2 · Exploration (`exploration.toml`) — body tour, NEW

Loop is the scene's job (`loop_to` back-edge); no required steps (fail = jump on, never
drive blind); NO jump step here. Chains UNCONDITIONALLY → Traversal.

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `nav_supercruise_unexplored` | macro | | CV: first UNEXPLORED row; `✦` system icon = terminator | LOOP HEAD; False → skip_to exit |
| 2 | `set_throttle 100` | tap | | — | close on the body |
| 3 | `orient_compass` | vision | | compass CV | coarse only; miss never blocks |
| 4 | `confirm_sc_assist_active` | observe | | HUD prompt CV | LOGS ScHudState, never gates |
| 5 | `wait_body_scanned` | wait | | **AutoScan seq advance** (persistent high-water baseline + poll-count backstop) | BK-1: confirm AutoScan is the right per-body edge live |
| 6 | `set_throttle 0` → **loop_to #1** | tap | | `loop_max = 64` budget | back-edge |
| 7 | `target_next_route` | gated | | — | exit landing; Traversal re-locks required |

## 3 · Traversal (`traversal.toml`) — the one jump lane

Retry: from `target_next_route`, ×3.

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `wait 5s` | wait | | operator-written pacing | before the lock |
| 2 | `target_next_route` | gated | req | route has next hop | retry anchor |
| 3 | `set_throttle 100` | tap | | — | full burn through jump |
| 4 | `wait 3s` | wait | | operator-written pacing | settle before orient |
| 5 | `orient_compass` | vision | req | compass CV; fails closed unwired | coarse |
| 6 | `orient_widget_ring` | vision | req | widget CV (no-op if flag off) | fine |
| 7 | `engage_jump_clearance` | gated | req | mass-lock/obstruction clearance loop → `StartJump` | terminal; witchspace pause |

## 4 · Park on star (`route_complete_park.toml`) — SYSTEM terminus

Retry: from `nav_supercruise_star`, ×3. Ends parked, bot IDLE.

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `set_throttle 0` | tap | | — | |
| 2 | `scoop_refuel` | gated | | fuel < **0.99** (destination top-off) | best-effort |
| 3 | `nav_supercruise_star` | vision | req | CV #8 label confirm | lock + engage in one action |
| 4 | `confirm_orbiting` | vision | | ORBITING HUD prompt | observational; non-blocking |

## 5 · Dock at station (`dock.toml`) — STATION terminus

Retry: from `dock_close_to_range`, ×3 (re-approach on Distance denial). **No NoFireZone
gate anywhere.** Boost DROPPED (operator 2026-07-04).

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `nav_supercruise_target` | vision | req | name-match station row + CV #8 confirm | SC-assist DROPS at a station (game outcome) |
| 2 | `dock_await_exit` | wait | req | `SupercruiseExit` (fallback: already dropped) | |
| 3 | `set_throttle 50` | tap | | — | closing thrust |
| 4 | `dock_close_to_range` | vision | req | **CV km read < 7.5 km, right-side target panel**; unread ≠ in range; throttle-0 ram-guard on every exit | poll-count backstop is fail-only |
| 5 | `dock_request` | macro | req | tail **E→½s→E→½s→D→space→throttle 0** (operator literal, from closed panel); gate `DockingGranted` / `DockingDenied` | on GRANT: throttle 0 + **'1'** closes panel (operator 2026-07-03); denial/watchdog also close for retry |
| 6 | `dock_await_docked` | wait | req | `Docked` (fallback: `status.docked`) | autodock flies |
| 7 | `station_services_macro` | macro | | blind-fire W/Space·D/Space·D/Space; events confirm-when-applicable | RETAINED (operator); grayed = already full = success |

## 6 · Undock / pit-stop resume (`dock_resume.toml`)

Runs when a NEW route arrives while docked. Retry: from `target_next_route`, ×3
(launch is not in the retry lane). Log-driven end-to-end.

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `auto_launch` | macro | req | `Undocked` / `status.docked→False` | S,S,Space menu macro, CV seek gate |
| 2 | `set_throttle 100` | tap | | — | **fly STRAIGHT out at top speed** (operator 2026-07-03) |
| 3 | `wait_masslock_clear` | wait | req | `FsdMassLocked` (Status bit 16) CLEARING — game's own >~10 km signal | no timer; fails closed w/o Status |
| 4-8 | jump leg | | | same shape as Traversal 2-7 (`engage_jump_clearance` re-checks mass-lock) | |

## 7 · Startup (`startup.toml`) — cold start, normal space *(old flow, not yet CV-rewired)*

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `nav_panel_target` (max_rows 3) | macro | | star found CLOSE → get-around; FAR → skip to 6 | clear-of-star distance gate |
| 2 | `engage_supercruise` | gated | req | `SupercruiseEntry` | close-star lane |
| 3 | `nav_panel_target` | macro | | — | re-lock star |
| 4 | `sc_assist_orbit` | macro | | — | get-around orbit |
| 5 | `wait 13s` | wait | | operator pacing | orbit acquire |
| 6 | `target_next_route` | gated | req | — | shared continuation |
| 7-10 | burn → orient ×2 → `engage_jump` → `hold_alignment` | | req | `StartJump` | + recovery lane 11-23 (retry anchor at 19) |

## 8 · SC resume (`sc_resume.toml`) — restart mid-SC, stale *(old flow)*

Same clear-of-star gate (1-3: `nav_panel_target` max_rows 3 → `sc_assist_orbit` → 13s) then
`target_next_route`(req) → burn → orient ×2 (req) → `engage_jump`(req) → `hold_alignment`(req).

## 9 · Smack recovery (`smack_recovery.toml`) — LOCKED LAW (operator 8-step)

Entry is CV-gated (escape-vector token at `SupercruiseExit`; abstains if CV unwired —
never a blind smack call). Real-space: throttle 0 → `nav_panel_target`(req) → throttle 75 →
`pitch_compass until=behind`(req, glare guards) → `wait_cooldown_clear`(req, FsdCooldown bit)
→ `target_ahead` → `engage_supercruise`(req, until_charging, re-press ≤3×15 s) → throttle 100
→ `target_next_route`(req) → `orient_compass`(req, escape-vector dot) → `hold_alignment
until SupercruiseEntry`(req). In SC: hop lock(req, anchor) → burn → 13 s clear → orient ×2(req)
→ `engage_jump`(req) → `hold_alignment`(req).

## 10 · Honk (`honk.toml`) — parallel track, every arrival

| # | Action | Kind | Req | Gate / signal |
|---|---|---|---|---|
| 1 | `ensure_analysis_mode` | gated | req | ANALYSIS HUD (PrimaryFire in combat HUD = weapons) |
| 2 | `hold_until_event PrimaryFire` | hold | | release on `FSSDiscoveryScan` (~5 s); 30 s backstop |

---

## Known gaps / edges (honest list)

1. **BK-1** — `wait_body_scanned` gates on AutoScan seq; AutoScan-vs-Scan as the per-body
   edge is unconfirmed live (built as spec'd, live-adjustable).
2. **C2 missed-latch fallback** — if NavRouteClear is MISSED, a terminal arrival branches
   "docking" → dock.toml even for a SYSTEM destination (primary latch path is correct and
   live-witnessed → park). Edge, bounded, known.
3. **Capture-at-plot unconfirmed** — plot-to-station setting `Destination.Body!=0` at
   NavRoute time has never been observed live; station docking rides the settle-re-poll path.
4. **#26 honk detach** — dispatcher still `join(timeout=15s)` on parallel tracks.
5. **#12 rename** — `steps.py` `_FRESH_ARRIVAL_WINDOW_S` name-collision with boot's 30 s.
6. **Startup / SC-resume still OLD blind flows** (`nav_panel_target`/`sc_assist_orbit`/13 s
   waits live only here) — rewire to CV actions = the remaining departure work.
7. **Smack #9** — escape-vector-presence ≠ smack (operator question) still open; one
   pre-existing red test (`test_startup_smacked_with_live_cooldown_runs_smack_recovery`).
8. **Per-ship CV regions** — Mandalay-only by design (#16 deferred until all functions
   operator-approved).
9. **First live run** needs the operator at the game (function-by-function approval).
