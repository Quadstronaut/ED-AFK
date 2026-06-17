# COUNCIL C4 — SMACK RECOVERY SCENE (ED-AFK flow redesign, 2026-06-17)

You are a council-v2 instance. **This brief is SELF-CONTAINED and AUTHORITATIVE.** Tier: feature.

## Binding standing rules
- **DESIGN-ONLY.** Ratified design + Operator-blocker list. Do NOT build, edit flight code, or commit.
- **NO GUESSING.** Unknown not settled in the cited sources → `BLOCKED-ON-KYLE: <question>`. Read docs.

## Shared context — read FIRST
- `docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md` (operator intent + settled truths).

## YOUR SCOPE — design the SMACK RECOVERY scene exactly as authored
1. `set_throttle 100`
2. `nav_target_star`
3. `pitch_compass`
4. `target_ahead`
5. `wait_cooldown_clear`
6. `engage_jump_clearance` — operator note "(enter supercruise)". **BLOCKED-ON-KYLE (do not guess):**
   clarify whether this is `engage_supercruise` (re-enter SC from NORMAL space after a smack) vs a
   hyperspace jump-clearance. The two are different mechanics.
7. `nav_supercruise_star`
8. → **Traversal**

Compare against current `projects/ed-autojump/procedures/smack_recovery.toml`. Cross-check the settled
smack game-truths: a smack is JOURNAL-BLIND and discriminated by the **escape-vector CV** (memories
`smack-journal-blind-vision-discriminator`, `smack-escape-vector-recovery`, `smack-compass-glare`). The
escape-vector detector is a SEPARATE track (G2, `ed_vision/escape_vector.py` stub) — note where this
scene depends on it; do NOT design the detector here. `nav_target_star` / `nav_supercruise_star` come
from council **C1** — design against their contract and flag assumptions.

## Ground in
- `projects/ed-autojump/procedures/smack_recovery.toml` (current).
- `projects/ed-core/src/ed_core/flow/steps_shared.py` (pitch_compass, target_ahead, wait_cooldown_clear,
  engage_supercruise).

## Deliverable
The redesigned SMACK procedure DESIGN as a proposed `.toml` sketch inside the doc (no real file) +
a `BLOCKED-ON-KYLE:` list (the step-6 ambiguity is the headline one). Do NOT modify flight code.
