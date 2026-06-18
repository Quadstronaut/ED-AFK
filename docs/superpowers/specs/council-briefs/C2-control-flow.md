# COUNCIL C2 — CONTROL-FLOW / SECTION TRANSITIONS (ED-AFK flow redesign, 2026-06-17)

You are a council-v2 instance. **This brief is SELF-CONTAINED and AUTHORITATIVE.** Tier: arch.

> 📌 **ROUND-2 PINNED (operator 2026-06-18) — apply, do not re-litigate:**
> - **D10/D4 transition mechanism = RESOLVED: Python orchestrator.** `_SECTION_TO_PROC` map +
>   `transition_to(runner, section)` + `run_arrival_then_branch` in ed_autojump, registered via the
>   classifier/event-route surfaces at activate() (NO core→domain import). TOML `goto` rejected.
> - **MANDATORY abort-recheck:** read `_preempt`/`_smacked`/`should_abort()` at TWO points — (a) in
>   run_arrival_then_branch BETWEEN `_run("arrival")` and the discriminator read; (b) at top of
>   transition_to before `runner._run(section)`. If set → don't branch, yield to run_live → `_route_sc_exit`.
> - **D2 exploration flag = RESOLVED: reuse `body_tour_enabled`.** VERIFIED wired (config.py:78 →
>   dispatcher.py:122/157/462 → context.py:148 → steps_body_tour.py:72 + arrival.toml:83). FIX: the
>   `exploration_active` predicate must read `ctx.body_tour_enabled`, NOT the phantom
>   `runner._exploration_mode` (boot_routes.py:84, never set).
> - **STILL BLOCKED:** D1 (Status.Destination station schema — operator live test: plot-to-station, read
>   `Destination.Body != 0`). D5/D6 minor (Exploration→Traversal via completion; route_complete_park scoop
>   knob stays 0.99). Do NOT re-run this council until D1 lands.

## Binding standing rules
- **DESIGN-ONLY.** Ratified design + Operator-blocker list. *(STATUS 2026-06-18: the no-build clause is LIFTED
  per the MASTER-SPEC standing rules — building is authorized for ratified scenes. This bullet records the
  design-only round this council ran; NO-GUESSING + fail-closed still bind.)*
- **NO GUESSING.** Unknown mechanic/bind/journal field/layout not settled in the cited sources →
  `BLOCKED-ON-KYLE: <question>`. Read repo code/docs AND community ED docs (journal + Status.json schema)
  before asserting.
- Honor `no-arbitrary-timed-waits` except where the operator explicitly wrote `wait Ns`.

## Shared context — read FIRST
- `docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md` (operator intent + settled truths).

## YOUR SCOPE
Design the **section-transition / control-flow machinery** the redesigned flow needs:
- The operator's `goto ## Section` chaining: **Arrival → (Docking | Exploration | Traversal)**;
  **Smack Recovery → Traversal**; **Exploration → Traversal**.
- The **Arrival flag** set from **witchspace journal entries** (what event(s) arm "arrival").
- The branch **discriminators**: `current system == destination`, `exploration == active`,
  `destination == system | station`.
- Determine whether TODAY's dispatcher already supports **procedure → procedure transitions**, or whether
  this is a NEW concept that must be designed (and how it fits the classifier/event-route surfaces).
- **`honk_dscanner`** as an IMMEDIATE, non-blocking arrival action (today honk = `parallel_tracks=['honk']`).
- **`scoop_refuel`** trigger change `refuel_below` 0.70 → **0.50**.

## Ground in (read before designing)
- `projects/ed-core/src/ed_core/flow/dispatcher.py`, `registry.py`, `step_registry.py`.
- `projects/ed-autojump/src/ed_autojump/flow/boot_routes.py` (classifier → procedure routing; arrival/
  witchspace handling; C-series determination).
- `projects/ed-core/src/ed_core/boot/scenes.py`, `primitives.py`.
- Existing procedures `projects/ed-autojump/procedures/*.toml` for how scenes currently start/end.

## Expected Operator-blockers (flag, don't guess)
- Exact `Status.json` `Destination` schema fields that discriminate **system vs station** (do not guess).
- The source of the **`exploration == active`** flag (config `body_tour_enabled`? a new mode flag?).

## Deliverable
A ratified DESIGN DOC for the transition/branch machinery (mechanism, where it lives, how each section
hands off, the flag/discriminator reads) + a `BLOCKED-ON-KYLE:` list. Do NOT modify flight code.
