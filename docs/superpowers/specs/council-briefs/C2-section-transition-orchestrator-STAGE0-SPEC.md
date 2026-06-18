<!-- council-v2 | C2-section-transition-orchestrator | STAGE 0 SPEC | arbiter | 2026-06-18 -->
<!-- task: BUILD shippable+tested section-transition orchestrator. Design-only is LIFTED. -->

# Stage-0 Spec — Section-Transition Orchestrator (C2, BUILD)

The operator has LIFTED design-only. This is a **buildable, committable, tested** deliverable.
Every claim below is grounded in cited flight code; the ONE game-truth gap (D1) is isolated
behind a single fail-closed predicate and clearly marked. Downstream stages are judged against
this document verbatim.

---

## 0. Scope & ground-truth anchors (proof, not vibes)

| Fact the build depends on | Proof (file:line) |
|---|---|
| `FlowRunner.procedures` is a real dict attr (`transition_to` membership check is valid) | `projects/ed-core/src/ed_core/flow/dispatcher.py:135`, `:508` |
| `runner._run(name)` exists; **clears `self._preempt = None` on entry** | `dispatcher.py:507-514` |
| `runner._should_abort()` exists, OPERATOR-ONLY (stop/panic), preempt deliberately excluded | `dispatcher.py:390-399` |
| `runner._run_abort()` = `should_abort() OR _preempt is not None` (combined signal) | `dispatcher.py:401-406` |
| `runner._preempt` always initialized (`Optional[str]`, default None) | `dispatcher.py:221` |
| `runner._smacked` always initialized (bool, default False) | `dispatcher.py:189` |
| Smack mid-scene sets BOTH `_preempt="star_smack"` (if `_running_proc in _PREEMPT_ON_SMACK`) AND `_smacked=True` | `dispatcher.py:621-643` |
| `_PREEMPT_ON_SMACK` **contains `"arrival"`** → arrival is preemptible; `_running_proc` stays `"arrival"` until the next `_run` | `dispatcher.py:37`, `:628` |
| `_body_tour_enabled` is the runner attr; `ctx.body_tour_enabled` is sourced from it | `dispatcher.py:157`, `:462`; `context.py:148` |
| `_exploration_mode` is a **PHANTOM** — read at `boot_routes.py:84` but assigned NOWHERE in `projects/` | `Grep _exploration_mode\s*=` → 0 matches |
| Settled discriminator predicates already exist in `ed_core` (domain-free) | `predicates.py:12` (`_destination_is_local_star`), `:43` (`_dest_is_named_station`) |
| `ArrivalLatch.arm/consume` (idempotent arm, exactly-once consume) | `primitives.py:53-91` |
| `register_classifier_rule` / `register_event_route` surfaces (raise on dup name) | `registry.py:20`, `:45` |
| Live arrival dispatch site (FSDJump route) | `boot_routes.py:529` (`runner._run("arrival")` in `_route_fsd_jump`) |
| Restart arrival dispatch site (C-series primary path) | `boot_routes.py:303` (`runner._run(payload)`, payload=="arrival") |
| Restart arrival dispatch sites (legacy fallback, 3 branches) | `boot_routes.py:197`, `:207`, `:221` |
| `run_classifiers` fires only when `not events`; `run_event_routes` only when events present (mutual exclusion per tick) | `dispatcher.py:942-944`, `:956` |
| Pytest collects `projects/ed-autojump/tests`; default run is `-m 'not requires_game'` | `pytest.ini:14-24` |

**LOCKED operator decisions honored:**
- **#10** (`CONSOLIDATED-BLOCKERS.md:72-81`): Python orchestrator in `ed_autojump` (`_SECTION_TO_PROC` +
  `transition_to` + `run_arrival_then_branch`), no core→domain import; **MANDATORY abort-recheck at TWO points**.
- **#9** (`CONSOLIDATED-BLOCKERS.md:39-41`): exploration predicate reads **`body_tour_enabled`** (CONFIRMED wired),
  NOT the phantom `_exploration_mode`. **This OVERRIDES the C2 design-council reference test**
  (which read `runner._exploration_mode`) — see §7 DEVIATION.

---

## 1. Interface (the contract to build)

All new code lands in **`projects/ed-autojump/src/ed_autojump/flow/boot_routes.py`** (domain side; no
core→domain import). Discriminators that are pure telemetry reads MAY live in
`ed_core/flow/predicates.py` (domain-free) and be imported by the orchestrator.

