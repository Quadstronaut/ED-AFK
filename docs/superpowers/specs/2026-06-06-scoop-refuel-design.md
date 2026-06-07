# Scoop refuel on arrival — design spec

Date: 2026-06-06
Status: COUNCIL-RATIFIED (haiku REJECT on wiring-not-yet-built grounds;
sonnet + opus APPROVE-WITH-CHANGES — all must-fixes folded in below).
Operator design source: verbatim instruction, 2026-06-06 session ("log-driven,
arrival-only, straight ahead into the star, rate-based standoff, 5-minute
fail budget, orbit out via SC assist, honk in parallel").

## 1. Problem

The bot has A→B travel, smack escape, arrival orbit, and honk — but NO
refueling. Every route ends when the tank does. The previously-sketched
SC-assist refuel flow was wrong (SC assist doesn't scoop, it rams —
standing memory) and was never built.

## 2. Operator's flow (the contract)

On **arrival only** (live FSDJump), in a system whose **arrival star is
scoopable (KGBFOAM)**:

1. Proceed **straight ahead** into the star (the hyperspace exit pose is
   nose-into-star — measured: 2 of 4 arrivals on 2026-06-06 smacked when
   throttling blind, which is exactly the geometry we now exploit).
2. Keep approaching until **refueling shows up in the logs**.
3. Use the **known max scoop rate of the equipped scoop** as the yardstick:
   once the *observed* rate reaches ~**50% of that max**, stop approaching —
   close enough to refuel quickly, far enough to not soak heat.
4. Hold until the **tank is full**, or the **5-minute overall budget**
   expires (operator-mandated fail backstop; tunable during testing).
5. Star is guaranteed the closest stellar object → **nav-panel target the
   star, engage SC assist** → it climbs to a stable higher orbit outside
   scoop range. (Operator's own caveat: "in THEORY" — see §8 live-test
   watch items.)
6. Target next hop, orient, jump — the existing arrival machinery.
7. **Honk runs in parallel** the whole time (already wired as arrival's
   parallel track) — zero time wasted in the pit stop.

## 3. Measured ground truth (2026-06-06)

- **Status.json is the high-resolution fuel source**, not the journal:
  `Fuel.FuelMain` (float tonnes, live) + `ScoopingFuel` flag (bit 11, already
  parsed at `status/status.py:155`). The journal's `FuelScoop` events are
  chunked (≈5t increments or scoop-end) — fine as confirmation, useless for
  rate control. This makes the operator's rate-based standoff *directly
  implementable*: rate = ΔFuelMain/Δt from Status polls.
- **Ship**: Mandalay, FuelCapacity.Main = 32.0 t (live Loadout).
  `config.ship.expected_ship = "cutter"` / `expected_fuel_capacity_t = 64.0`
  are STALE — this change corrects them to mandalay/32.0, and the step reads
  capacity from the live Loadout event, never config.
- **Scoop**: `int_fuelscoop_size6_class5` = 6A → **max rate 878 kg/s
  (0.878 t/s)**, verified against EDCD/coriolis-data
  `modules/internal/fuel_scoop.json` (full table ships as
  `data/fuel_scoops.json`; class1=E … class5=A). Sanity: a 32t tank refills
  from 20% in ~30s at full rate, ~60s at the 50% standoff — the 5-minute
  budget has ~5× margin.
- **Scoopable check exists**: `fsd.danger.is_scoopable()` (KGBFOAM,
  `danger.py:54`).
- **Current-system star class source**: the `StartJump` event of the jump
  that just completed carries `StarClass` (`events.py:73`).
  COUNCIL MUST-FIX: only `JumpType == "Hyperspace"` StartJumps count — a
  supercruise StartJump carries `star_class=null` and must NOT clobber the
  tracked value (pinned by test). `FSDTarget` at arrival time is the NEXT
  hop — wrong star. Backlog replay populates the tracker on restart, same
  mechanism as `_smacked`.
- **Binary-system note** (council): hyperspace exit is always at the
  system's arrival star, which is the star StartJump's StarClass describes —
  g2 checks the right body. A close non-scoopable companion is handled by
  the same fail path as everything else: no `ScoopingFuel` within budget →
  FAIL, throttle 0, climb out.
- **Smack protection already generalizes**: the refuel step runs inside
  `arrival`, which is in `_PREEMPT_ON_SMACK` — an exclusion-zone drop
  mid-scoop preempts the procedure and dispatches `smack_recovery`. No new
  preemption wiring needed. The feared smack→re-scoop livelock cannot loop
  in-system: smack_recovery's end state targets the next hop and JUMPS, so
  every cycle leaves the system.
- **Heat protection already generalizes**: the heat watchdog thread runs
  during long steps; the scoop hold is not input-exclusive, so the watchdog
  stays active and a heatsink fires on OverHeating. (Council: this is
  reactive — Heat≥1.0 — so thermal safety leans on the standoff keeping us
  out of the deep band; watch live.)
- **Throttle granularity**: the bot's throttle taps are the SetSpeed binds —
  0/25/50/75/100. The approach throttle is therefore **25%** (lowest
  non-zero), not a tuned percentage.

## 4. Design

### 4.1 New step: `scoop_refuel` (in `flow/steps.py`)

Inserted into `arrival.toml` immediately after `set_throttle 0` and BEFORE
the existing `nav_panel_target` → `pitch_compass(behind)` →
`sc_assist_orbit` block. Those existing steps ARE the operator's climb-out
(step 5): they lock the star, pitch it astern (pitch-star-first law), and
only then engage SC assist. **Every existing arrival step is retained
verbatim — the change is one inserted line.**

`required = false` (best-effort): a failed/skipped refuel must never block
the orbit-and-jump that follows. Every exit path zeroes throttle and logs a
`ScoopRefuelOutcome` with reason + fuel telemetry.

All tunables are TOML step params (the same pattern as every other step —
`pitch_compass center_frac` etc.), NOT config: `approach_pct=25`,
`standoff_frac=0.50`, `rate_window_s=2.0`, `budget_s=300`,
`refuel_below=0.70`, `full_epsilon=0.2`. The dead `routing.refuel_threshold`
config knob is DELETED (fix-or-delete; the live knob is `refuel_below` in
arrival.toml).

Skip gates (no-op success, each logged with its reason):
- `g1` no Status fuel block, no Loadout capacity, or unknown scoop module →
  skip (fail safe: don't fly at a star blind).
- `g2` current arrival star not scoopable (`is_scoopable` on the tracked
  last Hyperspace StartJump StarClass; missing/None → skip).
- `g3` `FuelMain / capacity >= refuel_below` → skip, tank healthy enough
  (council unanimous: don't pit-stop a near-full tank).

State machine (all gates are Status.json reads; the ONLY wall-clock is the
operator-mandated 5-minute budget, used strictly as a FAIL backstop, never
as a success gate):

```
ENTRY:     ScoopingFuel already set?  -> SCOOP_RATE  (event-gates-need-
           state-check law: never press-then-wait when the state holds)
           FuelMain already >= capacity - full_epsilon -> DONE(already_full)
APPROACH:  set_throttle(approach_pct=25); fly straight (arrival pose is
           nose-into-star; no steering inputs)
           ScoopingFuel flag appears                   -> SCOOP_RATE
           budget exhausted -> FAIL(no_scoop)  # also the non-scoopable-
                                               # companion / wrong-body net
SCOOP_RATE: keep 25% (operator: "keep approaching until ~50% rate")
            rate >= standoff_frac * max_rate           -> HOLD (cut throttle)
            FuelMain >= capacity - full_epsilon        -> DONE(full)
            budget exhausted                           -> FAIL(slow_scoop)
HOLD:      set_throttle(0)  # min SC speed drift: ~30 km/s × ≤60s of hold
           # ≈ 1.8 Mm vs scoop bands of 100s of Mm — negligible for KGBFOAM
           # mains; the pathological small-star case is bounded by smack-
           # preempt + smack_recovery (proven path, costs minutes not ships)
           FuelMain >= capacity - full_epsilon         -> DONE(full)
           ScoopingFuel dropped AND rate == 0          -> log ScoopStall,
                                                          re-APPROACH once
           budget exhausted                            -> FAIL(partial)
DONE/FAIL: set_throttle(0); log ScoopRefuelOutcome
           {reason, fuel_start, fuel_end, scooped_t, duration_s,
            max_rate_observed, standoff_rate, budget_s}
           return True (DONE/skip) / False (FAIL — non-required, arrival
           continues into the climb-out either way)
```

**Rate measurement (council must-fix — stale-poll trap):** the
StatusReader returns the cached snapshot when Status.json's mtime hasn't
changed, so naive per-poll deltas read rate=0 between writes. The step
keeps `(t, fuel)` samples ONLY when FuelMain differs from the last stored
sample; rate = (newest-oldest)/(t_newest-t_oldest) over samples inside
`rate_window_s`. No changed sample in the window while ScoopingFuel is set
→ rate 0 (true stall), distinct from "file not rewritten yet".

Abort polling: every loop iteration consults `ctx.should_abort()` — operator
panic AND star-smack preemption both land within one poll (same contract as
every other in-step loop). Poll cadence 0.5s (Status.json's own write rate).

**Accepted risk (council, documented not fixed):** there is no Status flag
for "inside the exclusion zone", so a scoop that drifts too deep cannot be
detected before the game acts. The bounds are: standoff at 50% rate keeps
us out of the deep band; a drop mid-scoop preempts arrival → smack_recovery;
a refused sc_assist_orbit after a too-deep exit fails closed (no jump) and
ends in the same recovery. Worst case costs minutes, never presses toward a
worse state.

### 4.2 New data: `data/fuel_scoops.json` + lookup (in `fsd/`)

Size(1-8) × rating(E-A) → max rate t/s, transcribed from EDCD/coriolis-data
with source header (same pattern as `fsd_modules.json`). Lookup keyed by the
Loadout module item string (`int_fuelscoop_size{N}_class{M}`, class1=E …
class5=A). Unknown item → g1 skip (fail safe), logged loudly.

### 4.3 FlowRunner wiring (in `flow/dispatcher.py`)

- `_apply_state` additionally tracks (backlog AND live, like `_smacked`):
  - `StartJump` with `jump_type == "Hyperspace"` and non-null star_class →
    `_arrival_star_class`. Supercruise StartJumps ignored (pinned by test).
  - `Loadout` → `_loadout` (capacity from `fuel_capacity.main`, scoop item
    from modules). Loadout is written at every LoadGame, so backlog always
    provides it; a refit mid-session emits a fresh one.
- New `StepContext` suppliers: `arrival_star_class: Callable[[], Optional[str]]`
  and `ship_fuel: Callable[[], Optional[ShipFuel]]` (capacity_t,
  scoop_max_rate_t_s). Defaults mean "not wired" → g1/g2 skip in unit tests
  without fakes.

### 4.4 `arrival.toml` — one inserted line

```toml
steps = [
  { action = "set_throttle", pct = 0 },
  { action = "scoop_refuel", approach_pct = 25, standoff_frac = 0.50,
    rate_window_s = 2.0, budget_s = 300.0, refuel_below = 0.70,
    full_epsilon = 0.2 },                  # NEW — best-effort pit stop
  # ...every existing step below retained VERBATIM (nav_panel_target,
  # pitch_compass behind, sc_assist_orbit, outward burn, target, orient,
  # engage, hold) — they are the operator's climb-out and jump...
]
```

Honk stays in `parallel_tracks` — it overlaps the scoop hold for free.
(Sender concurrency: honk holds PrimaryFire while scoop taps SetSpeed keys
and the watchdog may tap DeployHeatSink — different keys, and the identical
pattern already runs live today during hold_alignment; no new assumption.)

### 4.5 Telemetry

`ScoopRefuelSkipped{reason}`, `ScoopStart{fuel, star_class}`,
`ScoopRate{rate, frac_of_max}` (sampled ~1/5s), `ScoopStandoff{rate}`,
`ScoopStall{}`, `ScoopRefuelOutcome{...}` — enough to tune approach/standoff
/budget from session jsonl without frame captures.

## 5. Explicitly out of scope

- Exclusion-zone skimming / matched-arc flying (the operator's manual
  technique) — v1 is the simpler straight-in + rate standoff.
- Mid-scoop nose-off-star station-keeping (opus wanted it v1, sonnet argued
  v1-simple; drift math in §4.1 sides with simple — revisit with live data
  if HOLD creeps measurably).
- Re-routing or jump-blocking on critically-low fuel
  (`fuel_safety_threshold` 0.20 is a separate unwired knob; tracked, not
  part of this change).
- Non-arrival refueling (mid-route top-ups, startup-scene scooping).

## 6. Council resolutions of the open questions

1. **"50% refueling capacity"** = observed scoop rate ≥ 50% of the equipped
   scoop's max table rate ⇒ stop approaching. Unanimous — the tank-fraction
   reading contradicts the operator's own "close enough to refuel quickly".
2. **Approach throttle**: 25% — forced by SetSpeed bind granularity (lowest
   non-zero). Opus wanted ~10%; doesn't exist as a tap. Tune live if hot.
3. **Refuel gate**: scoop only when `FuelMain/capacity < 0.70`
   (`refuel_below`). Unanimous.
4. **Budget placement**: APPROACH+SCOOP_RATE+HOLD only; climb-out and jump
   keep their existing behavior. Unanimous. The smack→re-arrival loop fear
   is closed by smack_recovery always jumping OUT of the system.

## 7. Test plan

- Unit (TDD, scripted Status/Loadout fakes): gates g1/g2/g3, ENTRY
  already-scooping and already-full shortcuts, APPROACH→SCOOP_RATE→HOLD→DONE
  happy path, changed-sample rate math (incl. the stale-poll rate≠0 trap),
  stall re-approach exactly once, all three budget-FAIL paths, abort-poll
  responsiveness, unknown-scoop-item skip, throttle zeroed on every exit
  path, StartJump tracking (Hyperspace-only; SC StartJump must not clobber;
  backlog replay), Loadout tracking.
- Live (game up): one scoopable arrival watched end-to-end — verify observed
  max rate vs the 0.878 t/s table value, standoff distance behavior, heat
  trace, and the §8 watch items.

## 8. Live-test watch items (operator's "in THEORY" + council doubts)

1. Does `sc_assist_orbit` engaged from inside the scoop band climb OUT to a
   stable orbit, or behave badly (opus's strongest doubt; operator's own
   caveat)? Telemetry + eyes on the first run.
2. HOLD inward creep on the actual star class mix of the route — does rate
   keep rising after throttle cut (creeping in) and by how much?
3. Observed scoop rate ceiling vs the table's 0.878 t/s (validates the
   standoff threshold).
4. Heat trace during HOLD (reactive-only watchdog — confirm we never get
   near OverHeating at the 50% band).
