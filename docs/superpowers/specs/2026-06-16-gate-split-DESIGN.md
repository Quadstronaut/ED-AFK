# Gate Split Design — ARRIVAL / REFUEL / TRAVERSAL / EXPLORATION

**Design doc bound to:** `2026-06-16-gate-split-STAGE0-SPEC.md`
**Candidate:** gen-sonnet-2
**Scope:** re-partition + relabel + scene-transition ONLY. No new flight behavior.

---

## 1. Scene + Handoff Table (AC1)

| Gate | Job | Journal/Status Trigger (no wall-clock) | Hands off to |
|---|---|---|---|
| **ARRIVAL** | Kick honk track (background); evaluate fuel level. NO scoop, NO orbit, NO jump. | `FSDJump` event → `_route_fsd_jump` arms arrival latch | → REFUEL (if `low_fuel OR scooping_fuel`) · → TRAVERSAL (fuel ok) |
| **REFUEL** | Scoop pit stop only. Best-effort; backstop/skip never aborts. | `ScoopingFuel` Status flag clears (or `budget_s` backstop) | → TRAVERSAL (sequential chain: `_det_refuel` → False, `_det_traversal` → True) |
| **TRAVERSAL** | Get-around (orbit/wait), in-system explore, lock next route, orient, jump. Owns the fail-closed chain. | `engage_jump_clearance` fires `StartJump` → `FSDJump` | → ARRIVAL (FSDJump fires, latch re-armed) |
| **EXPLORATION** | Distinct procedure/label for exploration_mode. In-system tour only. NO required `target_next_route` on empty route. | Route appears (`route_empty` flips False via NavRoute event) → re-determine to TRAVERSAL | → TRAVERSAL (scene re-determination once route present) |

---

## 2. Step Partition (AC2)

**Installed `arrival.toml` 13 steps → new procedures (position-by-position, INV-D):**

`arrival.steps ++ refuel.steps ++ traversal.steps = 13 installed steps (exact position + content match)`.
EXPLORATION is a separate procedure with its own distinct steps (not drawn from this partition).

| # | Action | Params (verbatim) | req | skip_to | New Procedure |
|---|---|---|---|---|---|
| 1 | `set_throttle` | `pct=0` | false | — | **arrival** |
| 2 | `nav_panel_target` | _(none)_ | false | — | **arrival** |
| 3 | `scoop_refuel` | `approach_pct=25, standoff_frac=0.80, rate_window_s=2.0, budget_s=300.0, refuel_below=0.70, full_epsilon=0.2` | false | — | **refuel** |
| 4 | `nav_panel_target` | `max_rows=3` | false | `"target_next_route"` | **TRAVERSAL** |
| 5 | `sc_assist_orbit` | _(none)_ | false | — | **TRAVERSAL** |
| 6 | `wait` | `s=13.0` | false | — | **TRAVERSAL** |
| 7 | `explore` | _(none)_ | false | — | **TRAVERSAL** |
| 8 | `station_strand_recovery` | _(none)_ | false | — | **TRAVERSAL** |
| 9 | `target_next_route` | _(none)_ | **true** | — | **TRAVERSAL** |
| 10 | `set_throttle` | `pct=100` | false | — | **TRAVERSAL** |
| 11 | `orient_compass` | _(none)_ | **true** | — | **TRAVERSAL** |
| 12 | `orient_widget_ring` | _(none)_ | **true** | — | **TRAVERSAL** |
| 13 | `engage_jump_clearance` | _(none)_ | **true** | — | **TRAVERSAL** |

**Partition notes:**
- arrival(2) + refuel(1) + TRAVERSAL(10) = **13**. Each of the 13 original steps maps to EXACTLY ONE new procedure. (INV-D)
- Step 4's `skip_to="target_next_route"` resolves WITHIN TRAVERSAL (traversal step index 5, action `target_next_route`). No dangling skip. (INV-C)
- **Honk owner:** `arrival` sole `parallel_tracks=["honk"]`. REFUEL/TRAVERSAL/EXPLORATION declare none — honk fires once per system. (INV-I)
- **Retry anchor:** `TRAVERSAL [on_required_fail] retry_from="nav_panel_target" max_retries=3 backoff_s=2.0`. Names traversal's own step 0. (INV-J; see OQ §5)
- ARRIVAL: NO required steps, NO `engage_jump_clearance`.
- REFUEL: NO required steps, NO `engage_jump_clearance`.
- EXPLORATION is a **separate** procedure (not counted in the 13-step partition) with its own steps: `[explore, station_strand_recovery]`, distinct from TRAVERSAL's copy of those actions.

**Procedure file summaries:**

```toml
# arrival.toml (new — brief handoff)
parallel_tracks = ["honk"]
# NO [on_required_fail] — no required steps

steps = [
  { action = "set_throttle", pct = 0 },
  { action = "nav_panel_target" },
]
```

