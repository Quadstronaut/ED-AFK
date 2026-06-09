# Gate/Path Walk — live checklist

Drives the session in `docs/superpowers/plans/2026-06-08-future-test-gate-path-walk.md`.
Harness: `scripts/gatewalk.py` (real code, keys OFF). Mark each row ✅ correct ·
⚠️ needs a stability hook · ❌ bad gate / missing hook. On ❌ draft the issue
(template at the bottom).

## How to run

```powershell
# from projects/ed-autojump
.venv\Scripts\python scripts\gatewalk.py --mode routing            # dispatch trace (default)
.venv\Scripts\python scripts\gatewalk.py --mode step --duration 3600   # full procedures vs live frames
```

- **`DISPATCH>`** lines = the REAL procedure `_maybe_startup`/`dispatch` chose for
  the state Operator just drove into. **`[DECISION]`** = every `ctx.log`/recorder
  outcome (Step, HoldAlignmentDone, ProcedureAborted, …). **`[JOURNAL]/[STATUS]/
  [ROUTE]`** = game ground-truth.
- Operator drives one transition, calls the mark; we read the trace between marks.
- Session jsonl → `~/ed-afk-sessions/gatewalk_<mode>_<stamp>.jsonl` (the audit).

## What each tool covers (be honest about scope)

| Question | Tool |
|---|---|
| Which procedure routes for state X? | `gatewalk --mode routing` |
| Does a per-step gate read state right (vs live frames)? | `gatewalk --mode step` |
| Exact keys-ON event-consumption TIMING (a proc blocking ~70s mid-jump) | `ed-autojump run --record --engage-keys` (real flight) |

`gatewalk` keys are OFF — it audits ROUTING and GATE branches, not the keys-ON
timing path. Don't over-trust a green routing walk for a timing bug.

---

## 1 · Startup routing — `_maybe_startup` (plan §5.2: "confusion at different start locations")

Drive the ship into each state, fresh-launch the walk, read the `DISPATCH>` line.

| # | State to drive into | Expected dispatch | Got | ✅/⚠️/❌ |
|---|---|---|---|---|
| 1 | Normal space, at a star, route plotted | `startup` | | |
| 2 | Normal space, **no** route | (no fly — `NoRouteOnStartup`, idle) | | |
| 3 | In SC, parked at completed route terminus (route empty, dest=local star) | idle (`RouteCompleteIdleOnRestart`) | | |
| 4 | In SC, fresh arrival ≤30s, dest=local star | `arrival` (orbit get-around) | `arrival` via **P2 `local_star`** — dest `…b48-0 A`→`_destination_is_local_star`=True. jump_age=83s **not** the cause (P2 short-circuits before the ≤30s check) | ✅ |
| 5 | In SC, fresh arrival ≤30s (smack guard window) | `arrival` | | |
| 6 | In SC, stale loiter >30s, confident non-local-star dest | `sc_resume` | | |
| 7 | Smacked (normal space, last SC transition = star drop, FsdCooldown burning) | `smack_recovery` | | |
| 8 | Docked on load | (no escape — return) | | |

> **§1 walk note (2026-06-09, live + adversarially confirmed).** Observed real state
> *Tyroopps OT-X b48-0*, SC, dest `…b48-0 A`, route=108, **jump_age=83s** → `DISPATCH> ARRIVAL`,
> `reason=local_star`. This proves the **gate priority precedence**: `_maybe_startup` P2
> (dest IS local star) outranks P4 (stale-loiter `sc_resume`) **even when jump_age>30s** — the
> "≤30s" qualifier on rows 4–6 is incidental, the binding discriminator is
> `_destination_is_local_star`. Harness faithfulness CONFIRMED: every routing branch dispatches
> via `self._run(name)`, which the routing tracer intercepts — no silent dispatch path.
> **Caveat for boundary rows (4 vs 6):** the `[STATE]`/snapshot `jump_age_s` is read at snapshot
> time, not gate time (seen as 82.5 vs 83 drift) — near the 30s threshold trust the `[DECISION]`
> `ArrivalOnRestart`/`ScResumeOnRestart` payload's `jump_age`, not the `[STATE]` column.

## 2 · Full jump — live `dispatch` + arrival.toml (THE row today's bug lives on)

Walk a complete hop and confirm a procedure runs at EACH arrival.

