# ED-AFK workspace reorganization — design (Phase 1)

**Date:** 2026-06-14
**Status:** Approved for spec review → arch-tier council
**Author:** Operator + Claude (brainstorming)
**Scope:** Phase 1 ONLY — a behavior-preserving structural reorganization. No flight-logic
changes. The "handled VERY different" action-flow redesign is Phase 2+ and is explicitly
out of scope here.

---

## 1. Why

The single `ed-autojump` package has grown into four-plus domains fused inside one tree:
jump, docking, exploration (body tour), and shared plumbing, all interwoven in
`flow/dispatcher.py` and a single `flow/steps.py`. New work is coming — `ed-explore`
(navigate to each body + system randomness), `ed-combat` (Type-10 AFK, to be edited
heavily and independently), galaxy-map operation — and the current shape cannot absorb it
without every change touching a central god-object.

Operator decision: reorganize into a scalable workspace of domain packages on a shared
core, **before** any flow redesign, so the redesign lands on clean boundaries and each
phase is independently verifiable.

## 2. Decisions (all operator-approved, 2026-06-14)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Package shape | `ed-core` + domain packages (workspace) |
| D2 | Routing contract | Plug-in: domains register classifier/routes/steps/procedures into core |
| D3 | Concurrency | Core runs an **active set** of apps sharing one event loop + world-state |
| D4 | jump + explore | Two packages, **co-active** via core (interleave via events; never import each other) |
| D5 | combat | Separate package, runs **solo** (its own active set) |
| D6 | Phasing | Reorg first (behavior-preserving), redesign after |
| D7 | Build tooling | setuptools + `pip install -e`, one shared `.venv` (no uv/workspace manager) |
| D8 | Layout | Five packages as siblings under `projects/`; repo root = workspace root |

## 3. Target structure

```
ED-AFK/                       (repo = workspace root)
  launch.ps1, launch_job.ps1
  docs/superpowers/specs/
  projects/
    ed-core/      -> import ed_core
    ed-vision/    -> import ed_vision
    ed-autojump/  -> import ed_autojump   (kept; sheds code to siblings)
    ed-explore/   -> import ed_explore
    ed-combat/    -> import ed_combat     (empty scaffold this phase)
```

### Dependency DAG (imports point DOWN only)

```
ed-vision     pure perception — frames in, measurements out. No keys, no flight.
   ▲
ed-core       engine + plumbing + shared flight primitives + registry/active-set runtime
   ▲
ed-autojump · ed-explore · ed-combat    depend on ed-core + ed-vision; NEVER on each other
```

**Hard invariants (acceptance-gated, §6):**
- No domain imports another domain.
- `ed-core` never imports a domain.
- `ed-vision` imports nothing else in the workspace (true bottom leaf).
- jump↔explore coordination happens through core's shared **event loop + world-state**,
  never a direct import (this is what makes D4 cycle-free).

## 4. Package contents (recommended move map)

The categorization below is the recommended starting map. The council finalizes the
exact file-by-file destinations and **validates the import graph** (some files move based
on their actual imports — see the ⚠ flags, which the council must resolve from real
dependency edges, not assumption).

### ed-vision (bottom leaf)
- `vision/*` — `capture, reader, compass, cyan_reader, widget_ring, navpanel_reader,
  navpanel_icons, station_menu, debug_overlay, opencv_reader, ultralytics_reader, yolo`
- Training/reference data: `vision/model/*` (onnx/pt), `data/hud_sc_indicators.json`,
  calibration references, `tests/fixtures/{navpanel,smack,hud,...}`
- Constraint: perception only — returns measurements; sends no keys, runs no maneuver.

### ed-core
- Plumbing: `journal/`, `keys/`, `status/`, `config.py`, `lifecycle.py`, `panic.py`,
  `panic_listener.py`, `recorder.py`, `anonymizer.py`, `session_audit.py`, `visited.py`,
  `console_status.py`, `overlay.py`, `doctor.py`, `binds_tool.py`, `pull_binds.py`,
  `binds_validate.py`, `ship_sizes.py`