```toml
# refuel.toml (new — scoop only)
# NO parallel_tracks, NO [on_required_fail] — no required steps

steps = [
  { action = "scoop_refuel", approach_pct = 25, standoff_frac = 0.80, rate_window_s = 2.0, budget_s = 300.0, refuel_below = 0.70, full_epsilon = 0.2 },
]
```

```toml
# TRAVERSAL.toml (new — get-around + explore + orient + jump)
# Filename upper-case so proc.name=="TRAVERSAL" (loader uses p.stem). (AC4 / spec literal)
[on_required_fail]
retry_from = "nav_panel_target"   # in-procedure step index 0 (INV-J); see OQ §5
max_retries = 3
backoff_s = 2.0

steps = [
  { action = "nav_panel_target", required = false, skip_to = "target_next_route", max_rows = 3 },
  { action = "sc_assist_orbit" },
  { action = "wait", s = 13.0 },
  { action = "explore" },
  { action = "station_strand_recovery" },
  { action = "target_next_route", required = true },
  { action = "set_throttle", pct = 100 },
  { action = "orient_compass", required = true },
  { action = "orient_widget_ring", required = true },
  { action = "engage_jump_clearance", required = true },
]
```

```toml
# exploration.toml (new — distinct procedure/overlay label, NOT a TRAVERSAL alias)
# Runs in-system tour when exploration_mode=True + route empty.
# NO required target_next_route (route is empty when this runs). (INV-E / PIN-D)
# Onward: re-derives to TRAVERSAL once route appears (sequential chain).

steps = [
  { action = "explore" },
  { action = "station_strand_recovery" },
]
```

---

## 3. Determination Changes (AC3)

### `_STATE_TO_PROC` edits (3 changes)

```python
# boot_routes.py — three lines change:
CSeriesState.REFUEL:      ("run", "refuel"),      # was ("fallback", None)
CSeriesState.TRAVERSAL:   ("run", "TRAVERSAL"),   # was ("fallback", None)
CSeriesState.EXPLORATION: ("run", "exploration"), # was ("fallback", None)
```

Map stays TOTAL over all 11 `CSeriesState` (import-time assert still holds). (INV-K)

### Mid-flight ARRIVAL → REFUEL / TRAVERSAL transition (INV-H)

**Mechanism: sequential chaining inside `_route_fsd_jump`.**

The one-shot guard (`_startup_done`) is consumed only by `classify_startup`. The live `_route_fsd_jump` (boot_routes.py:507-530) calls `runner._run("arrival")` directly — it does NOT go through `classify_startup` and does NOT re-trip the one-shot guard. Sequential chaining here is safe.

After the new `arrival.toml` completes, `_route_fsd_jump` reads telemetry and chains to REFUEL or TRAVERSAL:

```python
def _route_fsd_jump(runner, ev):
    _reset_refuel_attempts(runner)       # PIN-F: fresh jump, reset cap
    runner._jumps += 1
    _ensure_latch(runner).arm()
    # ... overlay event ...
    if _is_route_complete(runner, ev):
        runner._navroute_cleared = False
        dispatch_route_complete(runner, ev)
        return "route_complete"

    runner._run("arrival")               # honk + fuel eval
    if runner._smacked or runner._preempt is not None:
        return "arrival"                 # gap-smack guard (PIN-E)

    # Fuel check → chain to REFUEL if needed
    st = runner._latest_status
    low_fuel = getattr(st, "low_fuel", False) if st else False
    scooping = getattr(st, "scooping_fuel", False) if st else False
    in_sc = getattr(st, "in_supercruise", False) if st else False
    if (scooping or (low_fuel and in_sc)) and not _refuel_cap_exceeded(runner):
        _increment_refuel_attempts(runner)
        runner._run("refuel")
        if runner._smacked or runner._preempt is not None:
            return "arrival"             # gap-smack guard after refuel

    runner._run("TRAVERSAL")            # get-around, orient, jump
    return "arrival"
```

This does NOT re-use `classify_startup`; the one-shot guard is never double-consumed. (INV-H)

### `_PREEMPT_ON_SMACK` additions (INV-F)

```python
# dispatcher.py line 37 — proposed frozenset:
_PREEMPT_ON_SMACK = frozenset({
    "arrival", "startup", "dock", "sc_resume",   # originals (preserved)
    "refuel", "TRAVERSAL", "exploration",         # new — these fly live SC scenes
})
```

### Gap-smack guard (PIN-E) — INV-F

Between two sequential `_run()` calls, `_running_proc is None` (dispatcher.py:571). A `SupercruiseExit(Star|Planet)` in the gap sets `_smacked` (line 643, unconditional) but NOT `_preempt` (line 628, guarded by `_running_proc in _PREEMPT_ON_SMACK`). `_should_abort()` reads only operator signals — NOT `_smacked` or `_preempt`.

The transition loop between sequential `_run()` calls MUST gate on:

```python
if runner._smacked or runner._preempt is not None:
    return "arrival"   # abort chain; smack_recovery dispatches from tail event
```