| # | Transition | Expected | Got | ✅/⚠️/❌ |
|---|---|---|---|---|
| 1 | Normal+route → `_maybe_startup` | `startup` dispatched | | |
| 2 | startup completes hyperspace → **FSDJump arrival** | **`dispatch(FSDJump)` → `arrival`** ⬅ REGRESSION 2026-06-09 | | |
| 3 | arrival: scoop gate (fuel<0.70 AND KGBFOAM star) | scoop or skip, correctly | | |
| 4 | arrival: bounded star lock (max_rows=3) close vs far | orbit vs skip_to direct | | |
| 5 | arrival: target_next_route locks next system | required ok | | |
| 6 | arrival: orient → engage → hold → StartJump | jump commits | | |
| 7 | witchspace StartJump→FSDJump (interpreter pause) | pause then **resume** (not wedge) | | |
| 8 | next FSDJump arrival → arrival again | dispatch repeats every hop | | |

> **Regression 2026-06-09 — ROOT-CAUSED + FIXED (commit 9d2a99b).** Fresh launch
> in *Dryio Eaec XQ-U b36-0* (normal+route) ran `startup`, jumped cleanly to
> *Dryio Eaec NE-Y b34-0* (FSDJump 18:05:15Z) — then **no procedure ran**, overlay
> froze on `STARTUP > HOLD_ALIGNMENT`, ship sat dead ~56 min. Two councils (4+5
> reviewers) + `scripts/replay_driver.py` proved the **trigger is sound**:
> `dispatch(FSDJump)→arrival` fired for all 10 arrivals in that journal. The real
> failure was the **process crashing** on an unhandled
> `pydirectinput.FailSafeException` (cursor in a screen corner); the overlay just
> froze on the last live line. Key up/down duration was never relevant. Fix:
> `FAILSAFE=False` + interpreter step-crash→abort (`StepCrashed`) + run_live
> `[CRASH-PARKED]` instead of dying. **This row is now a fix-holds check:** force a
> step error and confirm StepCrashed + abort/park with the process ALIVE.

## 3 · Refuel — `scoop_refuel`

| # | State | Expected | Got | ✅/⚠️/❌ |
|---|---|---|---|---|
| 1 | fuel ≥ 0.70 on arrival | skip | | |
| 2 | fuel < 0.70, KGBFOAM star | approach→scoop→standoff→hold→full | | |
| 3 | fuel < 0.70, non-scoopable star | skip | | |
| 4 | stale-arrival (no fresh StartJump star class) | skip | | |

## 4 · Dock / undock (plan §5.1: "it never targets the station")

| # | State | Expected | Got | ✅/⚠️/❌ |
|---|---|---|---|---|
| 1 | route terminus is a station | target station, approach, no-fire-zone, request, dock | | |
| 2 | capture-at-plot: station locked at plot time | `_dock_target` set | | |
| 3 | new route plotted while docked | `dock_resume` | | |
| 4 | `Undocked` | resume | | |

## 5 · Smack recovery

| # | State | Expected | Got | ✅/⚠️/❌ |
|---|---|---|---|---|
| 1 | drop in exclusion zone (SupercruiseExit Body=Star, cooldown) | `smack_recovery` | | |
| 2 | escape-vector dance to SupercruiseEntry | hold to SC entry | | |
| 3 | re-smack mid-recovery | recovery re-runs (not preempted) | | |
| 4 | glare false-pass guard (D2) | no false pitch-flip | | |

## 6 · Route complete

| # | State | Expected | Got | ✅/⚠️/❌ |
|---|---|---|---|---|
| 1 | final hop, system destination | `route_complete_park` (orbit + hold) | | |
| 2 | final hop, station destination | full dock, stay docked | | |
| 3 | restart while parked at terminus | idle, no re-arrival | | |

## 7 · Operator-observed efficiency targets (2026-06-09 live AFK watch — "the jumps are too long")

Added by Operator watching live hops at ~90–130s each. These are efficiency/correctness
defects, not just timing. Validate against his pending **play-by-play reference logic**
(the desired gates/ifs) to pin gaps vs erroneous code.

| # | Observed behaviour | What to test | Got | ✅/⚠️/❌ |
|---|---|---|---|---|
| 1 | Nav-panel **Target** fires too many times per hop, with very long pauses | count `target_next_route`/`nav_panel_target` invocations per hop; locate the long-pause source (watchdog wait / retries / event-gate stall) | | |
| 2 | Post-SC-assist **wait sometimes unnecessary** — next target already in front, no get-around needed | is there an "already in front / aligned" short-circuit, or does the orbit+wait always run? | | |

---

## ❌ → issue template (plan §6)

```markdown
**Title:** [KNOWN ISSUE] <short behaviour>
**State to reproduce:** <system, distance, SC/normal, route, GuiFocus>
**Expected:** <what the gate/path should do>
**Actual:** <branch taken, with journal/Status evidence>
**Root cause (if known):** <file:line, gate name>
**Workaround (HOWTO):** <how the user operates around it>
**Fix difficulty:** easy-code | hard-code | needs-game-mechanic-confirmation
**Roadmap tier:** blocker | next | someday
```