- Launcher/CLI host: `launcher/` (`launcher, wizard, focus, flow, menu_nav, audio_wait`),
  `cli.py` (becomes the host that selects + runs the active app set)
- Flow **engine** (generic machinery): `flow/model.py`, `flow/loader.py`,
  `flow/interpreter.py`, `flow/context.py`
- Extracted from the `FlowRunner` god-object (see §5): tail hub, world-state latches,
  status/navroute polling, heat watchdog, the **registry + active-set router shell**
- Generic steps (no vision): `step_press, step_wait, step_set_throttle, step_pitch,
  step_target_ahead, step_wait_cooldown_clear`
- ⚠ Shared vision-driven flight primitives: `step_orient_compass, step_orient_widget_ring,
  step_hold_alignment, step_pitch_compass` — used by both autojump and explore, so their
  only legal shared home is core (forces core → ed-vision dependency). Council confirms
  none of these is actually single-domain.
- ⚠ `executor/align.py` likely moves here if `step_orient_*` depend on it; council
  resolves from imports.
- ⚠ Honk track: `step_ensure_analysis_mode`, `step_hold_until_event`, and `honk.toml` —
  honk fires on every arrival across domains. Recommend core as a shared track; council
  confirms whether explore-only or truly shared.

### ed-autojump (jump + dock + galmap)
- Procedures: `startup, arrival, sc_resume, smack_recovery, dock, route_complete_park,
  dock_resume` (`.toml`)
- Jump steps: `step_target_next_route, step_engage_jump, step_engage_supercruise,
  step_sc_assist_orbit, step_nav_panel_target, step_scoop_refuel`
- Dock steps: `step_dock_target_station, step_dock_sc_assist, step_dock_approach,
  step_dock_request, step_dock_await_docked, step_station_services, step_auto_launch,
  step_wait_masslock_clear, step_dock_blind_maneuver, step_confirm_menu_item,
  step_station_services_macro`
- `executor/jump.py`, `executor/navpanel.py`; `fsd/` (`math, danger, scoops`)
- The jump-side **classifier rules** (`_maybe_startup` logic) + **event routes**
  (`dispatch` table), registered into core
- `data/galmap_search_coords.json` + a **galmap placeholder** (real build = Phase 2)

### ed-explore
- `step_body_tour` + its latches/config, relocated and wired **exactly as today**
  (today it is a step inside `arrival.toml`; Phase 1 preserves that wiring)
- System-randomness = Phase 2 (placeholder only)

### ed-combat
- Empty scaffold: package skeleton + a registry stub that registers nothing. Proves the
  plug-in pattern and reserves the slot. Real build = Phase 2 (heavy operator edits).

## 5. The hard move: decomposing `FlowRunner`

`flow/dispatcher.py`'s `FlowRunner` does seven jobs. The reorg splits it:

**→ ed-core**
- `_TailHub` (single journal consumer + fan-out)
- World-state latches: `_apply_state`, `_record_event_time`, and every `_*` latch they
  set (`_smacked, _in_witchspace, _docked, _current_system, _current_ship,
  _arrival_star_class, _ship_fuel, _final_waypoint, _navroute_cleared, _dock_target,
  _no_fire_zone_entered, _docking_denied_reason, _fsd_target, _last_fsdjump_utc`, body-tour
  latches)
- Status/NavRoute polling (`_poll_status, _fresh_status, _navroute_state`)
- Heat watchdog (`_heat_watchdog_loop, _heat_tick, heat_guard`)
- Procedure execution glue (`_run, _make_context`, preemption, the exclusive-input guard)
- The **registry + active-set router shell**: a generic surface that holds the active
  app set, merges their event→procedure routes, and runs their classifiers in priority
  order. (New code, extracted from the routing responsibility.)

