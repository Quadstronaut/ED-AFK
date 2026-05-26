# ed-autojump v1 — Procedure Interpreter (design)

> **Status:** approved design, not yet implemented.
> **Author/operator:** Quadstronaut (10-yr ED veteran).
> **Date:** 2026-05-25.
> **Supersedes:** the escape/engage logic in `orchestrator.py` (to be retired)
> and the four overlapping escape strategies in `executor/escape.py`.

---

## 1. Why this exists

Component 1 of the autoexploration bot is **A→B automated navigation**: arrive
in a system, honk, get clear of the arrival star, orient at the next route star,
jump, repeat. The current implementation is a 1,100-line orchestrator with four
escape modes and a vision-gated engage path. It has never produced a workable
end-to-end test, and it rams the star.

Two things are wrong, in priority order:

1. **The ram (primary, operator-observed).** On arrival the bot targets the next
   route system and the compass-orient points the nose at it. The orient *works*
   — it hits the jump coordinate. The bug is that the coordinate is frequently
   **obscured behind the arrival star**, so "pointing at the target" means
   pointing *through the star*, and the jump throttles into it. The script never
   gets *around* the star. The fix is the operator's original maneuver: engage
   **Supercruise Assist to orbit the star** (it settles at a safe altitude
   ~99.99% of the time), which moves the ship's angular position so the next jump
   is unobstructed — then orient and jump.

2. **Fail-open safety (secondary, latent).** The "don't throttle into the star"
   property is entirely vision-dependent and fails *open*:
   - `orchestrator.py:_aligned_for_engage()` returns `True` when vision is
     disabled/unwired, so the engage gate presses `SetSpeed100` +
     `HyperSuperCombination` blind.
   - `orchestrator.py:_startup_compass_escape()` clears `_startup_escape_pending`
     when the compass is unavailable, unlocking the engage gate even though the
     ship never left the star.
   When CV degrades, the bot throttles and jumps anyway. It must fail **closed**.

This redesign replaces the orchestrator's control flow with a small, testable
**procedure interpreter** driven by **human-editable step-list files**, and makes
the jump fail closed.

### 1.1 What the operator asked for (verbatim intent)

> "the ability to specify what actions it takes for each named procedure … files
> easily accessible in the repo and easy for a human to understand, and to
> reorder actions. duplicate actions allowed … an iteration of a list of actions
> to be performed step by step with the honk being the 1 parallel aspect."

---

## 2. Goals / non-goals (v1)

### Goals
- A→B route jumping that does **not** ram the star.
- Each named procedure = an **ordered, human-editable list of steps** in its own
  file under `procedures/`. Reorder by moving lines; duplicate steps freely.
- **Honk** runs as the one parallel track, terminated by a journal log match.
- The jump **fails closed**: it fires only after orientation is positively
  confirmed; any required-step failure aborts the procedure without throttling.
- Reuse the validated components: journal tail, status reader, key sender,
  cyan-dot compass reader (`align_to_target`), nav-panel SC-assist macro.

### Non-goals (v1 — disabled or removed)
- FSS, DSS, docking, EDDN publishing, spansh auto-plot, the launcher/menu-nav
  wizard, brightness/HUD detection.
- Refueling (revisited later; scooping is NOT done via SC-assist — see
  `refuel-not-via-sc-assist`).
- The `brightness`, `sc_assist`, and `blind` escape modes in `escape.py`.

---

## 3. Architecture

Three parts replace the orchestrator's escape/engage tangle:

```
 journal tail ─┐
 status reader ─┼─► Dispatcher ──► Interpreter ──► Step library ──► Sender (DirectInput)
 compass reader ┘     │  (event→        │ (runs a        │ (one tested
                      │   procedure)    │  procedure's   │  function per
                      │                 │  steps in      │  primitive)
                      │                 │  order)        │
                      └─ procedures/*.toml (editable, ordered step lists)
```

- **Step library** — one small, individually tested function per primitive
  action. Pure w.r.t. injected `sender`/`clock`/`sleeper`/readers (the codebase
  already injects these everywhere for tests).
- **Interpreter** — loads a procedure (a list of `{action, **params}` dicts) and
  runs the steps top-to-bottom. Tracks per-step success. A step flagged
  `required = true` that fails triggers the procedure's `on_required_fail`
  policy (retry-from-step N times, else abort). **Aborting never throttles or
  jumps.**
- **Dispatcher** — owns the journal tail + status reader (lifted from the
  orchestrator), and maps live events to procedures: a LIVE `FSDJump` runs
  `arrival`; a fresh-load-at-star condition runs `startup`; a
  `SupercruiseExit` with `BodyType == "Star"` runs `smack_recovery`. Replay
  events only update state; they never act (existing behaviour, preserved).

### 3.1 Parallel honk

Honk is the single parallel track. At the start of `arrival`/`startup` the
dispatcher launches the `honk` procedure on a background thread: hold the
discovery-scanner key, **terminate on the `FSSDiscoveryScan` journal match** (or
a hard timeout). The main procedure's steps run concurrently. Honk is a plain
key hold — independent of the journal-stream generator (see
`keypresses-are-independent`).

