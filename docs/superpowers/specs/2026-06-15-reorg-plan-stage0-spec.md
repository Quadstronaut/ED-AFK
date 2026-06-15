# Stage-0 spec — Phase-1 ED-AFK workspace reorg EXECUTION PLAN

**Council:** council-v2-spec, tier = arch. **Arbiter-authored.** 2026-06-15.
**Deliverable:** a PLAN + validating artifacts ONLY. No file moves, no source edits,
worktree-isolated, NO pytest, NEVER auto-commit. (operator green-test gate owns verification)

This spec governs what each blind generator must emit and how the two adversarial lenses
(dep-cycle, behavior-preservation) attack it. Every downstream stage is judged against it.

The executable lenses are AUTHORED + PROVEN here:
`docs/superpowers/specs/reorg_artifacts/{reorg_import_graph.py, reorg_behavior_assert.py}`,
with `placement.{naive,resolved}.json` demonstrating A1 bites (both FAIL with explicit edge
lists; A2 PASSes on master).

---

## 0. Ground truth pulled from the real tree (NOT assumption)

These facts are AST-extracted from `projects/ed-autojump/src/ed_autojump` and `procedures/*.toml`
as of master @ 806d298. Generators MUST reconcile against these exact edges; any plan that
contradicts them is wrong on its face.

**G1 — the import graph is real and small.** 64 modules, 112 intra-package edges (A1 dump). The
package is layered, NOT a hairball.

**G2 — the one true cycle hazard: `vision/debug_overlay.py`.** It imports `..overlay` (plumbing,
a core module) at the top of three `run_*` functions, AND `.capture`/`navpanel_icons`/
`station_menu` (vision). The design demands `ed-vision` import NOTHING in-workspace (true bottom
leaf, §3 invariant). As written, `debug_overlay` violates that. Worse, A1 finds a real STATIC
CYCLE `vision.debug_overlay <-> vision.capture` (capture imports `get_debug_sink` at top level;
debug_overlay imports `capture.ScreenGrabber` inside its runners). The *sink* class
(`CvDebugSink`, `ScreenToOverlay`, `set_debug_sink`/`get_debug_sink`) is pure + vision-facing;
the *CLI runners* (`run_calibration`, `run_navpanel_overlay`, `run_cv_debug`) reach UP into
`overlay.OverlayWriter` and orchestrate vision+overlay+status. A generator MUST split this module
so vision keeps only the leaf sink and the upward-reaching runners land in core (a new
`ed_core.cv_debug_cli` or similar). NOTE: A1 counts DEFERRED (in-function) imports as edges by
design — layering must not rely on import-time ordering. This is the single highest-value finding
the dep-cycle lens must confirm is resolved.

**G3 — shared flight primitives are genuinely shared (resolves §4 ⚠).** `orient_compass`,
`orient_widget_ring`, `hold_alignment`, `pitch_compass` are each referenced by procedures in
BOTH the jump domain (`startup, sc_resume, smack_recovery, route_complete_park, dock,
dock_resume`) AND the explore-touching `arrival`. None is single-domain. Legal shared home =
`ed-core`. This forces the `ed-core -> ed-vision` edge (correct, points down).

**G4 — `executor/align.py` imports only `vision.compass` + `vision.debug_overlay`.** It is the
closed-loop align controller backing `step_orient_compass`. It moves with the shared flight
primitives -> `ed-core` (core-step glue that `step_orient_*` call). Caveat: it imports
`vision.debug_overlay.get_debug_sink` — once G2's split lands, that becomes
`vision`-sink-only, so `align.py -> ed-vision` stays a clean downward edge.

**G5 — honk is universally shared (resolves §4 ⚠).** `parallel_tracks = ["honk"]` appears in ALL
SEVEN domain procedures (`arrival, dock, dock_resume, route_complete_park, sc_resume,
smack_recovery, startup`); `honk.toml` is the track itself (8 TOMLs total = 7 domain + honk).
`ensure_analysis_mode` + `hold_until_event` + `honk.toml` are a cross-domain track -> `ed-core`.
NOT explore-only. (A2 asserts 7/7.)