**NOT** `runner._should_abort()` alone — that would miss the gap case.

### Bounded-refuel cap (PIN-F / INV-G)

Because REFUEL (scene idx 4) outranks TRAVERSAL (scene idx 6), a backstopped scoop with `_det_refuel` still True would re-enter REFUEL indefinitely.

**Design: per-arrival refuel attempt counter on the runner.**

```python
def _reset_refuel_attempts(runner):
    """Call at the top of _route_fsd_jump (each new jump resets)."""
    runner._refuel_attempts_this_arrival = 0

def _increment_refuel_attempts(runner):
    runner._refuel_attempts_this_arrival = getattr(runner, "_refuel_attempts_this_arrival", 0) + 1

def _refuel_cap_exceeded(runner, max_attempts=1):
    """True iff refuel has run >= max_attempts times this arrival."""
    return getattr(runner, "_refuel_attempts_this_arrival", 0) >= max_attempts
```

With `max_attempts=1`: refuel runs at most once per FSD jump. After 1 backstopped scoop, the cap activates and the chain skips REFUEL → control reaches TRAVERSAL. (INV-G)

---

## 4. Overlay Change (AC4)

**No code change required in `interpreter.py` or `overlay.py`.**

The label source is already `proc.name` (interpreter.py:84 → `ctx.overlay.step(proc.name, step.action, i+1, n)` → `overlay.py:222-223` → `status(f"{procedure} > {action} ({idx}/{total})")`).

The change is that distinct procedures now fly the legs previously all named "arrival". The procedure name changes; the format string does not.

**File naming:** `TRAVERSAL.toml` (upper-case filename) → `load_procedure` uses `p.stem` → `proc.name == "TRAVERSAL"`. This produces the spec's canonical label.

**Canonical before/after (per spec AC4 literal):**
| | String |
|---|---|
| **Before** | `arrival > orient_compass (8/13)` |
| **After** | `TRAVERSAL > orient_compass (8/10)` |

Note: `orient_compass` is traversal step index 8 of 10 (1-based), since TRAVERSAL has 10 steps.

---

## 5. Open Question (AC5)

**OQ: retry re-anchor after `scoop_refuel` moved to REFUEL**

Because `scoop_refuel` now lives in REFUEL (not TRAVERSAL), a TRAVERSAL required-failure can no longer use `retry_from="scoop_refuel"` — `validate_procedure` would error on the dangling name.

**DEFAULT (provisional — flagged for operator sign-off):**
TRAVERSAL `[on_required_fail] retry_from="nav_panel_target"` — re-establishes the star lock and pose by re-anchoring at TRAVERSAL's first step (the bounded star lock). Does NOT re-scoop. Preserves `max_retries=3`, `backoff_s=2.0`.

**Alternative (operator's call):**
A TRAVERSAL required-failure re-enters REFUEL / re-scoops via cross-procedure logic in `_route_fsd_jump`'s error handling (e.g. detect a TRAVERSAL abort and restart the chain from REFUEL). This is not adopted without operator sign-off — it adds cross-procedure retry complexity that isn't in the original design.

**Operator action required before implementation:** confirm DEFAULT or Alternative.

---

## 6. Risk / Scope Note (AC6)

**Scope confirmed:** re-partition + relabel + scene-transition ONLY.

**Preserved unchanged:**
| Item | Status |
|---|---|
| Each step's params (action names, all inline TOML values) | Preserved byte-for-byte in partition |
| Witchspace pause (interpreter.py:60-77) | Unchanged — no procedure edit needed |
| Fail-closed jump gate (`engage_jump_clearance required=true`, last step in TRAVERSAL) | Preserved |
| Smack preemption (mid-procedure) | Preserved + extended to refuel/TRAVERSAL/exploration |
| Smack preemption (inter-procedure gap) | Covered by `_smacked or _preempt` guard (new) |
| Honk track | Exclusively owned by arrival; fires once per system |
| `max_retries=3`, `backoff_s=2.0` | Preserved in TRAVERSAL `[on_required_fail]` |

**Risks:**
- EXPLORATION is a new scene (exploration_mode=True, route_empty=True). If a route never appears after the tour, EXPLORATION re-enters indefinitely. Low risk: exploration_mode is explicitly set by operator and the tour is bounded.
- The bounded-refuel cap uses a lazy runner attribute initialized to 0 on each FSD jump. If `_route_fsd_jump` is called multiple times without a reset (e.g. replay backlog), the cap may be stale. Design specifies `_reset_refuel_attempts` is always called first.
- `TRAVERSAL.toml` upper-case filename is non-standard. The `load_procedures(directory)` loader uses `p.stem` so it reads correctly; but callers using the string key `"TRAVERSAL"` must match exactly. The proposed `_STATE_TO_PROC` edit uses `("run", "TRAVERSAL")` to match.

---

*Proof artifact:* `docs/superpowers/specs/gate_split_artifacts/prove_gate_split.py`
