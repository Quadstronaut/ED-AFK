# COUNCIL C5 — TRAVERSAL SCENE (ED-AFK flow redesign, 2026-06-17)

You are a council-v2 instance. **This brief is SELF-CONTAINED and AUTHORITATIVE.** Tier: feature.

## Binding standing rules
- **DESIGN-ONLY.** Ratified design + Operator-blocker list. Do NOT build, edit flight code, or commit.
- **NO GUESSING.** Unknown not settled in the cited sources → `BLOCKED-ON-KYLE: <question>`. Read docs.
- Honor `no-arbitrary-timed-waits` except where the operator explicitly wrote `wait Ns` (steps 1 & 4).

## Shared context — read FIRST
- `docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md` (operator intent + settled truths).

## YOUR SCOPE — design the TRAVERSAL scene exactly as authored
1. `wait 5s`
2. `target_next_route`
3. `set_throttle 100`
4. `wait 3s`
5. `orient_compass`
6. `orient_widget_ring`
7. `engage_jump_clearance`

This is the steady-state A→B hop. Most actions already exist; the work is the correct sequence + the
preconditions/fail-closed contract for each, and confirming the two operator `wait`s (5s, 3s) are
intended as wall-clock vs event gates (flag if one SHOULD be event-gated, but keep the operator's value).
Compare against the current `arrival.toml` tail (target_next_route → throttle → orient → jump) and
`startup.toml`/`sc_resume.toml`. The section is entered via `goto` from Arrival/Smack/Exploration —
that transition mechanism is council **C2**; design against it and flag assumptions.

## Ground in
- `projects/ed-autojump/procedures/{arrival,startup,sc_resume}.toml` (current tails).
- `projects/ed-core/src/ed_core/flow/steps_shared.py` (orient_compass, orient_widget_ring);
  `projects/ed-autojump/src/ed_autojump/flow/steps.py` (target_next_route, engage_jump_clearance).

## Deliverable
The TRAVERSAL procedure DESIGN as a proposed `.toml` sketch inside the doc (no real file) +
a `BLOCKED-ON-KYLE:` list. Do NOT modify flight code.
