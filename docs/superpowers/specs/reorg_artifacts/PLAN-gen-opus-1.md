# Phase-1 ED-AFK workspace reorg — EXECUTION PLAN (candidate gen-opus-1)

**Council:** council-v2-spec, tier=arch. **Blind generator gen-opus-1.**
**Deliverable:** plan + read-only validating artifacts ONLY. No file moves, no source
edits, worktree-isolated, no pytest, never auto-commit. Operator owns the green-test gate.

Every decision below is grounded in the REAL AST-extracted import graph:
`reorg_import_graph.py --dump` -> **64 modules, 98 intra-package edges** (this candidate's
walk; the spec's 112 counts per-imported-name edges — the layering verdict is identical).
The two lenses live next to this file and are PROVEN:
- **A1** (`reorg_import_graph.py`) FAILs `placement.naive.json` (6 edge violations + the
  capture<->debug_overlay cycle + the 3-package cycle) and PASSes `placement.resolved.json`
  (zero violations / zero cycles / zero unlabeled).
- **A2** (`reorg_behavior_assert.py`) PASSes on master (7/7 asserts green).

---

## D1 — Resolved file placement map

`placement.resolved.json` is the machine-readable source of truth A1 consumes (module
dotted-path -> package). Below is the human table. **Layer ranks:** ed-vision(0) <
ed-core(1) < {ed-autojump, ed-explore, ed-combat}(2, peers). Imports point DOWN only.

### ed-vision (rank 0 — true bottom leaf, imports NOTHING in-workspace)

| module | note |
|---|---|
| `vision/__init__` | |
| `vision/capture` | imports only `get_debug_sink` from the debug_overlay **sink** (intra-vision, downward) — see G2 |
| `vision/compass` | leaf |
| `vision/cyan_reader` | -> compass |
| `vision/debug_overlay` | **SPLIT (G2): keeps SINK half only** (`ScreenToOverlay`, `CvDebugSink`, `set_debug_sink`, `get_debug_sink`, lines 1-207). Becomes a true leaf. |
| `vision/navpanel_icons` | leaf |
| `vision/navpanel_reader` | leaf |
| `vision/opencv_reader` | -> compass |
| `vision/reader` | -> compass/cyan_reader/opencv_reader/ultralytics_reader/yolo |
| `vision/station_menu` | leaf |
| `vision/ultralytics_reader` | -> compass/yolo |
| `vision/widget_ring` | leaf |
| `vision/yolo` | -> compass |
| **DATA** `vision/model/*.{onnx,pt,md}` | follow vision (G11) |
| **DATA** `data/hud_sc_indicators.json`, `data/navpanel_calib_columns.json`, `data/receivetext_catalog.json` | perception reference -> ed-vision |
| **DATA** `tests/fixtures/{navpanel,smack,hud,...}` | perception fixtures -> ed-vision tests |

### ed-core (rank 1 — engine + plumbing + shared flight primitives + registry/active-set)

| module | note |
|---|---|
| `__init__` (package root) | **-> ed-core**: only in-edges are `cli`/`doctor` reading `__version__`/`__file__` (core glue). In the new tree each package gets its own `__init__`; the core host's `__init__` is what cli/doctor read. |
| `anonymizer`, `binds_tool`, `binds_validate`, `pull_binds` | plumbing/tools |
| `cli` | **host** — selects + runs the active app set; reads the core MERGED step registry (G12), never a domain step module |
| `config`, `console_status`, `doctor`, `lifecycle`, `overlay`, `panic`, `panic_listener`, `recorder`, `state`, `visited`, `ship_sizes` | plumbing |
| `executor/__init__` | |
| `executor/align` | **⚠ G4** — imports only `vision.compass` + the debug_overlay **sink** (`get_debug_sink`). Closed-loop align controller behind `step_orient_compass`; moves with the shared flight primitives. Both edges are core->vision (downward). |
| `flow/__init__`, `flow/context`, `flow/loader`, `flow/model` | generic engine |
| `flow/interpreter` | **G12** — reads the CORE merged `STEP_REGISTRY` + `INPUT_EXCLUSIVE_ACTIONS` from `ed_core.flow.step_registry`, NOT from a domain step module |
| `flow/dispatcher` | **SPLIT (D3): keeps ENGINE half** (`_TailHub`, world-state latches, `_apply_state`/`_record_event_time`, `_poll_status`/`_fresh_status`/`_navroute_state`, heat watchdog, `_run`/`_make_context`/preemption/exclusive-input, the registry+active-set router shell). Keeps `_CLEAR_JOIN_WINDOW_S=60.0` **verbatim**. |
| `journal/{__init__,events,tail,waiters}` | journal plumbing |
| `keys/{__init__,binds,scancodes,sender}` | input plumbing |
| `launcher/{__init__,audio_wait,flow,focus,launcher,menu_nav,wizard}` | launcher/CLI host |
| `status/{__init__,navroute,status}` | status plumbing |
| **NEW** `ed_core.cv_debug_cli` | **G2 split target** — `run_calibration`/`run_navpanel_overlay`/`run_cv_debug` land here (they reach UP into `overlay.OverlayWriter` and orchestrate vision+overlay+status) |
| **NEW** `ed_core.flow.step_registry` | **G12 surface #3** — the merged `STEP_REGISTRY` + `INPUT_EXCLUSIVE_ACTIONS`; the interpreter/cli read THIS; domains register names INTO it |
| **DATA** `data/ships.json` | `ship_sizes` (core) reads it -> ed-core |

### ed-autojump (rank 2 — jump + dock + galmap)

| module | note |
|---|---|
| `flow/steps` | the JUMP+DOCK step impls + their registration into the core step table. (Shared/honk/explore steps relocate — see below.) |
| **NEW** `ed_autojump.flow.boot_routes` | **D3 split target** — `_maybe_startup` classifier ladder + `dispatch` table + `dispatch_route_complete` + `_is_route_complete` + `_resolve_final_waypoint` + `_is_parked_terminal`. Holds `FRESH_ARRIVAL_WINDOW_S=30.0` **verbatim** (G7). Registers classifier rules (surface #1) + event routes (surface #2) into the core router shell. |
| `executor/jump`, `executor/navpanel` | jump/dock executors |
| `fsd/{__init__,danger,math,scoops}` | **G13** — jump-domain |
| `session_audit` | **⚠ G13 RESOLVED: ed-autojump.** Imports `DEFAULT_DANGER_CLASSES` from `fsd.danger` (jump-domain); it audits FSDJump counts + danger-class escapes. NOBODY in-package imports it (standalone analysis CLI), so labeling it ed-autojump creates ZERO core->domain edge. The design §4 "core" label is wrong here — A1 proves it makes `core->fsd.danger`. |
| **PROC** `startup.toml, arrival.toml, sc_resume.toml, smack_recovery.toml, dock.toml, route_complete_park.toml, dock_resume.toml` | jump/dock procedures |
| **DATA** `data/galmap_search_coords.json` | + galmap placeholder (real build = Phase 2) |
| **DATA** `data/fsd_modules.json`, `data/fuel_scoops.json` | fsd reads them -> ed-autojump |
| **CFG** `config.toml`, `binds/ED-AFK.4.2.binds` | autojump runtime config/binds (G11) |

### ed-core — the ⚠ shared steps that are NOT single-domain (G3 + G5)

These step impls currently live in `flow/steps.py` but, per the real edge evidence, are
**shared across jump AND arrival/explore** -> they relocate to a core step module
(`ed_core.flow.steps_shared`) and register into the core table. The jump/dock steps stay
in `ed_autojump.flow.steps`. (A1 sees a single `flow.steps` node; the per-function home is
a SOURCE-LEVEL split inside the same file, enforced at execution time, NOT an import-graph
edge — so it does not change A1's verdict, but D5 spells out the byte move.)

| step | home | edge evidence (G-id) |
|---|---|---|
| `step_orient_compass`, `step_pitch_compass`, `step_hold_alignment`, `step_orient_widget_ring` | **ed-core** | **G3** — referenced by procedures in BOTH jump domain (`startup, sc_resume, smack_recovery, route_complete_park, dock, dock_resume`) AND `arrival`. None single-domain. Forces core->vision (correct, downward). |
| `step_ensure_analysis_mode`, `step_hold_until_event` | **ed-core** | **G5** — the honk track; `parallel_tracks=["honk"]` in ALL 7 domain procedures (A2 B5). |
| generic: `step_press`, `step_wait`, `step_set_throttle`, `step_pitch`, `step_target_ahead`, `step_wait_cooldown_clear` | **ed-core** | no vision, cross-domain primitives |
| `step_body_tour` | **ed-explore** | **G6** — referenced by exactly `arrival.toml` (A2 B6). Latches (`body_tour_*`) stay in core world-state. |
| `step_target_next_route`, `step_engage_jump`, `step_engage_supercruise`, `step_sc_assist_orbit`, `step_nav_panel_target`, `step_scoop_refuel` | **ed-autojump** | jump steps |
| all `step_dock_*`, `step_station_*`, `step_auto_launch`, `step_wait_masslock_clear`, `step_confirm_menu_item` | **ed-autojump** | dock steps |

### ed-explore (rank 2)

| item | note |
|---|---|
| `step_body_tour` | relocated, registered into the shared step table, wired in `arrival.toml` **exactly as today** (G6). OFF == byte-identical (config `[exploration].body_tour_enabled`). |
| system-randomness | Phase-2 placeholder only |
| **PROC** | none new (body_tour rides inside `arrival.toml`, which stays in ed-autojump; the STEP is registered cross-package — legal because registration is data, not import) |

### ed-combat (rank 2)

| item | note |
|---|---|
| package skeleton + registry stub that registers NOTHING | reserves the slot, proves the plug-in pattern. Real build = Phase 2. |

### Constant-collision guard (G7 / AC8)

- `boot_routes.FRESH_ARRIVAL_WINDOW_S = 30.0` — the `_maybe_startup` **classifier**
  heuristic. THE task's verbatim invariant. (A2 **B1**.)
- `steps._FRESH_ARRIVAL_WINDOW_S = 120.0` — a SEPARATE constant inside `step_scoop_refuel`.
  Unrelated. Moves with the jump steps. (A2 **B2** asserts it is DISTINCT from B1.)
- `dispatcher.engine._CLEAR_JOIN_WINDOW_S = 60.0` — route-complete correlation window. (A2 **B3**.)
- `max_rows=3` tight call-site (route-complete/arrival nav-panel-target) — the step's
  default param stays `10`; the caller's tight `3` is byte-identical. Phase-2 target, NOT
  touched. `body_tour_max_rows` default `8` unchanged.

**These three are NEVER harmonized.** A generator that conflates B1/B2 is wrong (G7).

### Edge-citation summary for every ⚠/G12/G13 row (AC1)

| row | resolution | real A1 edge it cites |
|---|---|---|
| G2 debug_overlay | SPLIT: sink->vision, runners->`ed_core.cv_debug_cli` | `vision.debug_overlay -> overlay` (VISION-IMPORTS) + `capture <-> debug_overlay` cycle |
| G3 shared flight prims | -> ed-core | steps used by 7 jump procs + arrival (`flow.steps -> vision.*`, `executor.align`) |
| G4 executor/align | -> ed-core | `executor.align -> vision.compass`, `executor.align -> vision.debug_overlay` (sink) |
| G5 honk | -> ed-core | `parallel_tracks=["honk"]` x7 (A2 B5); no import edge — registration data |
| G6 body_tour | -> ed-explore | `body_tour` in arrival.toml only (A2 B6) |
| G12 step table | interpreter/cli read core `step_registry` | `cli -> flow.steps`, `flow.interpreter -> flow.steps` (CORE-IMPORTS-DOMAIN if steps=domain) |
| G13 session_audit | -> ed-autojump | `session_audit -> fsd.danger` (CORE-IMPORTS-DOMAIN if session_audit=core) |

---

## D2 — Registry + active-set API (EXACTLY four surfaces + the registrar)

Code-shaped pseudo-Python. A utility, not a plugin framework (single-operator tool).
All four surfaces live in `ed_core`; domains call them at module import / app activation.

```python
# ed_core/registry.py  — the ONLY extension contract. Four surfaces, no fifth.

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

# --- SURFACE 1: classifier rules (boot-state -> procedure) ----------------
# A rule inspects the latest Status + world-state and EITHER claims the boot
# scene (returns a procedure name) OR passes (returns None). Priority = the
# integer; lower runs FIRST. Byte-identical order is enforced by the priorities
# the domain assigns (ed-autojump reproduces the _maybe_startup ladder order).
ClassifierRule = Callable[["WorldState", "Status"], Optional[str]]

def register_classifier_rule(name: str, rule: ClassifierRule, *, priority: int) -> None: ...

# --- SURFACE 2: event -> procedure routes (live dispatch table) -----------
# A route maps a journal event to a procedure name (or None to pass). The
# router runs active apps' routes; first non-None wins (registration order
# within equal priority preserved).
EventRoute = Callable[["WorldState", "JournalEvent"], Optional[str]]

def register_event_route(event_name: str, route: EventRoute, *, priority: int = 100) -> None: ...

# --- SURFACE 3: step table (the MERGED registry — resolves G12) ------------
# Domains register their step impls by name INTO the one core table. The
# interpreter + cli read THIS, never a domain step module.
StepFn = Callable[..., bool]

def register_step(name: str, fn: StepFn, *, input_exclusive: bool = False) -> None: ...
def merged_step_registry() -> dict[str, StepFn]: ...            # what interpreter/cli read
def input_exclusive_actions() -> frozenset[str]: ...

# --- SURFACE 4: TOML procedure directories --------------------------------
# A domain contributes a directory of *.toml procedures + parallel tracks
# (e.g. honk.toml). The loader merges them; names must be globally unique.
def register_procedure_dir(path: "Path") -> None: ...

# --- the active-set registrar (runtime, NOT a 5th surface) -----------------
@dataclass
class App:
    name: str                                   # "autojump" | "explore" | "combat"
    solo: bool = False                          # combat runs solo (I8)
    activate: Callable[[], None] = lambda: None # calls the 4 register_* surfaces

@dataclass
class ActiveSet:
    """ONE event loop + ONE world-state shared by all active apps (I8)."""
    apps: list[App] = field(default_factory=list)
    world: "WorldState" = field(default_factory=lambda: WorldState())

    def activate(self, *names: str) -> None:
        chosen = [a for a in self.apps if a.name in names]
        if any(a.solo for a in chosen) and len(chosen) > 1:
            raise ValueError("solo app cannot co-activate")   # combat is solo
        for a in chosen:
            a.activate()                          # populates surfaces 1-4

# Phase-1 wiring (in cli.py, the host):
#   aset.register(App("autojump", activate=ed_autojump.activate))
#   aset.register(App("explore",  activate=ed_explore.activate))   # registers body_tour
#   aset.register(App("combat",   solo=True, activate=ed_combat.activate))  # no-op
#   aset.activate("autojump", "explore")          # {autojump, explore} co-active
# jump<->explore NEVER import each other; they meet only at `world` + the event loop (I2).
```

**Surface count = 4** (classifier rules, event routes, step table, TOML dirs) **+ the
active-set registrar** (runtime plumbing, not an extension point). No fifth surface. (I6/AC5.)

---

## D3 — FlowRunner / _TailHub decomposition (every method/latch/constant)

Two columns. **BYTE-IDENTICAL:** same priority order, latch semantics, correlation windows.

| member | STAYS ed-core (engine) | BECOMES ed-autojump (registered rule/route) |
|---|---|---|
| `_TailHub` (class: `subscribe`/`unsubscribe`/`poll`/`__init__`) | ✅ single journal consumer + fan-out | |
| `__init__` (all `self._*` world-state latches: `_smacked, _in_witchspace, _docked, _docked_station, _current_system, _current_ship, _arrival_star_class, _ship_fuel, _final_waypoint, _navroute_cleared, _navroute_cleared_utc, _dock_target, _no_fire_zone_entered, _docking_denied_reason, _fsd_target, _last_fsdjump_utc, _caught_up, _startup_done, _jumps, _fsd_target_seq, _autoscan_seq, _fss_discovered, _fss_body_count, _drop_seq, _scex_seq`, the `body_tour_*` latches, settle ceilings) | ✅ world-state lives in core | |
| `_apply_state`, `_record_event_time`, `event_time`, `_on_tail_event` | ✅ latch maintenance | |
| `_jump_age`, `_parse_journal_ts` (staticmethod) | ✅ time helpers | |
| `_fresh_status`, `_poll_status`, `_navroute_state`, `_fsd_target_state` | ✅ status/navroute polling | |
| `_heat_tick`, `_heat_watchdog_loop`, `heat_guard` | ✅ heat watchdog | |
| `_run`, `_make_context`, `_exclusive_input`, `input_exclusive`, `_should_abort`, `_run_abort`, `_clear_docking_denied`, `_clear_no_fire_zone`, `_wait_for_event`, `request_stop`, `run_live` | ✅ procedure-exec glue + preemption + exclusive-input + live loop | |
| **registry + active-set router shell** | ✅ NEW core code (merges active apps' routes, runs their classifiers in priority order) | |
| `_PREEMPT_ON_SMACK` frozenset | ✅ engine preemption set (core, unchanged) | |
| `_CLEAR_JOIN_WINDOW_S = 60.0` | ✅ **verbatim** (route-complete correlation, used by `_is_route_complete`) | |
| `_maybe_startup` (the boot classifier ladder) | | ✅ -> `ed_autojump.flow.boot_routes`; registers as classifier RULES (surface #1) in the recovered priority order |
| `FRESH_ARRIVAL_WINDOW_S = 30.0` (local in `_maybe_startup`) | | ✅ **verbatim** with the classifier (G7) |
| `dispatch` (FSDJump->arrival\|route-complete; SupercruiseExit@Star->smack_recovery; NavRoute-while-docked->dock_resume) | | ✅ event ROUTES (surface #2) |
| `dispatch_route_complete` (station->dock / system->park) | | ✅ event route |
| `_is_route_complete`, `_resolve_final_waypoint`, `_is_parked_terminal` | | ✅ route-complete helpers (jump-domain logic) |

**No orphans:** every `def` from `grep '^\s+def ' dispatcher.py` and every module constant
is assigned above. (AC4.)

**Recovered classifier ladder order (A2 B4, byte-identical):**
```
docked
  -> in_supercruise
       -> parked_terminal           (_is_parked_terminal -> idle)
       -> p1_indeterminate          (near_star is None or dest is None -> arrival)
       -> p2_local_star             (near_star is True -> arrival)
       -> p3_fresh_arrival          (jump_age None or <= 30.0 -> arrival; else sc_resume)
  -> smacked_cooldown               (_smacked and fsd_cooldown -> smack_recovery)
  -> empty_route_guard              (not route -> idle/no-fly)
  -> startup                        (len>=2 route -> _run("startup"))
```
The split keeps this EXACT order. `boot_routes` registers each branch as a rule with
ascending priority matching source order; the core router runs them lowest-priority-first.

---

## D4 — Dep-cycle proof (A1 exit-0 against this candidate's placement.json)

```
$ python reorg_import_graph.py --placement placement.resolved.json
========================================================================
A1 dep-cycle lens -- Phase-1 reorg
========================================================================
modules: 67   edges: 98
  split: split ed_autojump.vision.debug_overlay -> +ed_core.cv_debug_cli [ed-core]; moved
         ['ed_autojump.overlay', 'ed_autojump.vision', 'ed_autojump.vision.capture',
          'ed_autojump.vision.navpanel_icons', 'ed_autojump.vision.station_menu'];
         redirected-readers []
  split: split ed_autojump.flow.steps -> +ed_core.flow.step_registry [ed-core]; moved [];
         redirected-readers ['ed_autojump.cli', 'ed_autojump.flow.interpreter']
  split: split ed_autojump.flow.dispatcher -> +ed_autojump.flow.boot_routes [ed-autojump];
         moved ['ed_autojump.flow.steps', 'ed_autojump.fsd.scoops']; redirected-readers []

[PASS] TOTALITY: every module labeled  (0 violating)
[PASS] I3  VISION-IMPORTS (ed-vision imports nothing in-workspace)  (0 violating)
[PASS] I1  CORE-IMPORTS-DOMAIN (ed-core must not import a domain)  (0 violating)
[PASS] I2  DOMAIN-DOMAIN (no domain imports another domain)  (0 violating)
[PASS] I1  UPWARD-EDGE (imports point down only)  (0 violating)
[PASS] I1/I3  MODULE CYCLES (no import cycle of any length)  (0 violating)
[PASS] I1  PACKAGE CYCLES (collapsed graph is a DAG)  (0 violating)

RESULT: PASS   (exit 0)
========================================================================
```

The same lens FAILs `placement.naive.json` (proves it discriminates): VISION-IMPORTS
`debug_overlay -> overlay`; CORE-IMPORTS-DOMAIN `cli/interpreter -> flow.steps`,
`session_audit -> fsd.danger`; MODULE CYCLE `capture <-> debug_overlay`; PACKAGE CYCLE
`ed-autojump <-> ed-core <-> ed-vision`.

---

## D5 — Ordered operation list (mandated order; FlowRunner split LAST, in isolation)

Baseline pinned: **16 red / 1581 green** (2026-06-14-reorg-test-baseline.md). Each step
must keep EXACTLY those 16 red and all 1581 green. **A 17th failure = revert.**

### Step 1 — package skeletons (no code moves)
- Create `projects/ed-{core,vision,explore,combat}/pyproject.toml` (+ keep ed-autojump's).
  Each: `[tool.setuptools.packages.find] where=["src"]`; ed-core deps = pydantic/watchdog/
  requests + (optional) cv/vision/hotkey extras; domains depend on `ed-core` + `ed-vision`.
  Move the `[tool.setuptools.package-data]` globs PER PACKAGE (G11): `vision/model/*` ->
  ed-vision; `data/galmap*` -> ed-autojump; etc.
- `[project.scripts] ed-autojump = "ed_core.cli:main"` (host moves to core).
- `pip install -e projects/ed-core projects/ed-vision projects/ed-autojump
  projects/ed-explore projects/ed-combat` into the one shared `.venv`.
- `git mv`: none yet (skeletons only).  **Expected: 16-red / 1581-green** (nothing moved).

### Step 2 — ed-vision (bottom leaf first)
- `git mv projects/ed-autojump/src/ed_autojump/vision/* projects/ed-vision/src/ed_vision/`
  EXCEPT split `debug_overlay.py`: keep the SINK (lines 1-207) in
  `ed_vision/debug_overlay.py`; the runners go to core in Step 3.
- `git mv vision/model/* -> ed-vision`; `git mv data/hud_sc_indicators.json,
  data/navpanel_calib_columns.json, data/receivetext_catalog.json -> ed-vision/data`;
  `git mv tests/fixtures/{navpanel,smack,hud} -> ed-vision/tests/fixtures`.
- **Import rewrites:** within vision, `from ..X` -> `from ed_core.X` does NOT occur (vision
  imports nothing in-workspace once the sink is leaf-only). Internal `from .compass` etc.
  stay relative. capture's `from .debug_overlay import get_debug_sink` stays (sink-only).
- Re-run **A1 partial** + targeted vision tests.  **Expected: 16-red / 1581-green.**

### Step 3 — ed-core (engine + plumbing + shared prims + cv_debug_cli)
- `git mv` plumbing modules (config, lifecycle, overlay, panic*, recorder, state, visited,
  ship_sizes, anonymizer, binds_tool, binds_validate, pull_binds, console_status, doctor),
  `journal/`, `keys/`, `status/`, `launcher/`, `flow/{__init__,context,loader,model,
  interpreter}` -> `ed-core/src/ed_core/`. `git mv executor/{__init__,align}` -> ed-core;
  `git mv data/ships.json -> ed-core/data`.
- **G2 finish:** extract `run_calibration`/`run_navpanel_overlay`/`run_cv_debug` from the
  old debug_overlay into NEW `ed_core/cv_debug_cli.py`; their imports become
  `from ed_core.overlay import OverlayWriter`, `from ed_vision import navpanel_icons`,
  `from ed_vision.capture import ScreenGrabber`, `from ed_vision import station_menu`.
- **G12 finish:** create `ed_core/flow/step_registry.py` holding `STEP_REGISTRY` +
  `INPUT_EXCLUSIVE_ACTIONS` + `register_step`/`merged_step_registry`. Move the GENERIC +
  SHARED + HONK step impls (`step_press, step_wait, step_set_throttle, step_pitch,
  step_target_ahead, step_wait_cooldown_clear, step_orient_compass, step_pitch_compass,
  step_hold_alignment, step_orient_widget_ring, step_ensure_analysis_mode,
  step_hold_until_event`) into `ed_core/flow/steps_shared.py`; each calls `register_step`.
- **Import rewrites:** `interpreter`: `from .steps import ...` -> `from .step_registry
  import merged_step_registry, input_exclusive_actions`. `cli`: `from .flow.steps import
  STEP_REGISTRY` -> `from .flow.step_registry import merged_step_registry`. align:
  `from ..vision.compass` -> `from ed_vision.compass`; `from ..vision.debug_overlay import
  get_debug_sink` -> `from ed_vision.debug_overlay import get_debug_sink`. cv_debug callers
  in cli: `from .vision.debug_overlay import run_*` -> `from .cv_debug_cli import run_*`.
- Re-run **A1** (should now show no VISION-IMPORTS, no cycle).  **Expected: 16-red /
  1581-green.**

### Step 4 — FlowRunner split into ed-autojump (LAST, ISOLATED, revertable alone)
- `git mv flow/steps.py -> ed-autojump/src/ed_autojump/flow/steps.py` (now holds ONLY the
  jump+dock steps; each calls `ed_core.register_step`). `git mv executor/{jump,navpanel},
  fsd/*, session_audit.py -> ed-autojump`. `git mv data/{galmap_search_coords,fsd_modules,
  fuel_scoops}.json, config.toml, binds/ -> ed-autojump`. `git mv procedures/{startup,
  arrival,sc_resume,smack_recovery,dock,route_complete_park,dock_resume,honk}.toml ->
  ed-autojump/procedures` (honk is the shared track; registered via surface #4).
- **dispatcher split:** keep the ENGINE half in `ed_core/flow/dispatcher.py` (D3 left
  column). Extract the classifier ladder + dispatch table + route-complete helpers (D3
  right column) into NEW `ed_autojump/flow/boot_routes.py`. boot_routes registers its rules
  (surface #1) + routes (surface #2) on `ed_autojump.activate()`. Carry
  `FRESH_ARRIVAL_WINDOW_S=30.0` verbatim into boot_routes; `_CLEAR_JOIN_WINDOW_S=60.0`
  stays in the engine. `steps._FRESH_ARRIVAL_WINDOW_S=120.0` rides with steps.py.
- **Import rewrites:** boot_routes `from .steps import _destination_is_local_star,
  _dest_is_named_station` stays intra-ed-autojump (G10). `from ..fsd.scoops` ->
  `from ed_autojump.fsd.scoops`. The engine's `_run` resolves procedures via the core
  loader; no engine->domain import.
- **This is its own commit** so a routing regression reverts in isolation (spec §9).
- Re-run **A1** (full PASS) + **A2 --src** pointed at ed-core/ed-autojump (B1-B7 green) +
  the full suite.  **Expected: 16-red / 1581-green.**

### Step 5 — ed-explore + ed-combat
- `git mv` `step_body_tour` (+ helpers) into `ed-explore/src/ed_explore/steps_body_tour.py`;
  it calls `ed_core.register_step("body_tour", ...)`. `arrival.toml` (in ed-autojump) still
  lists `{action="body_tour"}` — legal because the step is registered cross-package via the
  core table (DATA, not an import). `ed_explore.activate()` registers it.
- ed-combat: `ed-combat/src/ed_combat/__init__.py` with `def activate(): pass` (registers
  nothing). solo=True in the active set.
- cli host: `aset.activate("autojump","explore")`.
- Re-run **A1** (PASS) + full suite.  **Expected: 16-red / 1581-green.**

### Step 6 (housekeeping) — entry point + launch.ps1
- Confirm `[project.scripts] ed-autojump = "ed_core.cli:main"`, `launch.ps1`/`launch_job.ps1`
  paths point at the workspace root, `pip install -e` all five. Launch smoke (operator).

---

## Evidence artifacts (all in this directory)

| artifact | path |
|---|---|
| A1 lens | `reorg_import_graph.py` |
| A2 lens | `reorg_behavior_assert.py` |
| naive trial map (A1 FAILs) | `placement.naive.json` |
| **resolved map (A1 PASSes, A1 consumes)** | `placement.resolved.json` |
| this plan | `PLAN-gen-opus-1.md` |

**Side-effect check (AC7):** `git status --short` shows ONLY additions under
`docs/superpowers/specs/reorg_artifacts/`. No `src/` or `procedures/` diff. No pytest run.