### 1.1 Section→procedure map
```python
# boot_routes.py — module-level, frozen, asserted total at import.
_SECTION_TO_PROC: dict[str, str] = {
    "docking":     "dock",
    "exploration": "exploration",
    "traversal":   "traversal",
}
```

### 1.2 `transition_to(runner, section) -> str`
Fail-closed successor dispatch with the **mandatory abort-recheck (point b)**.
```python
def transition_to(runner: Any, section: str) -> str:
    # (b) ABORT-RECHECK at the TOP, BEFORE runner._run (which clears _preempt).
    #     A smack/preempt/operator-abort landing here must NOT branch forward.
    if _transition_aborted(runner):
        return ""                       # yield to run_live -> _route_sc_exit
    proc = _SECTION_TO_PROC.get(section)
    if proc is None or proc not in getattr(runner, "procedures", {}):
        return ""                       # unknown/unloaded section = named abort, never a blind run
    runner._run(proc)
    return proc
```
- Returns the dispatched procedure NAME on success.
- Returns `""` on (i) abort-recheck positive, (ii) unknown section, (iii) section's procedure not loaded.
- `""` is a **named operator/abort signal**, never "run a blank procedure".

### 1.3 `run_arrival_then_branch(runner) -> Optional[str]`
The orchestration wrapper that REPLACES the bare `runner._run("arrival")` at every arrival site.
```python
def run_arrival_then_branch(runner: Any) -> Optional[str]:
    runner._run("arrival")                      # the arrival scene (honk track, scoop, sc-assist)
    # (a) ABORT-RECHECK BETWEEN arrival's return and the discriminator read.
    #     A smack landing mid-arrival sets _smacked / _preempt; do NOT branch into
    #     the exclusion zone — yield to run_live, which dispatches _route_sc_exit.
    if _transition_aborted(runner):
        return "arrival"                        # arrival ran; branch suppressed
    section = _arrival_branch(runner)
    transition_to(runner, section)
    return "arrival"
```
Return value `"arrival"` preserves the existing route/classifier contract (callers today
`return "arrival"` after `_run("arrival")`; the orchestrator keeps that external signature so
`run_event_routes` / `classify_startup` semantics are unchanged).

### 1.4 Abort-recheck predicate (the LOCKED #10 core)
```python
def _transition_aborted(runner: Any) -> bool:
    """True if a section transition must be SUPPRESSED: a smack landed, a preempt
    was requested, or the operator aborted. Reads the three LOCKED sources."""
    if bool(getattr(runner, "_smacked", False)):
        return True
    if getattr(runner, "_preempt", None) is not None:
        return True
    abort = getattr(runner, "_should_abort", None)
    return bool(abort()) if callable(abort) else False
```
Reads **`self._smacked`**, **`self._preempt`**, **`should_abort()`** — exactly the three the operator
named. Fail-closed: any of them set ⇒ no branch.

### 1.5 Arrival branch table
```python
def _arrival_branch(runner: Any) -> str:
    st = runner._latest_status
    route = _route_of(runner)               # nr.route or None, via runner._navroute_state()
    system = runner._current_system
    if _dest_is_system(st, route, system):  # arrived at final destination
        return "docking"
    if _exploration_active(runner):         # body-tour active
        return "exploration"
    return "traversal"                      # default: onward hop
```
Precedence is VERBATIM master spec (`MASTER-SPEC.md:66-68`): `dest_is_system` → `exploration` → `traversal`.

### 1.6 Discriminators

**`_dest_is_system(st, route, system_name) -> bool`** (buildable now; reuses settled predicate):
```python
def _dest_is_system(st, route, system_name) -> bool:
    if not route:                                   # None / [] -> empty route is PRIMARY signal
        return True                                 # no onward hop -> arrived (terminal Docking)
    return _destination_is_local_star(st, system_name) is True   # CORROBORANT
```

**`_dest_is_station(st) -> bool`** — **THE D1-BLOCKED PREDICATE, ISOLATED**:
```python
def _dest_is_station(st) -> bool:
    # BLOCKED-ON-D1: confirm Status.Destination.Body != 0 => station
    # (operator must test: undock -> plot-station -> read Status.json).
    # Until D1 is confirmed in-game, this predicate's REAL-WORLD correctness is
    # unverified. It fails CLOSED: a non-station / unread dest reads False, and the
    # arrival branch falls through to Traversal (never a blind drive into a station).
    # Schema NOT hardcoded as confirmed — _dest_is_named_station is the settled
    # READ, but the GAME MECHANIC that sets Body!=0 at plot-to-station time is D1.
    return _dest_is_named_station(st)               # predicates.py:43 (Body!=0 + non-$ Name)
```
NOTE: `_dest_is_station` is a SEPARATE, named predicate per the operator's "ONE isolated predicate"
mandate. The arrival branch does NOT call it to force Docking mid-route (Docking is reached at
route-complete, empty-route only); it exists as the named, marked seam D1 will validate.

