# ED-AFK — Action tables (every scene, as wired on `master`)

Operator evaluation lens (requested 2026-07-03: "every major set of actions in
labeled tables so i can evaluate if we got all the basics correct").
**Reconciled against the live `*.toml` procedures + the `register_step(...)` registry
on `master` (2026-07-12).** This is what the bot actually runs, not a design sketch.

> **Canonical action reference is [`procedures/procedures.md`](../projects/ed-autojump/procedures/procedures.md)** —
> it owns the full ~46-action list with every param, the ORPHANED/LEGACY marks, the
> control-flow keys, and the routing internals. This file is the *evaluation view*:
> per-scene step order with Kind / Req / Gate columns. When the two disagree,
> procedures.md + the `*.toml` files win. Project maturity, licensing, and the ToS
> warning live in the [root README](../README.md).

**Maturity (do not overclaim):** the steady-state **jump loop** (arrival → traversal)
is LIVE-VALIDATED over hundreds of consecutive jumps (a cross-galaxy Colonia run).
**Docking and the rarer recovery paths** (smack, sc_resume, dock_resume,
connection_recovery, exploration) are STILL UNDER LIVE VALIDATION. It is ALPHA with
open edges that can occasionally strand a run.

Step kinds: **tap** atomic keypress · **gated** keypress with Status/journal
precondition (fails closed) · **wait** passive block on journal event / Status flag /
CV poll · **macro** multi-key UI sequence, EXCLUSIVE (owns input) · **vision** CV
read/steer, EXCLUSIVE · **observe** reads screen, presses nothing, never gates · **hold**
key down → event → key up.

`req` = required (a terminal fail aborts the procedure via its `[on_required_fail]`
policy). All event gates carry an already-true state fallback
([[event-gates-need-state-check]]). Bare `wait s=N` steps are operator pacing, NOT
success gates.

---

## 0 · When each scene runs (dispatch map)

There are 11 procedures. Routing is done by three dispatch layers (boot classifier,
event routes, section orchestrator) — never by a step. See procedures.md → Routing,
and `boot_routes.py` / `dispatcher.py`.

| Trigger | Scene |
|---|---|
| Live `FSDJump`, route NOT complete | **Arrival** → orchestrator successor branch |
| Live `FSDJump`, `_is_route_complete` (NavRouteClear latch) | **Route-complete branch** (park vs dock) |
| Live `NavRoute` (non-empty) while docked | **Dock_resume** (pit-stop) |
| `SupercruiseExit` at **Star/Planet** | **Smack recovery** — ALWAYS-RECOVER (D2/C2 council 2026-07-07; the old CV-gated abstain is REPEALED) |
| **CONNECTION ERROR** modal (OCR by the watch daemon; NO journal event) | **Connection recovery** |
| Bot start: docked | idle (nothing to escape) — or dock_resume if a route is already plotted |
| Bot start: in SC, near / fresh / indeterminate | **Arrival** (restart-in-SC = arrival scene) |
| Bot start: in SC, stale + confident non-local-star lock | **SC resume** |
| Bot start: normal space, smacked + FSD cooldown | **Smack recovery** |
| Bot start: normal space, route plotted | **Startup** (no route → clean idle `[NO ROUTE]`) |
| Every arrival (parallel track, arrival-owned) | **Honk** |

**Real-time monitor preempts** (dispatcher, mid-procedure — abort the running scene at
the next poll, then dispatch): `FSDJump` → `new_system` → **arrival**; `SupercruiseExit`
Star → `star_smack` → **smack recovery**; boot escape-vector → `escape_vector` →
**smack recovery**; CONNECTION ERROR → **connection recovery**. The never-strand
`redispatch_from_live_state` driver re-classifies from scratch on any abort, so the
bot never dead-ends.

**Arrival → successor branch** (`run_arrival_then_branch` → `_arrival_branch`, not a step):

| Condition (precedence order) | Next scene |
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

## 1 · Arrival (`arrival.toml`) — every live jump. Retry: `set_throttle` **×1**

Honk rides in parallel. The jump tail is GONE — traversal owns the onward jump
(double-jump fix). Arrival does three things then HANDS OFF to the branch.

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `set_throttle 0` | tap | | — | in case auto-dethrottle off |
| — | *(honk track)* | hold | | `FSSDiscoveryScan` release | parallel, non-blocking |
| 2 | `scoop_refuel` | gated | | fuel < **0.69** AND star KGBFOAM; rate/budget/stale-arrival guards | best-effort pit stop |
| 3 | `nav_supercruise_star` | macro/vision | req | CV row-0 confirm + POI veto → SC-assist button-bar walk | replaces `sc_assist_orbit`+`nav_panel_target`; retry re-settles the pose |
| — | *branch* | — | | see dispatch map | orchestrator, not a step |

> `star_distance_gate` (skip the get-around when the star is confirmed FAR, ≥15 Ls) is
> present but **commented out** in `arrival.toml` (line ~61) — not currently active.

## 2 · Exploration (`exploration.toml`) — body tour. Retry: `target_next_route`

Loop is the scene's job (`loop_to` back-edge, `loop_max=128`); read failure exits the
loop and jumps on (never drives blind). Chains to its OWN terminal jump tail (the old
exploration→traversal chain was removed — it double-pressed the jump keys).

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `wait 7s` | wait | | operator pacing | |
| 2 | `nav_supercruise_unexplored` | macro/vision | | first UNEXPLORED nav row; `✦` system icon = terminator | **LOOP HEAD**; False → `skip_to target_next_route` (tail) |
| 3 | `set_throttle 25` | tap | | — | close on the body |
| 4 | `orient_compass` | vision | | compass CV | coarse; miss never blocks |
| 5 | `orient_widget_ring` | vision | req | widget CV | fine |
| 6 | `confirm_sc_assist_active` | observe | | HUD prompt CV | logs, never gates |
| 7 | `wait_sc_assist_orbiting` | wait | | cyan ORBITING prompt (bounded poll) | best-effort |
| 8 | `confirm_orbiting` | observe | | ORBITING prompt | logs |
| 9 | `wait_body_scanned` | wait | | **AutoScan** seq-advance (BK-1: confirm live) | between-body edge |
| 10 | `set_throttle 0` → **loop_to #2** | tap | | `loop_max=128` budget | back-edge |
| 11-17 | jump tail | | | `target_next_route` → 75 → wait → orient ×2 → 100 → `engage_jump_clearance` | terminal; same shape as traversal tail |

## 3 · Traversal (`traversal.toml`) — the steady-state hop. Retry: `target_next_route` ×3

Arrival hands off already SC-assisting the star, so traversal opens by confirming the
orbit, then locks the next hop and jumps. No honk (arrival owns it).

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `wait 3.33s` | wait | | pacing | |
| 2 | `wait_sc_assist_orbiting` | wait | | ORBITING prompt (bounded poll) | inherited orbit |
| 3 | `confirm_orbiting` | observe | | ORBITING prompt | logs |
| 4 | `wait 13s` | wait | | pacing | clear off the star |
| 5 | `target_next_route` | gated | req | new `FSDTarget` (dangerous class NOTED not refused) | retry anchor |
| 6 | `set_throttle 100` | tap | | — | full burn |
| 7 | `wait 3.33s` | wait | | pacing | settle |
| 8 | `set_throttle 75` | tap | | — | slower for CV align |
| 9 | `orient_compass` | vision | req | compass CV | coarse |
| 10 | `orient_widget_ring` | vision | req | widget CV | fine |
| 11 | `set_throttle 100` | tap | | — | re-assert burn |
| 12 | `engage_jump_clearance` | gated | req | charge-aware clearance loop → `StartJump` | terminal; obstruction/malfunction handled in-step |

## 4 · Park on star (`route_complete_park.toml`) — SYSTEM terminus. Retry: `nav_supercruise_star` ×3

Ends parked, bot IDLE. No jump tail.

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `set_throttle 0` | tap | | — | |
| 2 | `scoop_refuel` | gated | | fuel < **0.99** (destination top-off) | best-effort |
| 3 | `nav_supercruise_star` | vision | req | CV row-0 confirm → SC-assist | lock + engage in one action |
| 4 | `confirm_orbiting` | observe | | ORBITING prompt | non-blocking |

## 5 · Dock at station (`dock.toml`) — STATION terminus. Retry: `dock_close_to_range` ×3

SC-assist DROPS at a station (game outcome). Close-range gate is the RIGHT-SIDE
target-panel km CV — **no NoFireZone gate anywhere** (the NFZ is larger than 7.5 km and
never a valid docking-range signal).

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `nav_supercruise_target` | macro/vision | req | OCR name-match station row → SC-assist | |
| 2 | `dock_await_exit` | wait | req | `SupercruiseExit` (fallback: already dropped) | |
| 3 | `set_throttle 100` | tap | | — | closing thrust |
| 4 | `dock_close_to_range` | vision | req | **CV km < 7.5 km, target panel**; ram-guard zeros throttle on exit | poll-count backstop is fail-only |
| 5 | `set_throttle 0` | tap | | — | |
| 6 | `dock_request` | macro | req | literal request tail (E,E,D,space) + throttle 0 + close panel; gate `DockingGranted`/`DockingDenied(Distance)`→retry | EXCLUSIVE |
| 7 | `dock_await_docked` | wait | req | `Docked` (fallback: `status.docked`) | autodock flies |
| 8 | `station_services_macro` | macro | | blind W,Space,D,Space,D,Space,S; docked-menu detector gates arm | pit-stop refuel/repair/rearm |

## 6 · Undock / pit-stop resume (`dock_resume.toml`). Retry: `target_next_route` ×3

Runs when a NEW route arrives while docked. Launch is not in the retry lane.

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `auto_launch` | macro | req | `Undocked` / `status.docked→False` | S,S,Space + CV seek |
| 2 | `set_throttle 100` | tap | | — | **fly STRAIGHT out at top speed** (operator) |
| 3 | `wait_masslock_clear` | wait | req | `FsdMassLocked` (Status bit 16) CLEARING — game's own >~10 km signal | no timer; fails closed |
| 4-9 | jump tail | | | `target_next_route` → 75 → orient ×2 → 100 → `engage_jump_clearance` | same shape as traversal tail |

## 7 · Startup (`startup.toml`) — cold start, normal space. Retry: `star_distance_gate` ×3

Two lanes on a CV distance gate. Retries re-run the gate — no retry may bypass it into
the burn lane (the run-010444 L 32-8 fix). Honk in parallel.

> **Stale in-file comment:** the header says "throttle 100 FIRST" but step 1 is
> `set_throttle 0`; the burn ramps 75 → 100 later in the CLOSE lane. Threshold is **15 Ls**,
> not the "100 Ls" the prose mentions.

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `set_throttle 0` | tap | | — | |
| 2 | `star_distance_gate` | vision | | OCR row-0 distance; <15 Ls or UNREADABLE → CLOSE lane; ≥15 Ls → `skip_to target_next_route` | fail-closed to CLOSE; two agreeing reads = FAR |
| 3 | `set_throttle 75` | tap | | — | CLOSE lane |
| 4 | `pitch_star_off` | vision | req | centre-crop brightness clears (sun-avoid) | |
| 5 | `set_throttle 100` | tap | | — | ED refuses SC entry at zero throttle (live 2026-07-06) |
| 6 | `engage_supercruise` | gated | req | `SupercruiseEntry`; `escape_vector_abort=true` → hand off to smack_recovery on a boot escape-vector | |
| 7 | `set_throttle 0` | tap | | — | settle before the lock |
| 8 | `nav_supercruise_star` | vision | req | CV row-0 confirm → SC-assist | |
| 9-11 | `wait 3.33` · `wait_sc_assist_orbiting` · `wait 13` | wait | | ORBITING prompt + pacing | |
| 12 | `target_next_route` | gated | req | new `FSDTarget` | FAR lane lands here; cancels assist |
| 13-19 | jump tail | | req | 100 → wait → 75 → orient ×2 → 100 → `engage_jump_clearance` | shared burn |

## 8 · SC resume (`sc_resume.toml`) — restart mid-SC, stale. Retry: `star_distance_gate` ×3

Like startup minus SC entry (already in SC). The in-scene gate is live-proven necessary
(session_100951: a nose-on-star restart carried a non-local-star Destination and was
misclassified as a clear loiter).

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `set_throttle 0` | tap | | — | |
| 2 | `star_distance_gate` | vision | | OCR row-0 distance; <10 Ls or UNREADABLE → CLOSE lane; ≥10 Ls → `skip_to target_next_route` (fast resume) | |
| 3 | `set_throttle 75` | tap | | — | CLOSE lane |
| 4 | `pitch_star_off` | vision | req | brightness clears | |
| 5 | `nav_supercruise_star` | vision | req | CV row-0 → SC-assist | already in SC (no SC entry step) |
| 6-9 | `wait 3.33` · `wait_sc_assist_orbiting` · `confirm_orbiting` · `wait 13` | wait/observe | | ORBITING prompt + pacing | |
| 10 | `target_next_route` | gated | req | new `FSDTarget` | fast-resume lands here |
| 11-17 | jump tail | | req | 100 → wait → 75 → orient ×2 → 100 → `engage_jump_clearance` | shared burn |

## 9 · Smack recovery (`smack_recovery.toml`) — v8 ALL-CV. Retry: `set_throttle`, or `target_next_route` if in SC

Entry is ALWAYS-RECOVER (no CV/`smack_kind` gate — D2/C2 council). The v7 blind
star-lock escape is DEAD; the escape vector is a WORLD-SPACE cyan sky marker, not a
compass element. Honk in parallel.

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `set_throttle 75` | tap | | — | burn through the flip (real space can't ram a star) |
| 2 | `pitch_star_off` | vision | req | centre-crop brightness clears | put the star off-screen |
| 3 | `wait_cooldown_clear` | wait | req | `FsdCooldown` flag clears | smack cooldown |
| 4 | `engage_supercruise` | gated | req | LIVE charge (until_charging, ≤3 presses ×15 s, 240 s watchdog) | re-press only if no charge (re-press cancels a live charge) |
| 5 | `set_throttle 100` | tap | | — | |
| 6 | `orient_escape_vector` | vision | req | centre the cyan sky marker; sphere-sweep search on miss → `SupercruiseEntry` | the load-bearing recovery step |
| 7 | `nav_supercruise_star` | vision | req | CV row-0 → SC-assist | now in SC, get-around the star |
| 8-11 | `wait 3.33` · `wait_sc_assist_orbiting` · `confirm_orbiting` · `wait 13` | wait/observe | | ORBITING prompt + pacing | |
| 12 | `target_next_route` | gated | req | new `FSDTarget` | **retry_anchor** (SC-segment anchor) |
| 13-19 | jump tail | | req | 100 → wait → 75 → orient ×2 → 100 → `engage_jump_clearance` | shared burn |

## 10 · Honk (`honk.toml`) — parallel track (`parallel=true`), every arrival

| # | Action | Kind | Req | Gate / signal |
|---|---|---|---|---|
| 1 | `ensure_analysis_mode` | gated | req | ANALYSIS HUD (PrimaryFire in combat HUD = live weapons) — load-bearing, first |
| 2 | `hold_until_event PrimaryFire` | hold | | release on `FSSDiscoveryScan` (~5 s); `max_hold_s≈30` key-release backstop |

## 11 · Connection recovery (`connection_recovery.toml`) — server-drop modal

Dispatched by the connection-watch daemon (OCR on a full-frame grab), NOT an event
route — the CONNECTION ERROR modal carries no journal event. No honk (would fight the
menu input), no `on_required_fail`; after it returns the live loop re-classifies from
the fresh state.

| # | Action | Kind | Req | Gate / signal | Notes |
|---|---|---|---|---|---|
| 1 | `connection_recovery` | macro | | operator-verified menu macro: OK → main menu → CONTINUE → **Solo** (automatons can't play Open) → load real-space → galaxy-map re-plot the saved route | best-effort |

---

## Action inventory delta (registry vs live procedures)

The registry defines **~46 actions** (`register_step(...)` in `steps_shared.py` +
`steps.py` + `steps_body_tour.py`); the live procedures use a subset. Params and full
descriptions are in procedures.md — this is just the wired/orphaned split.

**NEW actions the current flow runs** (the CV/OCR rebuild): `engage_jump_clearance`
(replaces `engage_jump`+`hold_alignment`), `nav_supercruise_star` / `nav_supercruise_unexplored`
/ `nav_supercruise_target`, `star_distance_gate`, `pitch_star_off`, `orient_escape_vector`,
`orient_widget_ring`, `scoop_refuel`, `wait_sc_assist_orbiting`, `confirm_orbiting`,
`confirm_sc_assist_active`, `wait_body_scanned`, `wait_cooldown_clear`, `wait_masslock_clear`,
`dock_close_to_range`, `dock_await_exit`, `dock_request`, `dock_await_docked`,
`station_services_macro`, `auto_launch`, `connection_recovery`.

**ORPHANED / LEGACY** (registered + callable but referenced by NO current `*.toml`):

| Orphaned action | Superseded by |
|---|---|
| `engage_jump`, `hold_alignment` | `engage_jump_clearance` (charge-aware loop folds both in) |
| `sc_assist_orbit`, `nav_panel_target`, `nav_target_star` | `nav_supercruise_star` family (CV row-0 confirm) |
| `pitch_compass` | `pitch_star_off` (brightness) + `orient_escape_vector` (sky marker) |
| `dock_target_station`, `dock_sc_assist`, `dock_approach`, `station_services`, `dock_blind_maneuver`, `confirm_menu_item` | live dock tail: `nav_supercruise_target` → `dock_close_to_range` → `dock_request` → `dock_await_docked` → `station_services_macro` |
| `reset_power_distribution`, `pips_engines` | pip management scrapped (2026-06-08) |
| `target_ahead`, `pitch`, `press` | primitives / old blind lanes — kept as building blocks |
| `body_tour` (ed_explore) | `nav_supercruise_unexplored` (the live `exploration.toml` uses this instead) |

**Deleted / banned:** `wait_for_event`, `wait_cooldown` — a duration as a success/failure
gate is banned. Use the event/state-gated waits above.

---

## Known gaps / edges (honest list)

1. **BK-1** — `wait_body_scanned` gates on `AutoScan` seq-advance; AutoScan-vs-Scan as
   the per-body edge is unconfirmed live (built as spec'd, live-adjustable).
2. **Smack ALWAYS-RECOVER supersedes the old abstain** — the 2026-07-06 `cv_unwired`
   abstain that idled the bot after the L 32-8 smack is REPEALED (D2/C2 council
   2026-07-07): `_route_sc_exit` and the boot smacked-branch now recover unconditionally.
   The escape-vector CV grabber is wired in `activate()`; if it degrades to None the
   never-strand driver still re-dispatches rather than stranding.
3. **C2 missed-latch fallback** — if `NavRouteClear` is MISSED, a terminal SYSTEM
   arrival could branch "docking" instead of park. Primary latch path is correct and
   live-witnessed. Bounded, known.
4. **Capture-at-plot unconfirmed** — a plot-to-station setting `Destination.Body!=0` at
   `NavRoute` time has never been observed live; station docking rides the settle-re-poll
   path. Docking is under live validation.
5. **Per-ship CV regions** — Mandalay-only by design (recalibrate on hull swap).
6. **Arrival `star_distance_gate`** — present but commented out; arrival always runs the
   SC-assist get-around even for a far arrival star (a cheap over-do, never unsafe).
