# Procedure DSL reference

Each `*.toml` in this directory is one **procedure**. A procedure is a list of
**steps**. A step is an inline table with one required key — `action` — plus
whatever params that action takes, plus optional control-flow keys (`required`,
`skip_to`, `loop_to`/`loop_max`, `retry_anchor`).

```toml
steps = [
  { action = "wait", s = 1.5 },
  { action = "orient_compass", required = true },
]
```

The loader discovers a file by directory glob (`loader.py`), so **dropping a
`*.toml` in this directory IS the registry wiring** — there is no separate
manifest. The filename stem is the procedure name.

---

## Procedure inventory

The 11 procedures that exist and can be called, and how each is reached. There is
NO goto/chaining primitive inside a procedure — inter-scene hand-off is done by
the dispatch layer (see **Routing** below), never by a step.

| Procedure | When it runs | Steps | honk track | retry anchor |
|---|---|---|---|---|
| **arrival** | Every LIVE `FSDJump` (non-terminal) and an in-SC restart. Throttle 0 → scoop-if-low → SC-assist the row-0 arrival star → HAND OFF. The jump tail was stripped (the double-jump fix); the successor scene is chosen by `_arrival_branch` **after** the proc returns. | 3 | yes | `set_throttle` (×1) |
| **traversal** | The steady-state A→B hop (default onward branch). Orbit-settle → lock next hop → orient (compass+widget) → `engage_jump_clearance`. | 12 | no (arrival owns honk) | `target_next_route` (×3) |
| **smack_recovery** | Emergency drop inside a star/planet exclusion zone (star-smack), plus the boot-cooldown / escape-vector-at-boot overrides. Pitch star off → re-charge SC → ride the escape-vector marker to `SupercruiseEntry` → SC-assist + jump. | 19 | yes | `set_throttle`, or `target_next_route` **if already in SC** |
| **exploration** | In-system body tour on an onward hop when `body_tour_enabled`. Loops SC-assisting the first UNEXPLORED nav-list body until the system-icon terminator, then jumps out. | 17 | no | `target_next_route` |
| **sc_resume** | Restart already in supercruise, stale + confident non-local-star lock. CV distance-gate → fast-resume or CLOSE get-around → jump. | 17 | yes | `star_distance_gate` |
| **startup** | Fresh load in NORMAL space (parked at a star). Opens `set_throttle 0`; CV distance-gate two-lane; CLOSE lane pitches off + enters SC + SC-assists; then the shared lock+orient+jump tail. | 19 | yes | `star_distance_gate` |
| **honk** | The ONLY parallel track (`parallel = true`). Ensure analysis-mode → hold `PrimaryFire` until `FSSDiscoveryScan` → release. Never dispatched — only ever a `parallel_tracks` child. | 2 | — | — |
| **dock** | Station terminus. SC-assist to the named station → drop → close to <7.5 km → request docking → `Docked` → station-services macro. | 8 | yes | `dock_close_to_range` |
| **dock_resume** | A NEW route plotted while DOCKED. Auto-launch → full throttle out → wait mass-lock clear → jump tail. Fires only from the docked state. | 9 | yes | `target_next_route` |
| **route_complete_park** | Terminal park at a route-final SYSTEM/star. Throttle 0 → best-effort top-off → SC-assist the star → confirm orbiting → STOP. No jump tail. | 4 | yes | `nav_supercruise_star` |

### Routing (how a procedure gets chosen)

Three dispatch layers decide which procedure runs — none of them is a step:

1. **Boot classifier** — `classify_startup` / `_classify_startup_legacy`
   (`boot_routes.py`) reads live game state at launch through priority gates and
   picks the entry procedure (`startup`, `sc_resume`, `smack_recovery`,
   `arrival`, …). `_run_startup_with_escape_override` (`:506`) wraps startup so a
   boot-time escape-vector hands off to `smack_recovery`.
2. **Event routes** — registered in `activate()` (`boot_routes.py:1350-1353`):
   `_route_fsd_jump` (arrival branch on `FSDJump`; arm `:1117`), `_route_sc_exit`
   (star-smack ALWAYS-RECOVER; def `:1121`), `_route_nav_route` (docked re-plot;
   def `:1221`).
