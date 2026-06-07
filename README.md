# ED-AFK 🛸

> **Robots that fly spaceships so you don't have to stay awake.**

A monorepo of AFK automation tools for *Elite Dangerous: Odyssey*. The first
tool, **`ed-autojump`**, drives the keyboard to take a ship from system A to
system B, over and over, while you sleep — arrive, honk, get clear of the star,
orient at the next route star, jump, repeat.

<p>
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white">
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0-A42E2B">
  <a href="https://github.com/Quadstronaut/ED-AFK/actions/workflows/test.yml"><img alt="CI" src="https://github.com/Quadstronaut/ED-AFK/actions/workflows/test.yml/badge.svg"></a>
  <img alt="status" src="https://img.shields.io/badge/status-v1%20in%20development-orange">
</p>

---

## 🤔 What is this?

`ed-autojump` is an autoexploration bot. **v1** is one thing done well —
**Component 1: automated A→B navigation.** No FSS, no DSS, no docking, no
fancy route planning. Just: don't ram the star, and jump to the next system.

It works by tailing the game's own public data files (the Player Journal and
`Status.json`), reading the in-cockpit nav compass with computer vision, and
synthesizing keystrokes via DirectInput. The architecture is the interesting
part: a **step library + an interpreter + human-editable per-procedure TOML
files**. You tune the bot's behaviour by editing a list, not by editing code.

> ⚠️ **Honest disclaimer.** Automating gameplay is against the *Elite Dangerous*
> Terms of Service. This repo is a personal, educational architecture
> exercise — a study in event-driven control, fail-closed safety, and
> data-as-config. Run it at your own risk.

---

## ⚙️ How it works

Three readers feed a dispatcher; the dispatcher picks a procedure; the
interpreter runs that procedure's steps in order and **fails closed**; each step
calls one tested function that presses keys into the game.

```mermaid
flowchart LR
    J[📜 journal tail] --> D
    S[📊 status reader] --> D
    C[🧭 compass vision] --> D
    D{{Dispatcher\nevent → procedure}} --> I
    I[Interpreter\nruns ordered steps\nfails closed] --> L[Step library\n1 fn per primitive]
    L --> K[⌨️ DirectInput keys] --> G([🎮 Elite Dangerous])
    P[(procedures/*.toml\neditable step lists)] -.-> I
```

A LIVE `FSDJump` event runs the `arrival` procedure; a fresh load sitting at a
star runs `startup`; an emergency drop inside a star's exclusion zone
(`SupercruiseExit` with `BodyType: Star`) runs `smack_recovery`. Replayed
backlog events only update state — they never press a key. The interpreter walks
each procedure top-to-bottom, tracking per-step success, and any failed
`required` step aborts the run **without ever throttling forward or jumping**.

---

## 📝 The editable procedures

This is the whole idea. **Every named procedure is an ordered, reorderable list
of steps living in its own TOML file** under `projects/ed-autojump/procedures/`.

- Reorder behaviour by **moving lines**. Duplicate a step? Totally fine.
- **Every number is a live-tune knob** — orbit settle times, throttle percents,
  retry counts — all data, all in execution order.
- The loader validates every procedure at startup: unknown action, unbound key,
  or bad `retry_from` and the bot **refuses to run** rather than improvising.

The four v1 procedures (all editable in `projects/ed-autojump/procedures/`):

- **`arrival`** — runs on every live `FSDJump` event. Throttles to zero, scoops
  fuel if needed (star class and fuel level gated), locks the arrival star via
  the nav-panel macro (identity-verified, fails closed on a non-star row), orbits
  with SC-assist to clear the target geometry, locks the next route hop (with
  danger-class verification), burns clear, then coarse-orients via nav compass
  (`orient_compass`, `required`), fine-orients via the HUD widget ring
  (`orient_widget_ring`, `required`), engages the jump (`engage_jump`,
  `required`), and holds alignment through the FSD spool (`hold_alignment`,
  event-gated on `StartJump`). Honk runs in parallel throughout.
- **`startup`** — fresh load in normal space at a star. Direct jump attempt
  first; if a required step fails, the recovery lane locks the star, pitches it
  astern, engages supercruise, orbits with SC-assist, then locks the hop, burns
  clear, orients, and jumps. Honk runs in parallel.
- **`honk`** — the parallel track. Switches to analysis mode (status-flag
  gated), holds the fire-group trigger (`PrimaryFire`) until `FSSDiscoveryScan`
  lands in the journal (~5 s), then releases.
