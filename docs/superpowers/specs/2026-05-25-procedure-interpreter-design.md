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
| `pitch` | `dir` ∈ {up,down}, `hold_s` | hold Pitch{Up,Down}Button (dead-reckoned, no vision) | bind unbound |
| `pitch_compass` | `until` ∈ {edge,behind}, pitch knobs | compass-gated pitch: hold PitchUp until the TARGETED star's dot reaches the rim (`edge` ≈ 90° off the nose) or goes centred + hollow (`behind` ≈ directly astern) | star not confirmed there within budget |
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
- `pitch_compass` repurposes the (otherwise-retired) compass-gated pitch loop
  from `escape.py:pitch_star_under` + the cyan reader. `until = "edge"` gates on
  the star's dot magnitude reaching the rim; `until = "behind"` gates on
  hollow + small offset (centred astern). It is the get-the-star-out-of-the-way
  primitive for `startup` and `smack_recovery` (arrival uses orbit instead).
- `target_ahead` does double duty: it presses `SelectTarget` ("Select Target
  Ahead"). With a body ahead it locks it; with **nothing** ahead (e.g. the star
  is now behind you) the same press **clears** the target — that is how
  `smack_recovery` "targets nothing" before igniting the FSD.

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
retry_from = "sc_assist_orbit"   # re-orbit changes the geometry; a 2nd orient at the same obstructed angle would just fail again
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
Get the star to the **edge** of the compass (nose off it), then full throttle to
engage the FSD, enter supercruise, full throttle **again** (SC throttle is a
separate axis), wait, orient, jump. No orbit — orbit is arrival-only.
```toml
[on_required_fail]
retry_from = "pitch_compass"
max_retries = 3
backoff_s = 2.0

steps = [
  { action = "target_ahead" },                              # lock the arrival star
  { action = "pitch_compass", until = "edge", required = true },  # star to the EDGE of the compass
  { action = "set_throttle", pct = 100 },                   # normal-space throttle to engage the FSD
  { action = "engage_supercruise", required = true },       # confirm SC entry via Status flag (fail closed)
  { action = "set_throttle", pct = 100 },                   # SC throttle is a SEPARATE axis — full again
  { action = "wait", s = 10.0 },
  { action = "target_next_route" },
  { action = "orient_compass", required = true },
  { action = "engage_jump", required = true },
]
```

### `procedures/smack_recovery.toml`  (reflex: `SupercruiseExit` with `BodyType:Star`)
You emergency-dropped INSIDE the star's exclusion zone. Face directly **away**
from the star (centred + hollow), wait out the long cooldown, clear the target,
then igniting the FSD spawns the game's **escape-vector** compass marker — fly
that out of the exclusion zone into supercruise, put the star on the compass
edge, fly clear for a longer 15 s (deeper gravity well), then orient and jump.
```toml
[on_required_fail]
retry_from = "pitch_compass"
max_retries = 3
backoff_s = 2.0

steps = [
  { action = "target_ahead" },                                    # lock the star (you dropped facing it)
  { action = "pitch_compass", until = "behind", required = true },# star CENTRED + HOLLOW (directly astern)
  { action = "wait", s = 45.0 },                                  # FSD cooldown after an emergency drop (~45 s)
  { action = "target_ahead" },                                    # nothing ahead now -> CLEARS the star target
  { action = "press", bind = "Supercruise" },                     # ignite FSD -> spawns the escape-vector marker
  { action = "set_throttle", pct = 100 },
  { action = "orient_compass", required = true },                 # fly the escape vector out of the exclusion zone
  { action = "wait_for_event", event = "SupercruiseEntry", timeout_s = 30.0, required = true },
  { action = "pitch_compass", until = "edge", required = true },  # in SC now: star to the EDGE of the compass
  { action = "wait", s = 15.0 },                                  # fly clear 15 s (stronger gravity well post-smack)
  { action = "target_next_route" },
  { action = "orient_compass", required = true },
  { action = "engage_jump", required = true },
]
```

> **Flight-verify (smack):** the exact decomposition of the escape-vector phase
> — whether one `press Supercruise` + orient + throttle is enough to clear the
> zone and auto-enter SC, or whether SC must be re-pressed once clear — is
> modelled here as best-understood and must be confirmed in a supervised run.
> Because every step is data, correcting it is a file edit, not a code change.

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
- `executor/escape.py` — the `sc_assist` and `blind` escape functions are
  retired. The compass `pitch_star_under` loop is **repurposed** into the
  `pitch_compass` step (not deleted). The **brightness** sun-avoid code is
  **archived, not removed**: moved to `projects/ed-autojump/archive/brightness/`
  with a header noting the v2 plan (a grid of brightness checks for directional
  star response — see §11). It must stay out of the live import graph but remain
  in the repo for v2 to bust back out.
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

## 10. Decisions & open questions

### Resolved (operator)
- **Display resolution.** The game runs **at 1080p** on the main monitor and the
  hardware never goes higher. `[vision].region` calibrated at 1920×1080 is
  therefore correct. Action: fix `[cv].target_resolution` to `[1920, 1080]` (the
  `2560×1440` value is stale and misleading).
- **SC-assist & Advanced Docking Computer are assumed fitted** for v1. No
  pre-flight gating on them; accommodating ships *without* them is a later scope.
- **Retry after a failed orient** resumes from the get-around step
  (`sc_assist_orbit` for arrival, `pitch_compass` for startup/smack), not from
  `orient_compass` — re-running orient at the same obstructed angle would just
  fail again. Confirmed: a failed orient generally means the geometry is still
  obstructed.
- **Orbit duration** (`wait s=10`) acknowledged as a live-tune knob.

### Open / verify-in-flight
- **`target_next_route` cancelling SC-assist.** Operator believes one press both
  cancels assist and locks the next star ("i think so"). Verify in a supervised
  run; if it doesn't, add an explicit deactivate step (the macro is symmetric).
- **Smack escape-vector decomposition.** See the flight-verify note under
  `smack_recovery` in §6.
- **Compass vision is "jerky" (v1-acceptable).** The orient is a low-resolution,
  move-check-move-check loop that lands "within tolerance." Fine for v1
  (functional is the bar). Smoothness is a v2 goal — see §11.
- **Realtime compass vision (research).** *How do we get high-resolution,
  near-realtime compass reads so the ship turns smoothly instead of
  jank-stepping?* Carried as a v2 research project; the operator has a plan to
  gather compass training data in an automated fashion.

---

## 11. Scope boundary & v2 earmarks

**v1 is deliberately narrow: functional A→B route jumping that doesn't ram the
star.** The design is built to *scale* into the rest without rework:

- **Procedures are independent files.** Adding `docking`, `undocking`,
  `refuel_via_purchase`, mining, Robigo, exobiology, etc. is dropping new files
  in `procedures/` and (where needed) new primitives in the step library — no
  surgery on existing procedures. This is the whole point of the editable-list
  architecture. Those procedures are **out of v1 scope**.
- **Brightness / directional star sensing (v2).** The archived brightness code
  (§7) returns in v2 as a **grid of brightness checks** enabling *directional*
  response to stars (know which way the star is, not just "bright ahead"). Kept
  in `archive/brightness/`, out of the live path.
- **High-resolution, near-realtime compass vision (v2).** Replace the jerky
  low-res orient with an accurate, smooth closed loop so the ship turns fluidly
  rather than stepping to "approximately within tolerance." Depends on the
  realtime-vision research question (§10) and the operator's automated
  data-gathering plan.
- **Ships without SC-assist / Advanced Docking Computer (later).** v1 assumes
  both are fitted; graceful handling of their absence is deferred.
```