**→ ed-autojump (registered into core)**
- Boot classifier *rules*: the `_maybe_startup` priority ladder (docked / parked-terminal
  / proximity arrival-vs-sc_resume / smacked / no-route / startup)
- Live event *routes*: the `dispatch` table (FSDJump→arrival|route-complete,
  SupercruiseExit@Star→smack_recovery, NavRoute-while-docked→dock_resume) and
  `dispatch_route_complete` (station→dock / system→park)

**This is the single riskiest change.** Behavior must be preserved byte-for-byte in logic:
same priority order, same latch semantics, same correlation windows
(`_CLEAR_JOIN_WINDOW_S`, the `FRESH_ARRIVAL_WINDOW_S=30s` classifier heuristic stays as-is
this phase — it is a Phase-2 redesign target, NOT a reorg target).

## 6. Acceptance criteria (this is a REFACTOR — prove it changed nothing)

1. **Green baseline first.** Run the suite on `master` pre-move; record the exact set of
   passing tests. The ~13 currently-red tests are out of scope and tracked separately.
2. **Every previously-green test still passes** after the move, assertions unchanged.
   Tests may be relocated into their package's `tests/`, but their logic is identical.
3. **No behavior change.** Boot-classifier priority order, live dispatch, all latches,
   correlation windows, and every procedure's step logic are identical.
4. **Dep check passes.** An import check (import-linter or equivalent) proves: no
   domain↔domain imports, no core→domain imports, ed-vision imports nothing in-workspace.
5. **Installable + launchable.** `pip install -e` each package into the existing `.venv`;
   the `ed-autojump` entry point and `launch.ps1` still start the bot (paths updated as
   needed).
6. **Active-set runtime exists** and `{autojump, explore}` co-activate while `{combat}` is
   solo — but Phase 1 only needs autojump's behavior preserved; explore is just the
   relocated body-tour step, wired as today.

## 7. Execution plan

1. **Pin the green baseline** (operator/Claude): run pytest, snapshot passing tests.
2. **Arch-tier council** (`council` skill, tier=`arch`): blind generators each produce the
   detailed move-map + the `FlowRunner` decomposition + the registry/active-set surface;
   adversarial reviewers apply a **dep-cycle lens** and a **behavior-preservation lens**,
   each with an executable artifact (import-graph check; classifier/dispatch equivalence
   check). Arbiter rules. Worktree-isolated, **never auto-commits**, no pytest inside the
   council (council ops rule).
3. **Operator owns verification + commit.** Claude runs the green-test gate (§6) and the
   dep check against the council's artifact, then commits — small, labelled, revertable
   commits (the package skeletons, then the moves domain by domain, then the `FlowRunner`
   split last so it can be reverted in isolation if it regresses).
4. **Report** decision, winning candidate, every blocker caught, unresolved dissent; log
   to `.claude/council-ledger.jsonl`.

## 8. Out of scope (Phase 2+, separate specs)

- The action-flow redesign: closing the loop on blind proxies (nav-panel distance vs
  `max_rows=3`, ORBITING/ALIGN vs the 13s waits, killing `FRESH_ARRIVAL_WINDOW_S=30s`),
  new explore behaviors, galaxy-map operation, combat flows.
- Any change to flight maneuvers, gates, or conditions.
- Fixing the ~13 pre-existing red tests (tracked separately).

## 9. Risks

- **`FlowRunner` split regresses routing silently** — mitigated by the behavior-
  preservation lens + the green-test gate + committing the split last/in isolation.
- **Hidden import edges create a cycle** — mitigated by the dep-cycle lens + an automated
  import check in acceptance.
- **Package-data / entry-point breakage** (the `[tool.setuptools.package-data]` globs,
  the `ed-autojump` script) — mitigated by the install + launch criterion (§6.5).
- **Over-engineering the registry** (personal-tool ethos) — keep the contract to four
  things: classifier rules, event→procedure routes, step table, TOML procedures. No
  plugin framework.
```