- **`smack_recovery`** — reflex for an emergency drop inside the exclusion zone
  (`SupercruiseExit`, `BodyType:Star`). Kills thrust, locks the arrival star,
  pitches it 180° astern (compass-gated), waits for the FSD cooldown flag to
  clear (flag-gated, no clock), presses supercruise until a charge is live
  (which spawns the escape-vector on the compass), centres the escape-vector
  dot, and holds it to `SupercruiseEntry`. Then locks the hop, burns clear,
  orients, and jumps.

---

## 🧰 The v1 step library

Every step is `{ action = "<name>", <params> }` and returns `ok: bool`. Mark a
step `required = true` and a failure triggers the procedure's retry/abort policy.
The canonical reference is `projects/ed-autojump/procedures/procedures.md`.

**Input primitives**

| action | does | fails when |
|---|---|---|
| `press` | press a bound ED action for `hold_s` | bind unbound |
| `wait` | sleep `s` seconds | never |
| `set_throttle` | press `SetSpeedN` for `pct ∈ {0,25,50,75,100}` | bind unbound or invalid pct |
| `pitch` | dead-reckoned pitch up/down for `hold_s` (no vision) | bind unbound |
| `pips_engines` | reset power distribution, then max engine pips | any bind unbound |

**Targeting**

| action | does | fails when |
|---|---|---|
| `target_ahead` | `SelectTarget` — locks the body ahead, or clears the target if nothing's there | bind unbound |
| `target_next_route` | `TargetNextRouteSystem` (also cancels SC-assist), then verifies the resulting `FSDTarget` StarClass against the danger list — fails closed on D\*/N/H/W | bind unbound, no new FSDTarget, or danger-class star |
| `nav_panel_target` | nav-panel macro: open → row 0 (closest body) → activate — targets the arrival star regardless of reticle aim | any bind unbound |
| `ensure_analysis_mode` | gate on the AnalysisMode status flag; toggles the HUD if needed (bounded press count) | no status reader, toggle limit exceeded |

**Jump / supercruise**

| action | does | fails when |
|---|---|---|
| `engage_jump` | checks blocking flags, `SetSpeed100`, `Hyperspace` (granular jump) | any blocking flag, or bind unbound |
| `engage_supercruise` | presses `Supercruise`, gates on `SupercruiseEntry` journal event or Supercruise status flag | FsdCharging clears without entry, or stuck-state watchdog fires |
| `hold_alignment` | micro-corrects compass alignment during the FSD spool; exits on `StartJump` event or its state flag | FsdCharging clears without `StartJump`, FsdCooldown before any charge, or stuck-state watchdog |

**Timing / wait gates**

| action | does | fails when |
|---|---|---|
| `wait_cooldown_clear` | blocks until the `FsdCooldown` status flag clears (flag-gated, no clock) | no status reader |
| `hold_until_event` | key down, wait for a named journal event, key up — key always released in finally | max_hold_s backstop exceeded |

**Vision-gated steering**

| action | does | fails when |
|---|---|---|
| `orient_compass` | yaw/pitch until the nav-compass target dot is centred and in front | vision uncalibrated, or alignment never converges inside `timeout_s` |
| `orient_widget_ring` | fine-alignment pass using the HUD widget ring after coarse orient (no-op if disabled) | widget not detected and `widget_ring_on_miss = fail_closed` |
| `pitch_compass` | compass-gated pitch until the target dot reaches `edge` (near rim) or `behind` (centred + hollow) | timeout or iteration limit |
| `sc_assist_orbit` | nav-panel macro: open panel → lock star → activate SC-assist → close | any bind unbound |
| `scoop_refuel` | approach scoopable star at `approach_pct` throttle, hold standoff by observed scoop rate fraction, drink until full (log-gated) — skipped if fuel ≥ `refuel_below` or arrival star is not scoopable | best-effort: skip/fail never blocks the jump |

`orient_compass`, `orient_widget_ring`, `engage_jump`, and `hold_alignment` are the
steps marked `required` in the jump lane — together they guarantee the bot never
engages the FSD without a confirmed, maintained alignment.

---

## ⭐ Why it stopped ramming the star

The old orchestrator oriented the ship at the **next jump target** the instant
it arrived. The orient logic was correct — it hit the coordinate. The problem:
that coordinate is frequently **hidden directly behind the arrival star**, so
"pointing at the target" meant pointing *through the star*, and the bot
throttled straight into it.