**`_exploration_active(runner) -> bool`** — **LOCKED #9, reads `body_tour_enabled`, NOT the phantom**:
```python
def _exploration_active(runner) -> bool:
    # LOCKED #9: read the CONFIRMED-wired body-tour flag (runner._body_tour_enabled,
    # the source of ctx.body_tour_enabled), NOT the phantom runner._exploration_mode
    # (assigned nowhere -> always False). Fail-closed to False if unset.
    return bool(getattr(runner, "_body_tour_enabled", False))
```

### 1.7 Wiring (integration sites)
Replace bare arrival dispatch with the orchestrator at **all five** sites that currently run arrival
on a NEW arrival (NOT on restart-into-completed-park, which idles):
- `boot_routes.py:529` (live `_route_fsd_jump`) → `run_arrival_then_branch(runner)` then `return "arrival"`.
- `boot_routes.py:303` C-series primary path, ONLY when `payload == "arrival"` → route through the
  orchestrator (other payloads — startup/smack_recovery — keep `runner._run(payload)`).
- `boot_routes.py:197/:207/:221` legacy restart-arrival branches → `run_arrival_then_branch(runner)`.
No new `register_*` call is required (the orchestrator is invoked from inside the already-registered
`_route_fsd_jump` / `classify_startup`); `activate()` is unchanged. If a reviewer prefers an explicit
registration surface, it must still avoid a core→domain import and stay idempotent.

---

## 2. Invariants (must hold; each is test-enforced)

- **INV-1 (fail-closed transition).** `transition_to` dispatches NOTHING and returns `""` for an
  unknown section OR a section whose procedure is not in `runner.procedures`.
- **INV-2 (abort suppresses branch — point a).** If `_smacked` OR `_preempt is not None` OR
  `should_abort()` is true at the point BETWEEN arrival's return and the discriminator read,
  `run_arrival_then_branch` performs NO `transition_to` and dispatches no section procedure.
- **INV-3 (abort suppresses dispatch — point b).** `transition_to` reads the abort sources at its TOP,
  BEFORE `runner._run`, and returns `""` without dispatching if any is set. (Ordering is load-bearing:
  `_run` clears `_preempt`, dispatcher.py:514 — checking after would be blind.)
- **INV-4 (smack-mid-transition never drives the exclusion zone).** Given a runner with `_running_proc`
  left at `"arrival"` and a smack applied (`_smacked=True`, `_preempt="star_smack"`), the orchestrator
  branches to NOTHING and yields; `_route_sc_exit` (CV-gated) owns recovery.
- **INV-5 (exploration source).** `_exploration_active` reads `_body_tour_enabled` and NEVER
  `_exploration_mode`. With `_body_tour_enabled` unset/false → False; true → True.
- **INV-6 (branch precedence).** `dest_is_system` is evaluated FIRST (arrived beats exploration beats
  traversal). An arrived-at-destination runner with exploration ON still branches `docking`.
- **INV-7 (dest_is_system fail-closed).** Empty/None route → True (terminal). Non-empty route with an
  unjudgeable dest (`_destination_is_local_star` returns None/False) → False (no false "arrived").
- **INV-8 (D1 isolation + fail-closed).** `_dest_is_station` is a single named predicate carrying the
  `# BLOCKED-ON-D1` marker; its mis-classification can only fail toward Traversal/park, never toward a
  blind station drive. The unverified schema is NOT asserted as confirmed anywhere else.
- **INV-9 (no core→domain import).** `boot_routes.py` (domain) imports `ed_core` only; nothing in
  `ed_core` imports `ed_autojump`. (Enforced by the existing whole-tree DAG gate.)
- **INV-10 (no arbitrary timed waits).** The orchestrator adds NO wall-clock gate. All gating is the
  abort flags + the existing arrival scene's own event/Status gates.
- **INV-11 (idempotent map totality).** `_SECTION_TO_PROC` is asserted to cover exactly
  `{"docking","exploration","traversal"}` at import; an `assert` guards drift.
- **INV-12 (external signature preserved).** Both arrival sites still `return "arrival"` to
  `run_event_routes` / `classify_startup`, so registry-level behavior (and the existing
  `test_activation_e2e` arrival assertion) is unchanged.

