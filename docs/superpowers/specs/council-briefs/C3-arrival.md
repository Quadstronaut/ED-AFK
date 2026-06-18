# COUNCIL C3 — ARRIVAL SCENE (ED-AFK flow redesign, 2026-06-17)

You are a council-v2 instance. **This brief is SELF-CONTAINED and AUTHORITATIVE.** Tier: feature.

## Binding standing rules
- **DESIGN-ONLY.** Ratified design + Operator-blocker list. *(STATUS 2026-06-18: the no-build clause is LIFTED
  per the MASTER-SPEC standing rules — building is authorized for ratified scenes. This bullet records the
  design-only round this council ran; NO-GUESSING + fail-closed still bind.)*
- **NO GUESSING.** Unknown not settled in the cited sources → `BLOCKED-ON-KYLE: <question>`. Read docs.
- Honor `no-arbitrary-timed-waits` except where the operator explicitly wrote `wait Ns`.

## Shared context — read FIRST
- `docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md` (operator intent + settled truths).

## YOUR SCOPE — design the ARRIVAL scene exactly as the operator authored it
1. `set_throttle 0`
2. `honk_dscanner` — begin IMMEDIATELY on arrival, non-blocking (other actions must NOT wait on it)
3. `scoop_refuel` — **new trigger 50%** (refuel_below 0.70 → 0.50)
4. `nav_supercruise_star`
5. branch: **if** current system == destination → **Docking**; **elif** exploration == active →
   **Exploration**; **else** → **Traversal**

Compare against the CURRENT `projects/ed-autojump/procedures/arrival.toml` and state EXACTLY what is
removed (the early `nav_panel_target` lock at step 1b, the `max_rows=3` distance-proxy, the blind
`sc_assist_orbit` macro, the 13s waits) vs kept. The new actions (`nav_supercruise_star`,
`honk_dscanner`) and the branch mechanism are designed in parallel councils **C1** and **C2** — design
against their stated contracts (in the master spec) and FLAG every cross-council assumption.

## Ground in
- `projects/ed-autojump/procedures/arrival.toml` (current), `startup.toml`, `sc_resume.toml`.
- `projects/ed-autojump/src/ed_autojump/flow/steps.py` (scoop_refuel, step kinds).

## Deliverable
The redesigned ARRIVAL procedure DESIGN — as a proposed `.toml` sketch INSIDE the design doc (do NOT
write a real procedure file) — + a `BLOCKED-ON-KYLE:` list. Do NOT modify flight code.