---

## 4. Step library (v1 primitives)

Each step is `{ action = "<name>", <params> }`. Every step returns
`ok: bool`. `required = true` (default `false`) makes a failure trigger
`on_required_fail`.

| action | params | does | fails when |
|---|---|---|---|
| `press` | `bind`, `hold_s=0.05` | press a bound ED action for `hold_s` | bind unbound |
| `wait` | `s` | sleep `s` seconds | never |
| `set_throttle` | `pct` ∈ {0,25,50,75,100} | press the matching `SetSpeedN` | bind unbound |
| `pitch` | `dir` ∈ {up,down}, `hold_s` | hold Pitch{Up,Down}Button (dead-reckoned) | bind unbound |
| `wait_for_event` | `event`, `timeout_s` | block until the journal logs `event` | timeout |
| `target_ahead` | — | press `SelectTarget` (lock the body in the reticle) | bind unbound |
| `target_next_route` | — | press `TargetNextRouteSystem` (also cancels SC-assist) | bind unbound |
| `sc_assist_orbit` | `settle_s=0.4` | run `navpanel.engage_supercruise_assist` (orbit the star, get around it) | any bind unbound |
| `engage_supercruise` | `timeout_s=30` | press `Supercruise`, then confirm SC entry via Status flag | SC entry not logged in `timeout_s` |
| `orient_compass` | (vision knobs from `[vision]`) | run `align_to_target` (cyan compass) | not aligned / no compass wiring |
| `engage_jump` | — | re-check status (not docked/mass-locked/cooldown), `SetSpeed100`, `HyperSuperCombination` | blocked flag, or bind unbound |

Notes:
- `orient_compass` and `engage_jump` are the steps normally marked `required` —
  that is what makes the jump **fail closed**. If `orient_compass` fails,
  `engage_jump` is never reached.
- `sc_assist_orbit` wraps the existing, tested `executor/navpanel.py`. SC-assist
  has no keybind; the macro walks the left nav panel
  (`FocusLeftPanel → UI_Select → UI_Right → UI_Select → close`). The arrival star
  is the auto-selected top row, so the macro is deterministic and vision-free.

---

## 5. Procedure file format

One file per procedure under `projects/ed-autojump/procedures/`. TOML — no new
dependency (`tomllib` already loads `config.toml`), comment-friendly, and its
array-of-inline-tables maps directly to an ordered, reorderable step list.

Schema per file:

```toml
# Optional: this procedure is a parallel track, terminated by a journal match.
parallel = false                       # default
stop_on_event = "FSSDiscoveryScan"     # only meaningful when parallel = true
timeout_s = 12.0                        # hard cap for parallel tracks

# Optional failure policy for required steps (default: abort immediately).
[on_required_fail]
retry_from = "orient_compass"          # step action to resume from
max_retries = 3
backoff_s = 2.0

# The ordered step list. Reorder by moving lines. Duplicates allowed.
steps = [
  { action = "...", required = false, <params> },
]
```

The loader validates: every `action` exists in the step library, every `bind`
referenced is resolvable in the active preset, and `retry_from` names a real
step. Validation failures are reported at startup (fail fast, readable) — the
bot refuses to run an invalid procedure rather than improvising.

---

## 6. The v1 procedures

### `procedures/honk.toml`
```toml
parallel = true
stop_on_event = "FSSDiscoveryScan"
timeout_s = 12.0
steps = [
  { action = "press", bind = "ExplorationFSSDiscoveryScan", hold_s = 6.0 },
]
```

### `procedures/arrival.toml`  (runs on every LIVE `FSDJump`; ship is in supercruise)
```toml
[on_required_fail]
retry_from = "orient_compass"
max_retries = 3
backoff_s = 2.0

steps = [
  { action = "target_ahead" },                       # lock the arrival star (nav-panel top row)
  { action = "sc_assist_orbit" },                    # orbit AROUND the star -> unobstruct the next hop
  { action = "wait", s = 10.0 },                     # orbit settles; honk finishes in parallel
  { action = "target_next_route" },                  # H: cancels SC-assist + locks next system
  { action = "set_throttle", pct = 100 },
  { action = "wait", s = 10.0 },
  { action = "orient_compass", required = true },    # cyan compass; target now UNOBSTRUCTED; fails closed
  { action = "engage_jump", required = true },       # SetSpeed100 + FSD; only after orient confirms
]
```

### `procedures/startup.toml`  (fresh load: ship sits at the star in NORMAL space)
```toml
[on_required_fail]
retry_from = "orient_compass"
max_retries = 3
backoff_s = 2.0

steps = [
  { action = "target_ahead" },
  { action = "set_throttle", pct = 100 },            # normal-space throttle to engage the FSD
  { action = "engage_supercruise", required = true },# confirm SC entry via Status flag (fail closed)
  { action = "sc_assist_orbit" },
  { action = "wait", s = 10.0 },
  { action = "target_next_route" },
  { action = "set_throttle", pct = 100 },
  { action = "wait", s = 10.0 },
  { action = "orient_compass", required = true },
  { action = "engage_jump", required = true },
]
```