---

## 3. Acceptance criteria

1. New module surface (`_SECTION_TO_PROC`, `transition_to`, `run_arrival_then_branch`,
   `_transition_aborted`, `_arrival_branch`, `_dest_is_system`, `_dest_is_station`,
   `_exploration_active`) exists in flight code (`boot_routes.py` and/or `predicates.py`) — NOT only in
   a test file. (This is the BUILD deliverable; design-only is lifted.)
2. `_exploration_active` reads `_body_tour_enabled`; a grep proves `_exploration_mode` is NOT read by
   the new predicate. (LOCKED #9.)
3. Abort-recheck reads `_smacked`, `_preempt`, AND `should_abort()` at BOTH points (a) and (b).
   (LOCKED #10.)
4. `_dest_is_station` carries the literal `BLOCKED-ON-D1` marker and fails closed to Traversal/park.
   No unverified `Body != 0` schema is hardcoded as confirmed outside this marked predicate.
5. All five arrival-dispatch sites route through `run_arrival_then_branch` (live FSDJump + restart
   primary + 3 legacy branches). Restart-into-completed-park still idles (unchanged).
6. The acceptance suite (≥29 tests per the C2 design) is GREEN under the repo-root config
   (`pytest -m 'not requires_game'`). The full existing suite's green count does not regress
   (pinned 1581-green / 16-red invariant per `pytest.ini:11`).
7. No core→domain import introduced (DAG gate green).
8. The test suite is actually RUN and its output shown before any "pass" is claimed (operator mandate).

---

## 4. Concrete executable acceptance tests

New file: **`projects/ed-autojump/tests/flow/test_c2_orchestrator.py`** (collected by `pytest.ini:15`;
PURE — no game/CV/network; imports the REAL flight-code symbols from `ed_autojump.flow.boot_routes`
and `ed_core.flow.predicates`, NOT a reference copy). The 29 from the design council
(`worktrees/.../test_c2_section_transition.py`) are PORTED to import flight code, MINUS the
`_exploration_mode` tests (replaced per §7) PLUS the abort-recheck tests below.

Test harness: a minimal `_Runner` stand-in mirroring `tests/flow/__init__.py:FakeSender` discipline —
records `_run` dispatches, carries `_latest_status`, `_current_system`, `_navroute_state()`,
`_body_tour_enabled`, `_smacked`, `_preempt`, `_running_proc`, `_should_abort()`, and `procedures`.
Status/NavRoute built via the REAL parsers (`parse_status`, `parse_navroute`).

### Group A — `transition_to` fail-closed (INV-1, INV-3)
- `test_transition_dispatches_section_proc`: `transition_to(r,"traversal")=="traversal"`, dispatched==["traversal"].
- `test_transition_docking_maps_to_dock_proc`: returns "dock"; dispatched==["dock"].
- `test_transition_unknown_section_returns_empty_no_dispatch`: `=="" ` and dispatched==[].
- `test_transition_missing_proc_fail_closed`: section known, proc absent → "" , dispatched==[].

### Group B — abort-recheck at point (b), inside `transition_to` (INV-3, INV-4)
- `test_transition_suppressed_when_smacked`: `r._smacked=True` → `transition_to(r,"traversal")==""`, dispatched==[].
- `test_transition_suppressed_when_preempt_set`: `r._preempt="star_smack"` → "" , dispatched==[].
- `test_transition_suppressed_when_should_abort`: `r._should_abort` returns True → "" , dispatched==[].
- `test_transition_recheck_precedes_run_clearing_preempt`: assert the abort read happens BEFORE
  `runner._run` (which would clear `_preempt`): with `_preempt` set, dispatched stays [] (proves order).

### Group C — `run_arrival_then_branch` abort-recheck at point (a) (INV-2, INV-4)
- `test_branch_suppressed_after_smack_landing`: arrival runs, then `_smacked=True` before branch →
  dispatched==["arrival"] only (no section proc); return=="arrival".
- `test_branch_suppressed_after_preempt`: `_preempt="star_smack"` post-arrival → dispatched==["arrival"].
- `test_branch_suppressed_on_operator_abort`: `_should_abort()->True` post-arrival → dispatched==["arrival"].
- `test_smack_mid_arrival_does_not_branch_into_exclusion_zone`: `_running_proc="arrival"`,
  `_smacked=True`, `_preempt="star_smack"` → dispatched==["arrival"]; NO "dock"/"traversal"/"exploration".
- `test_clean_arrival_branches`: no abort flags, onward route → dispatched==["arrival","traversal"].

### Group D — arrival branch table + precedence (INV-6, ports design AC4)
- `test_arrived_no_route_goes_docking`; `test_arrived_local_star_goes_docking`;
  `test_onward_hop_exploration_off_goes_traversal`; `test_onward_hop_exploration_on_goes_exploration`;
  `test_precedence_docking_beats_exploration`; `test_station_dest_with_onward_route_not_docking_yet`.

### Group E — exploration source per LOCKED #9 (INV-5, REPLACES design's `_exploration_mode` tests)
- `test_exploration_active_reads_body_tour_enabled_true`: `_body_tour_enabled=True` → True.
- `test_exploration_active_false_when_body_tour_disabled`: `_body_tour_enabled=False` → False.
- `test_exploration_active_ignores_phantom_exploration_mode`: `_exploration_mode=True` BUT
  `_body_tour_enabled=False` → False (proves the phantom is NOT read).
- `test_branch_uses_body_tour_for_exploration`: `_body_tour_enabled=True`, onward hop → "exploration".

### Group F — discriminators over REAL parsers (INV-7, ports design AC3)
- `test_dest_is_system_primary_empty_route`; `test_dest_is_system_corroborant_local_star`;
  `test_dest_is_system_false_next_hop_star`; `test_dest_is_system_false_route_to_station`;
  `test_dest_is_system_fail_closed_none_route_is_terminal`;
  `test_dest_is_system_unknown_system_non_empty_route_false`.

### Group G — D1 isolation (INV-8)
- `test_dest_is_station_true_for_named_body`; `test_dest_is_station_false_for_star_hop`;
  `test_dest_is_station_false_for_symbolic_beacon`; `test_dest_is_station_fail_closed_on_none`.
- `test_dest_is_station_carries_d1_marker`: read the source of `boot_routes`/`predicates`, assert the
  literal substring `BLOCKED-ON-D1` is present in the `_dest_is_station` definition (guards the seam).

### Group H — totality + wiring (INV-11, INV-12)
- `test_section_map_total`: `set(_SECTION_TO_PROC)=={"docking","exploration","traversal"}`.
- `test_arrival_route_still_returns_arrival_signal`: a stubbed `_route_fsd_jump`-equivalent path still
  yields the `"arrival"` external signal (registry contract unchanged).

### Run command (must be executed; output shown)
```
pytest projects/ed-autojump/tests/flow/test_c2_orchestrator.py -q
pytest -q          # full suite: assert no green regression vs pinned 1581/16
```

---

## 5. Out of scope / explicitly deferred
- The D1 game mechanic itself (live undock→plot-station→read Status.json). The CODE seam ships now,
  fail-closed; D1 confirmation is an operator live-test, not a code task.
- The Docking / Exploration / Traversal SCENE bodies (C7/C6/C5) — the orchestrator only TRANSITIONS to
  their procedures; their internals are other councils.
- The `scoop refuel_below 0.70→0.50` knob and honk reconciliation (C2 design AC5/AC6) — separate edits,
  not part of the orchestrator machinery; do NOT bundle.

## 6. Failure routing for downstream stages
- **Spec-conformance fail** (e.g. orchestrator reads `_exploration_mode`; abort-recheck missing at
  either point; D1 schema hardcoded as confirmed) → Stage 0.
- **Blocker fail** (e.g. a smack-mid-transition test proves the bot CAN branch into the exclusion zone;
  core→domain import introduced; green count regresses) → halt, route back, no override.
- **No verdict without an `evidence_artifact`** (the actual pytest run output). A `pass` lacking it is
  treated as `abstain`.

## 7. DEVIATION the build MUST resolve (surfaced, not patched silently)
The C2 **design-council reference** (`worktrees/wf_449df9ea-091-2/.../test_c2_section_transition.py:63-66`)
defined `exploration_active` as `bool(getattr(runner,"_exploration_mode",False))`. The operator's
**LOCKED #9** (post-dating that council, `CONSOLIDATED-BLOCKERS.md:39-41`, 2026-06-18) supersedes it:
read `body_tour_enabled`. **This spec follows LOCKED #9.** The build ports the design's 29 tests but
DROPS `test_exploration_active_fail_closed_unwired` / `test_exploration_active_true_when_set`
(they assert the phantom) and REPLACES them with Group E. Any candidate that ships `_exploration_mode`
is a **spec-conformance fail → Stage 0**.