**G6 — `body_tour` is explore, wired only in `arrival.toml`.** `step_body_tour` is referenced by
exactly one procedure (`arrival.toml`, opt-in, OFF == byte-identical). The step belongs to
`ed-explore`; Phase 1 keeps it registered into the shared step table and wired into `arrival.toml`
EXACTLY as today. Its latches (`body_tour_*` in `_apply_state`, `body_tour_max_rows` plumbed via
`_make_context`/`StepContext`, default `8`) stay where the world-state lives (`ed-core`).

**G7 — the two FRESH_ARRIVAL constants are DIFFERENT (collision trap).**
- `dispatcher.py:1170` `FRESH_ARRIVAL_WINDOW_S = 30.0` — the `_maybe_startup` *classifier*
  heuristic. THIS is the task's verbatim invariant. Moves to ed-autojump with the classifier.
- `steps.py:1179` `_FRESH_ARRIVAL_WINDOW_S = 120.0` — a SEPARATE constant inside
  `step_scoop_refuel`. Unrelated. Moves with the jump steps to ed-autojump.
  A generator that conflates / "harmonizes" these is WRONG. Both stay verbatim at their own value.

**G8 — other verbatim constants.** `_CLEAR_JOIN_WINDOW_S = 60.0` (dispatcher, route-complete
correlation window) and the `max_rows=3` tight call-site in the route-complete/arrival
nav-panel-target path (the step's *default param* is `10`; the tight `3` is passed by the caller).
All stay byte-identical (Phase-2 targets, NOT reorg targets).

**G9 — `STEP_REGISTRY` is one global dict** in `steps.py`, populated incrementally across the
file (`STEP_REGISTRY = {...}` then repeated `STEP_REGISTRY.update({...})`). The interpreter reads
`STEP_REGISTRY` + `INPUT_EXCLUSIVE_ACTIONS` from `steps`. Splitting steps across packages means
each domain registers its step names into the ONE shared core table — this is registration
surface #3 (step table). The interpreter (core) and cli-validation MUST read the MERGED registry,
never import a domain step module (see G12).

**G10 — classifier↔step coupling stays intra-package.** `_maybe_startup` does
`from .steps import _destination_is_local_star`. Both the classifier and that step move to
ed-autojump together, so the edge stays inside one package (no cross-package edge created).

**G11 — entry point + package-data.** `[project.scripts] ed-autojump = "ed_autojump.cli:main"`;
`[tool.setuptools.package-data]` globs `data/*.json schemas/*.json binds/*.binds
edhm-presets/*.json vision/model/*.{onnx,pt,md}`. `cli.py` imports across journal/launcher/
status/vision/flow/config — it is the host and lands in ed-core. Package-data globs must follow
their files to the right package (model/* -> ed-vision; galmap/navpanel json -> ed-autojump).

### 0.5 — Ambiguities A1 SURFACED that the design §4 did NOT flag (generators MUST resolve)

Running A1 against trial placements exposed two cross-package edges beyond the §4 ⚠ set. Each is
a real resolution the winning plan must make and justify from edges:

**G12 — `cli` + `flow.interpreter` -> `flow.steps` (the step-table seam).** If jump steps move to
ed-autojump, then `flow.interpreter` (core, line: `from .steps import INPUT_EXCLUSIVE_ACTIONS,
STEP_REGISTRY`) and `cli.py:483` (`from .flow.steps import STEP_REGISTRY` for procedure
validation) would become core->domain imports. RESOLUTION: the interpreter and cli must read the
core-owned MERGED step registry populated by the active set (D2 surface #3), NEVER import a domain
step module. This is WHY surface #3 exists; a plan that just `git mv`s `steps.py` whole fails A1
here. (Alternatively: generic/shared steps stay in a core step module, domain steps register in —
the generator picks and proves it.)

**G13 — `session_audit` -> `fsd.danger` (a mislabeled-domain trap).** `session_audit.py:17`
imports `DEFAULT_DANGER_CLASSES` from `fsd.danger`. `fsd/` (math/danger/scoops) is jump-domain.
So either `session_audit` is jump-domain (move to ed-autojump, NOT core as design §4 lists), or
`fsd.danger`'s shared constants are core. The design §4 puts `session_audit` in core — A1 proves
that creates a core->domain edge. Generator MUST resolve from intent + edges and state which.

---

## 1. Interface — what each blind generator MUST emit (all five deliverables)

A generator's submission is a single plan document plus referenced data files. It is REJECTED
(treated `abstain` by the arbiter) if any of D1–D5 is missing or lacks its named artifact.

**D1 — Resolved file placement map.** A table: every `.py`, `.toml`, and data file in
`projects/ed-autojump` -> its destination package (`ed-core | ed-vision | ed-autojump |
ed-explore | ed-combat`) PLUS a machine-readable `placement.json` (module-dotted-path ->
package) that A1 consumes. Each ambiguous-flagged row (the §4 ⚠ items AND G12/G13) carries a
one-line justification CITING the real import edge from artifact A1 (not "recommended"). Must
agree with G2–G6/G12/G13 or argue, with edge evidence, why they are wrong.

**D2 — Registry + active-set API.** The core/domain plug-in contract, EXACTLY four registration
surfaces and no more (anti-over-engineering, §9): (1) classifier rules, (2) event->procedure
routes, (3) step table, (4) TOML procedure directories. Plus the active-set runtime: core holds
an active SET of apps sharing ONE event loop + ONE world-state; `{autojump, explore}` co-active,
`{combat}` solo; jump<->explore coordinate ONLY via shared world-state/events, never a direct
import. Emit the function/dataclass signatures (names + params + return types) for each of the 4
surfaces and the active-set registrar — as code-shaped pseudo-Python, NOT prose. Keep it a
utility, not a plugin framework (single-operator personal tool).

**D3 — FlowRunner decomposition map.** Per spec §5: an explicit two-column table —
"stays in ed-core engine" vs "becomes ed-autojump registered rule/route" — for EVERY method,
latch, and constant currently on `FlowRunner` / `_TailHub` in `dispatcher.py`. Must list:
`_TailHub` (core), every `_*` world-state latch + `_apply_state`/`_record_event_time` (core),
status/navroute polling `_poll_status`/`_fresh_status`/`_navroute_state` (core), heat watchdog
`_heat_watchdog_loop`/`_heat_tick`/`heat_guard` (core), procedure-exec glue
`_run`/`_make_context`/preemption/`_exclusive_input`/`input_exclusive` (core), the
registry+active-set router shell (core, new code); AND -> ed-autojump: the `_maybe_startup`
priority ladder (boot classifier rules) + the `dispatch` table + `dispatch_route_complete` +
`_is_route_complete` + `_resolve_final_waypoint` + `_is_parked_terminal` (live event routes).
Behavior BYTE-IDENTICAL: same priority order, same latch semantics, same correlation windows.
`FRESH_ARRIVAL_WINDOW_S=30.0` (classifier), `_CLEAR_JOIN_WINDOW_S=60.0`, `max_rows=3` call-site
stay VERBATIM. Respects G7/G8/G10. The recovered ladder order (A2) is:
`docked -> in_supercruise{parked_terminal, then p1_indeterminate < p2_local_star <
p3_fresh_arrival} -> smacked+cooldown -> empty-route guard -> startup`.

**D4 — Dep-cycle proof.** A machine-checkable statement of the post-move dependency rules AND its
passing output against the proposed placement map (D1). The executable artifact is A1 run against
the generator's own `placement.json`. MUST prove: no domain->domain, no core->domain, ed-vision
imports nothing in-workspace, no cycle of any length, no UNLABELED module (placement.json total).
Paste A1's exit-0 output (violations list EMPTY).

**D5 — Ordered operation list.** The safe incremental commit sequence, in THIS order
(non-negotiable, per task): (1) package skeletons (5 `pyproject.toml` + `pip install -e`),
(2) ed-vision, (3) ed-core, (4) FlowRunner split into ed-autojump — LAST, in isolation,
revertable alone, (5) ed-explore + ed-combat. Per step emit: the exact `git mv` set, the import
rewrites (old dotted path -> new), and the expected test result (`stays 16-red / 1581-green`,
citing the pinned baseline). The FlowRunner-split step (4) must be its own commit so a routing
regression can be reverted in isolation (spec §9 mitigation).

---

## 2. Invariants (hold across every candidate; violation = blocker)

- **I1 (DAG).** Post-move imports point DOWN only: `domains -> {ed-core, ed-vision}`,
  `ed-core -> ed-vision`. NO domain->domain. NO core->domain. ed-vision imports NOTHING
  in-workspace. No import cycle of any length. (acceptance via A1)
- **I2 (jump<->explore decoupling).** No `ed_autojump <-> ed_explore` import in either
  direction. They coordinate solely through core's shared world-state + event loop. (A1)
- **I3 (debug_overlay resolved).** The G2 hazard is eliminated: nothing in `ed-vision` imports
  `ed-core` (or any non-vision module), and the `vision.debug_overlay<->vision.capture` static
  cycle is gone. The plan states explicitly where the `debug_overlay` upward-reaching runners
  land. (A1 + D1 narrative)
- **I4 (behavior byte-identical).** No flight-logic change. Same classifier priority order, same
  latch semantics, same dispatch table, same correlation windows. Constants verbatim:
  `FRESH_ARRIVAL_WINDOW_S=30.0` (classifier), `steps._FRESH_ARRIVAL_WINDOW_S=120.0` (scoop —
  NOT the same constant, G7), `_CLEAR_JOIN_WINDOW_S=60.0`, `max_rows=3` call-site,
  `body_tour_max_rows` default 8. (acceptance via A2)
- **I5 (test invariant).** The plan keeps EXACTLY the 16 pinned reds red and all 1581 greens
  green. No 17th failure is tolerable. Tests may relocate to a package's `tests/` but assertions
  are unchanged. (baseline: 2026-06-14-reorg-test-baseline.md)
- **I6 (four registration surfaces, no fifth).** The registry contract is exactly: classifier
  rules, event->procedure routes, step table, TOML procedures. No plugin framework, no extra
  extension points. (D2 review)
- **I7 (plan-only, no side effects).** No file moved, no source edited, no commit, no pytest run,
  worktree-isolated. The deliverable is documents + read-only analysis artifacts.
- **I8 (active-set shape).** Exactly one event loop + one world-state in core; `{autojump,
  explore}` co-active; `{combat}` solo. Phase 1 only needs autojump behavior preserved + explore
  = relocated body_tour wired as today; combat = empty scaffold registering nothing.
- **I9 (entry point + package-data survive).** The plan updates `[project.scripts]` /
  `package-data` globs / `launch.ps1` paths so `pip install -e` each package + launch still
  starts the bot. Each data glob follows its files to the owning package (G11).

---

## 3. Acceptance criteria (executable; arbiter requires an evidence_artifact per pass)

Each criterion names the artifact that proves it. A reviewer `pass` WITHOUT the named artifact
is downgraded to `abstain` by the arbiter (council-v2 evidence rule).

- **AC1 — Placement map is total + edge-justified.** Every file in `src/ed_autojump` + every
  `.toml`/data file appears exactly once in D1; the `placement.json` has ZERO unlabeled modules.
  Each ⚠/G12/G13 row cites a real A1 edge. *Artifact:* D1 table + `placement.json` + A1 dump.
- **AC2 — Dep rules pass under the proposed labels.** Running A1 with D1's `placement.json` yields
  RESULT: PASS — zero violating edges, zero cycles, zero unlabeled. *Artifact:* A1 exit-0 output.
- **AC3 — debug_overlay split named.** The plan states the exact destination of `CvDebugSink`/
  `ScreenToOverlay`/sink-registry (-> ed-vision) vs `run_calibration`/`run_navpanel_overlay`/
  `run_cv_debug` (-> ed-core), and A1 shows no resulting ed-vision->core edge AND no
  debug_overlay<->capture cycle. *Artifact:* D1 narrative + AC2 output.
- **AC4 — FlowRunner split is total + behavior-preserving.** Every method/latch/constant on
  `FlowRunner`+`_TailHub` is assigned in D3 with no orphans; the classifier priority ladder is
  reproduced in the SAME order (the A2-recovered order); the verbatim constants are present and
  unchanged. *Artifact:* A2 exit-0 + D3 table completeness.
- **AC5 — Four surfaces, no fifth.** D2 enumerates exactly four registration surfaces + the
  active-set registrar; a reviewer confirms no extra extension point, and surface #3 (step table)
  resolves G12. *Artifact:* D2 signatures.
- **AC6 — Ordered ops are safe + revertable.** D5 follows the mandated order, the FlowRunner
  split is its own isolated step, and each step's expected result is "16-red/1581-green" with the
  git-mv set + import rewrites spelled out. *Artifact:* D5 step list.
- **AC7 — No side effects produced.** No git diff to source, no new/moved file under
  `src/`/`procedures/`, no pytest invocation in the council run. *Artifact:* `git status`
  showing only doc/artifact additions under `docs/`.
- **AC8 — Constant-collision guarded.** The plan explicitly distinguishes the two FRESH_ARRIVAL
  constants (G7) and keeps both verbatim. *Artifact:* D1/D3 note + A2 B1/B2 asserts.

---

## 4. Concrete executable acceptance tests (the two adversarial lenses' artifacts)

Two read-only scripts live in `docs/superpowers/specs/reorg_artifacts/`. They run against the
UNMOVED tree (files do not move in this phase) by taking the candidate's `placement.json` as a
label overlay. NEITHER imports the package, runs pytest, or edits anything. Both are AUTHORED +
SELF-VERIFIED here (A2 PASSes on master; A1 FAILs the naive + resolved trial maps with explicit
edge lists, proving it discriminates).

**A1 — dep-cycle lens (`reorg_import_graph.py`).** AST-walks `src/ed_autojump`, builds the real
intra-package edge set (deferred imports counted), applies a candidate `placement.json`, and
checks I1/I2/I3 + cycle detection + totality. Output: per-rule PASS/FAIL + the explicit list of
any violating edges (e.g. `VISION-IMPORTS ed_autojump.vision.debug_overlay ->
ed_autojump.overlay`). `--dump` prints the raw graph generators build placement.json against.
Exit 0 iff PASS with zero violations/cycles/unlabeled. The dep-cycle reviewer's evidence_artifact.

**A2 — behavior-preservation lens (`reorg_behavior_assert.py`).** Static-asserts, by parsing
source (NOT executing flight logic), the byte-identical invariants the FlowRunner split must
preserve. The seven asserts: B1 classifier `FRESH_ARRIVAL_WINDOW_S==30.0`; B2 `steps.
_FRESH_ARRIVAL_WINDOW_S==120.0` distinct from B1; B3 `_CLEAR_JOIN_WINDOW_S==60.0`; B4 the
`_maybe_startup` ladder order (source-order recovered:
`docked -> in_supercruise -> parked_terminal -> p1_indeterminate < p2_local_star <
p3_fresh_arrival -> smacked_cooldown`); B5 `parallel_tracks=["honk"]` in all 7 domain procedures;
B6 `body_tour` in exactly `arrival.toml`; B7 the shared/honk/body step impls all present. Exit 0
iff all pass. The behavior reviewer's evidence_artifact; a plan must not change any asserted value.
After the operator executes the plan, A2 is re-runnable with `--src` pointed at the new
ed-core/ed-autojump roots to confirm the constants survived verbatim.

A pass on either lens that does not attach its script's exit-0 output is treated as `abstain`.

---

## 5. Out of scope (Phase 2+; a plan that touches these is spec-non-conformant -> routes to Stage 0)

- ANY flight-logic / maneuver / gate / condition change.
- Killing `FRESH_ARRIVAL_WINDOW_S=30s`, closing nav-panel `max_rows=3` proxy, ORBITING/ALIGN
  vs 13s waits — explicitly Phase-2 targets.
- Fixing the 16 pinned red tests.
- Real galmap build, new explore behaviors, real combat flows (combat is an empty scaffold).

---

## 6. Reviewer §6 verdict shape (for Stage-3 arbitration)

Each reviewer returns, per lens: `verdict` (pass|fail|abstain), `severity` (none|minor|blocker),
`evidence_artifact` (path to A1/A2 exit-0 output or the violation list), `findings[]`. A
spec-conformance fail (touching §5) is a blocker that routes to Stage 0. A dep-cycle fail with a
real violating edge is a blocker that routes to Stage 1. Missing evidence_artifact => abstain.
