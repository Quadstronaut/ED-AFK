# COUNCIL C2 — CONTROL-FLOW / SECTION TRANSITIONS (ED-AFK flow redesign, 2026-06-17)

You are a council-v2 instance. **This brief is SELF-CONTAINED and AUTHORITATIVE.** Tier: arch.

## Binding standing rules
- **DESIGN-ONLY.** Ratified design + Operator-blocker list. Do NOT build, edit flight code, or commit.
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
