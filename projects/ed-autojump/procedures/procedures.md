# Procedure DSL reference

Each `*.toml` in this directory is one **procedure**. A procedure is a list of
**steps**. A step is an inline table with one required key — `action` — plus
whatever params that action takes, plus the optional `required` flag.

```toml
steps = [
  { action = "wait", s = 1.5 },
  { action = "orient_compass", required = true },
]
```

## What `required = true` means

`required` controls **what happens when the step fails** (returns False — bad
bind, vision unavailable, status flag blocked, journal event never arrived,
etc.).

| `required` | step succeeds | step fails |
|---|---|---|
| `false` (default) | move on | log it, move on anyway |
| `true` | move on | trigger `[on_required_fail]` policy → retry-from OR abort |

A `required` failure is the **fail-closed gate**. When a required step fails
and there's no successful retry left, the procedure aborts immediately and
**no later steps run** — that's the whole point. Putting `required = true` on
`orient_compass` is what guarantees the bot will never `engage_jump` without
a confirmed alignment. Putting it on `target_next_route` is what guarantees
the bot won't try to jump with no target locked. Use it on any step whose
later steps would be unsafe or pointless without it.

`[on_required_fail]` (optional, per procedure):

```toml
[on_required_fail]
retry_from   = "sc_assist_orbit"   # action name to jump back to (must exist in steps)
max_retries  = 3                   # how many times to retry before aborting
backoff_s    = 2.0                 # sleep this long before each retry
```

With no `[on_required_fail]` block, a required failure aborts on the first try.

## Procedure-level keys (top of file, before `steps`)

| Key | Type | Default | What it does |
|---|---|---|---|
| `parallel` | bool | `false` | This procedure is a background track. Launched concurrently via `parallel_tracks`. |
| `parallel_tracks` | list[str] | `[]` | Names of procedures to launch concurrently at start of this one. |
| `stop_on_event` | str | unset | **Reserved (v1: parsed, not enforced).** Journal event meant to end a parallel track early. |
| `timeout_s` | float | `0.0` | **Reserved (v1: parsed, not enforced).** Hard cap for a parallel track. |

## Actions

All step params are keyword-only. Anything not listed for an action is
ignored (loader passes the inline table straight through as `**params`).

### Input primitives

| action | params | what it does |
|---|---|---|
| `press` | `bind: str`, `hold_s: float = 0.05` | Tap a bound action for `hold_s` seconds. Fails if `bind` is unbound. |
| `wait` | `s: float` | Sleep `s` seconds. Always succeeds. |
| `set_throttle` | `pct: int` (one of `0`, `25`, `50`, `75`, `100`) | Press the matching `SetSpeedN` bind. Fails on any other `pct` or unbound action. |
| `pitch` | `dir: "up"\|"down"`, `hold_s: float` | Hold `PitchUpButton` or `PitchDownButton` for `hold_s`. |

### Targeting

| action | params | what it does |
|---|---|---|
| `target_ahead` | none | Press `SelectTarget`. With nothing ahead this CLEARS the target. |
| `target_next_route` | none | Press `TargetNextRouteSystem`. Also cancels Supercruise Assist. |
| `nav_panel_target` | `settle_s: float = 0.4` | Blind nav-panel macro: open panel → row 0 (closest body) → UI_Select (opens detail; cursor on "Lock Destination") → UI_Select (activate) → close. Targets the arrival star regardless of reticle aim. |

### Jump / supercruise

| action | params | what it does |
|---|---|---|
| `engage_jump` | none | Check status flags (docked, FSD charging/cooldown/mass-locked, overheating) → throttle 100 → `HyperSuperCombination`. Fails closed on any blocking flag. |
| `engage_supercruise` | `timeout_s: float = 30.0` | If already in SC, no-op. Else press `Supercruise` and wait for `SupercruiseEntry` event (up to `timeout_s`). |

### Journal / timing gates

| action | params | what it does |
|---|---|---|
| `wait_for_event` | `event: str`, `timeout_s: float` | Block until journal logs `event`, or `timeout_s` elapses. Returns True only if event fires. |
| `wait_cooldown` | `since: str`, `s: float` | Sleep for the **remainder** of `s` seconds since the `since` event fired. No anchor → sleep full `s`. |
| `hold_until_event` | `bind: str`, `event: str`, `max_hold_s: float = 30.0` | Key DOWN, wait for `event`, key UP. Release is log-gated; `max_hold_s` is a safety backstop. Key is ALWAYS released (try/finally). |

### Vision-gated steering (compass CV)

These fail closed if vision (compass reader + frame grabber) is unwired.

| action | params | what it does |
|---|---|---|
| `orient_compass` | any `align_kwargs` overrides | Yaw/pitch until the targeted star's compass dot is centered ahead. Returns False if vision missing or alignment never converges — pair with `required = true` to gate the jump. |
| `pitch_compass` | `until: "edge"\|"behind" = "edge"`, `edge_frac: float = 0.6`, `center_frac: float = 0.25`, `pitch_hold: float = 1.0`, `settle_s: float = 1.0`, `max_iters: int = 20`, `timeout_s: float = 30.0` | Pitch-up only (no throttle) until the targeted star's dot crosses the gate (`edge` = near rim; `behind` = behind ship & centered). |
| `sc_assist_orbit` | `settle_s: float = 0.4` | Walk the nav panel: open → row → LOCK & SC → activate → close. |

## Conventions

- **Bind names** match Elite's binds file action names (`ExplorationFSSDiscoveryScan`, `HyperSuperCombination`, `PitchUpButton`, etc.). The sender resolves the name to a scancode via the active preset; unbound → step fails.
- **Event names** match Elite journal event names verbatim (`FSSDiscoveryScan`, `SupercruiseEntry`, `StartJump`, `FSDJump`, `Docked`, etc.).
- **A False from a step never throttles or jumps.** Failure either aborts (if `required`) or is logged and skipped (if not). The `engage_*` steps additionally check status flags before sending input.
- **Times are seconds, always floats.** `pct` is an integer (`0`/`25`/`50`/`75`/`100`).