### `procedures/smack_recovery.toml`  (reflex: `SupercruiseExit` with `BodyType:Star`)
```toml
steps = [
  { action = "pitch", dir = "up", hold_s = 4.0 },    # nose off the star
  { action = "set_throttle", pct = 100 },
  { action = "wait_for_event", event = "SupercruiseEntry", timeout_s = 60.0 },  # FSD cooldown ~45s
  { action = "wait", s = 7.0 },
]
```

These are the editing surface. Every number is a live-tune knob in execution
order.

---

## 7. Component reuse and removal

### Reused (kept)
- `journal/` (tail, events, waiters), `status/`, `keys/` (sender, binds,
  scancodes), `state.py`, `panic*` (safety abort), `session_audit.py` /
  `recorder.py` (outcome logging).
- `vision/` cyan-dot compass stack + `executor/align.py` (`align_to_target`).
- `executor/navpanel.py` (SC-assist orbit) — **not** extraneous.
- `executor/smack_recovery.py` logic, re-expressed as a procedure.

### Removed / disabled for v1
- `orchestrator.py` escape + engage methods → replaced by Dispatcher +
  Interpreter. (The journal/status plumbing inside it is lifted out, not lost.)
- `executor/escape.py` — the `brightness`, `sc_assist`, `blind`, and compass
  escape functions are retired; the pitch-under maneuver is no longer the escape
  (orbit-around-star is). Keep only what a step needs.
- `executor/fss.py`, `executor/dss.py`, `executor/refuel.py`, `docking/`,
  `launcher/`, `hud/`, `eddn/`, `planner/` — zero live callers for v1
  (audit-confirmed). Left on disk but unwired, or deleted, per the plan.
- `[escape]` config section collapses: the four-mode knobs move into procedure
  files. `[escape]` is removed from `config.toml` and `config.py`.

---

## 8. Failure semantics (fail closed)

- A non-`required` step that fails is logged and the procedure continues.
- A `required` step that fails runs `on_required_fail`: resume from
  `retry_from`, up to `max_retries`, with `backoff_s` between attempts. After the
  retries are exhausted, the procedure **aborts** — no throttle, no jump. The
  dispatcher records the abort and waits for the next trigger.
- `engage_jump` additionally re-checks Status flags immediately before pressing
  (docked / mass-locked / FSD cooldown / overheating) and refuses if any block.
- There is **no path** where a degraded compass leads to a throttle-forward.
  That is the behavioural contract this design exists to guarantee.

---

## 9. Testing strategy

- **Step library:** one unit test per primitive with an injected fake sender +
  clock + sleeper, asserting exact key sequences and fail/return behaviour
  (mirrors existing `tests/test_escape.py`, `test_align.py`, `test_navpanel.py`).
- **Interpreter:** drive a fixture procedure list through a recording sender;
  assert step order, that `required` failures trigger retry/abort, that an abort
  never emits a throttle/jump key, and that the parallel honk track starts and is
  joined on the stop event.
- **Procedure-file validation:** load every `procedures/*.toml`; assert all
  actions/binds/`retry_from` resolve. A CI test fails on an invalid procedure.
- **Dispatcher wiring:** feed fixture journals (reuse
  `tests/fixtures/journals/`) and assert the right procedure runs for
  `FSDJump` / startup / star-smack.
- Prune orchestrator/escape tests that cover removed paths; port the still-valid
  assertions (danger-class refusal, retarget, smack recovery) to the new layer.

---

## 10. Risks & open questions

- **Orbit duration.** `wait s=10` after `sc_assist_orbit` is a guess for "enough
  angular travel to clear the star." Tune live; it is a one-line knob. Risk: a
  very large/close star may need longer, or the SC-assist may not have reached a
  stable orbit in 10 s.
- **`target_next_route` cancelling SC-assist.** `navpanel.py` documents that
  `TargetNextRouteSystem` both cancels assist and locks the next star. Verify in
  flight that one press does both on the operator's build.
- **Compass region vs resolution.** `[vision].region` was calibrated at
  1920×1080 but `[cv].target_resolution` is 2560×1440. Confirm the live game
  resolution and recalibrate `region` before trusting `orient_compass`.
- **SC-assist availability.** `sc_assist_orbit` assumes the ship has Supercruise
  Assist fitted and the nav-panel layout the macro expects. Pre-flight check
  should confirm the module (extend the existing `Loadout` scan).
- **Retry-from after orbit.** `retry_from = "orient_compass"` re-runs orient
  without re-orbiting. If a failed orient means the geometry is still obstructed,
  a second orient will also fail; consider `retry_from = "sc_assist_orbit"` if
  flight testing shows that.
```