The fix is the maneuver a human would do: on arrival, **engage Supercruise
Assist to orbit the star.** That moves the ship's angular position so the next
hop is unobstructed — *then* orient, *then* jump. And the jump now **fails
closed**: it fires only after orientation is positively confirmed. If the
compass is degraded or the orient fails, the procedure aborts — no throttle, no
jump. The old code failed *open* (a missing compass unlocked the jump anyway).
That single inversion — fail open → fail closed — is the safety contract this
whole redesign exists to guarantee.

---

## 🗺️ Scope

**v1 is deliberately narrow: functional A→B route jumping that doesn't ram the
star.** The architecture is built to scale into the rest without rework — new
behaviour is a new file in `procedures/`, not surgery on the existing ones.

| | v1 (now) | v2+ (earmarked) |
|---|---|---|
| **In** | A→B navigation: arrive → honk → scoop → orient → jump | Hi-res, near-realtime compass vision (smooth turns, not jank-stepping) |
| | Fail-closed jump gate (danger-class filter, status flags) | Brightness directional grid (know *which way* the star is) |
| | Editable TOML procedures + step library | Docking procedures |
| | Fuel-scoop approach (standoff, log-gated, rate-controlled) | Ships *without* SC-assist / Advanced Docking Computer |
| | Spansh route auto-plotting (`--route-plot`) | |
| | Game launch via MinEdLauncher, menu navigation | |

**Out of v1 scope (deferred, framework stubs only):** FSS keyboard sweep, FSS
CV-assisted, DSS. v1 **assumes** Supercruise Assist (blue-zone throttle mode)
and an Advanced Docking Computer are fitted.

---

## 📂 Repo layout

```
ED-AFK/
├── README.md                  <- you are here
├── LICENSE                    <- MIT (repo root)
├── THIRD_PARTY_NOTICES.md     <- attribution + the AGPL model note
├── docs/
│   ├── shared/                <- cross-tool reference (journal events, FSD, star classes)
│   └── superpowers/specs/     <- design specs (incl. the v1 procedure-interpreter design)
└── projects/
    └── ed-autojump/           <- first tool: the autoexploration bot
        ├── LICENSE            <- AGPL-3.0 (the shippable distribution)
        ├── config.toml        <- runtime config (vision region, nav knobs, ...)
        ├── procedures/        <- the editable step-list TOML files live here
        ├── src/ed_autojump/   <- journal/ status/ keys/ vision/ executor/ ...
        └── tests/             <- offline unit + interpreter + procedure-validation tests
```

---

## 🧭 Pointers

- **Config** lives in `projects/ed-autojump/config.toml` (vision region, nav
  knobs, and the rest of the runtime settings).
- **Procedures** live in `projects/ed-autojump/procedures/` — the editable
  surface described above.
- **The full v1 design** (the source of truth this README condenses) is
  [`docs/superpowers/specs/2026-05-25-procedure-interpreter-design.md`](./docs/superpowers/specs/2026-05-25-procedure-interpreter-design.md).
- The per-tool README with setup details is
  [`projects/ed-autojump/README.md`](./projects/ed-autojump/README.md).

---

## 📜 License & attribution

The repository root is **MIT** (see [`LICENSE`](./LICENSE)). However, the
**`ed-autojump` distribution is licensed AGPL-3.0-or-later** (see
[`projects/ed-autojump/LICENSE`](./projects/ed-autojump/LICENSE)): it bundles the
nav-compass detection model, whose Ultralytics weights are AGPL-3.0, and AGPL is
viral over the combined work. If you don't ship the bundled model, the OpenCV
fallback needs no weights at all.

<details>
<summary>Borrowed patterns &amp; constants</summary>

Full chain in [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md):

- **[SumZer0-git/EDAPGui](https://github.com/SumZer0-git/EDAPGui)** (MIT) —
  DirectInput scancode table, `.binds` parser shape, Status/NavRoute poller
  patterns, nav-compass alignment approach. The bundled compass model weights
  (`compass.onnx` / `compass.pt`) are **AGPL-3.0** (Ultralytics).
- **[EDCD/coriolis-data](https://github.com/EDCD/coriolis-data)** (MIT) — FSD
  per-class/rating constants.
- **[EDCD/EDDN](https://github.com/EDCD/EDDN)** (BSD-2-Clause) — schema field
  reference for journal/exploration events.
- **[EDCD/FDevIDs](https://github.com/EDCD/FDevIDs)** (MIT) — module Item IDs.

Frontier-supplied data (`.binds` schema, journal/Status field names) comes from
the public Player Journal Manual; no Frontier files are modified or
redistributed.

</details>

---

<sub>github.com/Quadstronaut/ED-AFK · A study in fail-closed control · Fly safe, CMDR. o7</sub>