3. **Section orchestrator** — `run_arrival_then_branch` (def `:346`) runs arrival
   then `_arrival_branch` picks the successor section
   (`docking`|`exploration`|`traversal`), mapped to a procedure by
   `_SECTION_TO_PROC` and entered via `transition_to`.

The never-strand driver `redispatch_from_live_state` (`:1329`) re-classifies from
scratch if a procedure aborts, so the bot never dead-ends.

> **Quirk:** `_STATE_TO_PROC` (`boot_routes.py:137`) maps `EXPLORATION` /
> `TRAVERSAL` / `REFUEL` / `PAUSE` / `RESUME` → `'fallback'` (legacy classifier).
> So exploration/traversal are reached **only** via the section orchestrator
> (`_SECTION_TO_PROC`), never directly from the boot classifier.

> **Stale in-file comments** (the code is right, the prose is not): `startup.toml`
> line 11 says "throttle 100 FIRST" but step 0 is `set_throttle 0`; `sc_resume` /
> `startup` prose mentions a "100 Ls" gate while the real `star_distance_gate`
> thresholds are `10.0` (sc_resume) and `15.0` (startup).

---

## What `required = true` means

`required` controls **what happens when the step fails** (returns False — bad
bind, vision unavailable, status flag blocked, journal event never arrived, etc.).

| `required` | step succeeds | step fails |
|---|---|---|
| `false` (default) | move on | log it, move on anyway |
| `true` | move on | trigger `[on_required_fail]` policy → retry-from OR abort |

A `required` failure is the **fail-closed gate**. When a required step fails and
there's no successful retry left, the procedure aborts immediately and **no later
steps run** — that's the whole point. `required = true` on `orient_compass` is
what guarantees the bot never engages a jump without confirmed alignment;
`required = true` on `target_next_route` guarantees it never jumps with no target
locked. Use it on any step whose later steps would be unsafe or pointless without
it.

---

## Step-level control-flow keys

Beyond `action`, `required`, and the action's own params, a step may carry:

| Key | Type | What it does |
|---|---|---|
| `skip_to` | str | On **success**, jump FORWARD to the named action (skipping the steps between). The special target `"__end__"` finishes the procedure cleanly with no further steps. Used for the two-lane distance gate (`star_distance_gate` skips the get-around when the star is FAR). |
| `loop_to` | str | On **success**, jump BACK to the named action — a bounded back-edge for loops (exploration's per-body sweep loops on the `set_throttle` back-edge). |
| `loop_max` | int | Cap on `loop_to` iterations before falling through (prevents an infinite body-tour loop). |
| `retry_anchor` | bool | Marks this step as a mid-procedure retry resume point (used by `smack_recovery`'s state-aware retry). |

`skip_to` / `loop_to` targets must name an `action` that exists in the same
`steps` list (validated at load). Defined in `ed-core/flow/model.py` +
`interpreter.py`.

---

## Procedure-level keys (top of file, before `steps`)

| Key | Type | Default | What it does |
|---|---|---|---|
| `parallel` | bool | `false` | This procedure is a background track (only `honk`). Launched concurrently via a parent's `parallel_tracks`. |
| `parallel_tracks` | list[str] | `[]` | Names of procedures to launch concurrently at the start of this one (every non-honk scene lists `["honk"]`). |
| `stop_on_event` | str | unset | **Reserved (parsed, not enforced).** Journal event meant to end a parallel track early. |
| `timeout_s` | float | `0.0` | **Reserved (parsed, not enforced).** Hard cap for a parallel track. |

### `[on_required_fail]`

```toml
[on_required_fail]
retry_from   = "sc_assist_orbit"   # action name to jump back to on a required fail (must exist in steps)
max_retries  = 3                   # retries before aborting
backoff_s    = 2.0                 # sleep before each retry
retry_from_if_supercruise = "target_next_route"   # OPTIONAL state-aware override
```

With no `[on_required_fail]` block, a required failure aborts on the first try.
`retry_from_if_supercruise` (used by `smack_recovery.toml`) picks a **different**
resume point when the ship is already in supercruise at retry time — the recovery
skips the re-charge steps and resumes at the jump lock instead.

### `input_exclusive`

A registration-time property (set in the `register_step(...)` call, not the TOML)
on the ~15 UI-macro / CV-panel steps that own the screen + input. While an
`input_exclusive` step runs, the heat/idle watchdog is paused so a long
panel-walk isn't mistaken for a stall. Not something a TOML sets — noted here
because it explains why those steps block other input.

---

## Actions

All step params are keyword-only. Anything not listed for an action is ignored
(the loader passes the inline table straight through as `**params`). There are
**~46 registered actions**; the live procedures use a subset. Actions tagged
**ORPHANED** are registered and callable but referenced by no current procedure
(superseded); **LEGACY** ones are superseded within their own family.

### Input primitives

| action | params | what it does |
|---|---|---|
| `press` | `bind: str`, `hold_s: float = 0.05` | Tap a bound action for `hold_s`s. Fails on unbound `bind`. |
| `wait` | `s: float` | Sleep `s`s. Always succeeds. |
| `set_throttle` | `pct: int` (`0`/`25`/`50`/`75`/`100`) | Press the matching `SetSpeedN` bind. Fails on any other `pct` or unbound action. |
| `pitch` | `dir: "up"\|"down"`, `hold_s: float` | Hold `PitchUpButton` / `PitchDownButton` for `hold_s`. |
| `target_ahead` | none | Press `SelectTarget`. With nothing ahead this CLEARS the target. |

### HUD / analysis

| action | params | what it does |
|---|---|---|
| `ensure_analysis_mode` | `poll_s=0.5`, `settle_polls=4`, `max_toggles=3` | Gate on the AnalysisMode status flag (bit 27): already set → no-op; else toggle `PlayerHUDModeToggle` and poll (bounded). The honk only fires in analysis mode. Fails closed without status. |

### Targeting

| action | params | what it does |
|---|---|---|
| `target_next_route` | `poll_s=0.5`, `watchdog_s=60.0` | Press `TargetNextRouteSystem` (also cancels Supercruise Assist), then verify the new `FSDTarget`. **DANGER-STAR REPEAL:** a dangerous class (D*/N/H/W) is now NOTED (`TargetDangerNoted`), **not** refused — a confirmed hop confirms regardless of class; only an unconfirmable target watchdogs out. Event-gated on a NEW `FSDTarget`. |
| `nav_target_star` | `settle_s=0.4`, `panel_focus_action='FocusLeftPanel'` | LOCK the arrival star (row 0) with CV label-confirm that kills the double-toggle unlock bug. **ORPHANED.** `input_exclusive`. |
| `nav_panel_target` | `settle_s=0.4`, `verify_reads=4`, `max_toggles=4`, `max_rows=10`, `pin_to_top=true`, `pin_hold_s=4.0` | Compass-dot + lock-identity verified nav-panel star lock (`max_rows` is a distance proxy). **ORPHANED** (replaced by `nav_supercruise_star`). `input_exclusive`. |

### Jump / supercruise

| action | params | what it does |
|---|---|---|
| `engage_jump_clearance` | `poll_s=0.8`, `max_jump_polls=12`, `max_charge_polls=75`, `max_charge_live_polls=300`, `align_hold_check_poll=8`, `max_align_holds=3`, `malfunction_recovery_s=3.0`, `max_clear_attempts=3`, `max_sc_entry_polls=25`, `clear_burn_s=7.0` | The live jump. Charge-aware hyperspace loop: SC-assist orbit get-around on obstruction, ALIGN-hold re-align, SCO-malfunction wait-and-refire, realspace SC-entry. Replaces `engage_jump`+`hold_alignment`. `input_exclusive`. |
| `engage_supercruise` | `poll_s=0.8`, `max_charge_s=60.0`, `presses=1`, `between_press_s=8.0`, `until_charging=false`, `press=true`, `escape_vector_abort=false` | Press `Supercruise`, gate on `SupercruiseEntry`/flag. `until_charging` re-presses on exclusion-zone refusal (`smack_recovery` passes `presses=3`/`between_press_s=15`/`max_charge_s=240`); `escape_vector_abort=true` (startup) hands off to `smack_recovery` on a boot escape-vector. |
| `engage_jump` | none | Status-flag gate → `SetSpeed100` → `Hyperspace`. **ORPHANED** (every live toml uses `engage_jump_clearance`). |
| `hold_alignment` | `until_event='StartJump'`, `poll_s=0.8`, `align_tol`, `gain`, `min_press`, `max_press`, `samples`, `max_charge_s=60.0` | Post-`engage_jump` micro-align gate. **ORPHANED** (folded into `engage_jump_clearance`). |

### Orientation / CV steering

Fail closed if vision (reader + frame grabber) is unwired.

| action | params | what it does |
|---|---|---|
| `orient_compass` | `**align_overrides` | Yaw/pitch until the targeted star's compass dot centers ahead. Coarse stage. Pair with `required = true` to gate the jump. |
| `orient_widget_ring` | `timeout_s=18.0`, `settle_s=0.45`, `samples=3`, `gain_s_per_px=0.18`, `min_press=0.04`, … | FINE alignment via the mouse widget-ring after the coarse `orient_compass`. Wired in every jump tail. |
| `pitch_star_off` | `bright_thresh=125`, `clear_frac=0.05`, `pitch_hold_s=0.7`, `settle_s=1.4`, `max_iters=20` | Pitch the star off-screen by centre-crop brightness (sun-avoid). Fail-closed. `input_exclusive`. |
| `orient_escape_vector` | `deadzone_px=48.0`, `gain_s_per_px=0.0022`, `min_press=0.12`, `max_press=0.5`, `settle_s=0.5`, `samples=3`, `miss_limit=8`, `max_iters=150`, `search_hold_s=0.45`, `search_limit=45` | Centre the world-space cyan escape-vector sky marker while charge is live, ride to `SupercruiseEntry`; sphere-sweep search on miss. Smack recovery. `input_exclusive`. |
| `pitch_compass` | `until='edge'\|'behind'`, `edge_frac=0.6`, `center_frac=0.25`, `pitch_hold=1.0`, `settle_s=1.0`, `max_iters=20`, `timeout_s=30.0` | Pitch-up-only until the star dot crosses a gate. **ORPHANED.** |

### Nav-panel SC-assist

| action | params | what it does |
|---|---|---|
| `nav_supercruise_star` | `settle_s=0.4`, `panel_focus_action='FocusLeftPanel'`, `label_reads=2`, `label_retry_s=0.5`, `bar_walk_max=5`, `row_reads=3`, `row_retry_s=0.5` | CV row-0 confirm + positive-POI veto + SC-assist button-bar walk on the arrival star. Replaces blind `sc_assist_orbit`. `input_exclusive`. |
| `nav_supercruise_unexplored` | `settle_s=0.4`, `panel_focus_action='FocusLeftPanel'`, `pin_hold_s=4.0`, `label_reads=2`, `label_retry_s=0.5`, `bar_walk_max=5` | Find the first UNEXPLORED nav body, walk the cursor, SC-assist it. Sets `explore_terminated` on the system-icon terminator. Exploration loop. `input_exclusive`. |
| `nav_supercruise_target` | `settle_s=0.4`, `panel_focus_action='FocusLeftPanel'`, `pin_hold_s=4.0` | SC-assist the destination STATION by OCR name-matching its nav-list row. `dock.toml` step 1. `input_exclusive`. |
| `star_distance_gate` | `threshold_ls=100.0`, `settle_s=0.4`, `panel_focus_action='FocusLeftPanel'`, `far_agree_ratio=1.5`, `regrab_gap_s=0.3` | CV row-0 distance gate. `True` = CLOSE/unreadable (run the get-around); `False` = confirmed FAR → `skip_to` the jump. FAR needs two agreeing reads. `input_exclusive`. |
| `sc_assist_orbit` | `settle_s=0.4` | Guarded blind SC-assist macro on the locked local star. **ORPHANED** (replaced by `nav_supercruise_star`). `input_exclusive`. |

### Waits / confirms (state- or event-gated, never wall-clock)

| action | params | what it does |
|---|---|---|
| `wait_cooldown_clear` | `poll_s=0.5` | Block until the `FsdCooldown` status flag clears. Already clear → instant pass. Fails closed without status. |
| `hold_until_event` | `bind: str`, `event: str`, `max_hold_s=30.0` | Key DOWN → wait for `event` → key UP (try/finally, key always released). `max_hold_s` is a safety backstop. Honk uses it. |
| `wait_sc_assist_orbiting` | `poll_s=1.0`, `max_polls=22` | Best-effort wait for the cyan ORBITING-DESTINATION HUD prompt. Returns True on every exit. Replaced the blind `wait s=13`. |
| `confirm_orbiting` | `settle_s=0.4` | Observability: detect the ORBITING prompt; returns False on miss/no-grabber (non-required). Park/traversal/smack. |
| `confirm_sc_assist_active` | none | Observational: OCR the SC-assist HUD state and log it; NEVER gates (always True). Exploration loop. |
| `wait_body_scanned` | `poll_s=0.5`, `max_polls=240` | Event-gated wait for an `AutoScan` seq-advance (persistent high-water baseline). Best-effort True. Exploration between-body gate. |
| `wait_masslock_clear` | — | Block until `FsdMassLocked` clears (post-launch). Dock-resume. |

### Scoop

| action | params | what it does |
|---|---|---|
| `scoop_refuel` | `approach_pct=25`, `standoff_frac=0.50`, `rate_window_s=2.0`, `budget_s=300.0`, `refuel_below=0.70`, `full_epsilon=0.2`, `poll_s=0.5` | Arrival pit-stop fuel scoop: fly into the arrival star, hold at the standoff rate, drink to full. Best-effort (many skip gates); stale-arrival guard. `standoff_frac` is a RATE fraction, not a distance. |

### Docking family

| action | params | what it does |
|---|---|---|
| `nav_supercruise_target` | (see above) | SC-assist the named station. |
| `dock_close_to_range` | `threshold_km=7.5`, `poll_s=1.0`, `max_polls=120` | Poll CV target-panel km distance until inside 7.5 km; ram-guard zeroes throttle on every exit. |
| `dock_request` | `settle_s=0.5`, `poll_s=0.8`, `max_wait_s=120.0` | Literal request-docking tail (E,E,D,space) + throttle 0 + close panel; gate on `DockingGranted`, `DockingDenied(Distance)` → retry. `input_exclusive`. |
| `dock_await_docked` | — | Gate on the `Docked` journal event. |
| `dock_await_exit` | — | Gate on the SC-drop at the station. |
| `station_services_macro` | `keystroke_gap_s=1.0`, `menu_settle_s=2.0`, `menu_reads=3` | Blind docked-services pit-stop macro (W,SPACE,D,SPACE,D,SPACE,S) gated on the docked-menu detector != NONE. `input_exclusive`. |
| `auto_launch` | — | Undock / auto-launch from a station. Dock-resume. |
| `confirm_menu_item` | — | Detect the highlighted docked-menu item (observability). |
| `dock_target_station` / `dock_sc_assist` / `dock_blind_maneuver` | — | Older docking-tail pieces. |
| `dock_approach` | — | **LEGACY** NoFireZone approach — superseded by `dock_close_to_range`. |
| `station_services` | — | **LEGACY** verify-each-service flow — superseded by `station_services_macro`. |

### Pips / power

| action | params | what it does |
|---|---|---|
| `reset_power_distribution` | — | Reset pips to balanced. |
| `pips_engines` | — | Pip to engines (SYS/ENG balance for the burn). |

### Exploration (ed_explore package)

| action | params | what it does |
|---|---|---|
| `body_tour` | — | The in-`ed_explore` body-tour driver. Registered but the live `exploration.toml` uses `nav_supercruise_unexplored` instead. |

> `explore` and `station_strand_recovery` appear in older specs but were **never
> built** — no `register_step` for either exists.

---

## Deleted / banned

`wait_for_event` and `wait_cooldown` are **deleted** — a timeout/duration as a
success/failure gate is banned. Use the event/state-gated waits above
(`hold_until_event`, `wait_cooldown_clear`, `wait_sc_assist_orbiting`,
`wait_body_scanned`, `wait_masslock_clear`) instead.

---

## Conventions

- **Bind names** match Elite's binds-file action names (`ExplorationFSSDiscoveryScan`,
  `Hyperspace`, `Supercruise`, `PitchUpButton`, `SetSpeed100`, …). The sender
  resolves the name to a scancode via the active preset; unbound → step fails.
- **Event names** match Elite journal event names verbatim (`FSSDiscoveryScan`,
  `SupercruiseEntry`, `StartJump`, `FSDJump`, `Docked`, `NavRoute`, …).
- **A False from a step never throttles or jumps.** Failure either aborts (if
  `required`) or is logged and skipped. The `engage_*` steps additionally check
  status flags before sending input.
- **No wall-clock gates.** Waits are journal-event or Status-flag gated; a bare
  `wait s=N` is operator pacing only, never a success/failure gate.
- **Times are seconds, always floats.** `pct` is an integer (`0`/`25`/`50`/`75`/`100`).
