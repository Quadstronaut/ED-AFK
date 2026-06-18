# COUNCIL C4 — SMACK RECOVERY SCENE (ED-AFK flow redesign, 2026-06-17)

> ✅ **SMACK ROUTINE IS LAW — LOCKED/RESOLVED (operator 2026-06-18).** The earlier
> "DEVIATION-BLOCKED / DO-NOT-RE-FIRE / target-nothing / verbal-vs-written contradiction" banner was a
> **CLAUDE ERROR** — there was never a real conflict, and it is struck. The authored 8-step routine
> below is the law, in order: (1) `set_throttle 100` → (2) `nav_target_star` → (3) `pitch_compass`
> [keep the smack-glare guards] → (4) `target_ahead` → (5) `wait_cooldown_clear` →
> (6) `engage_supercruise` (Key_J — re-enter SC; spawns the escape vector; ALIGN-AND-HOLD to
> `SupercruiseEntry`; **NOT** `engage_jump_clearance`/Key_K) → (7) `nav_supercruise_star` →
> (8) → **Traversal**. **Step 6 is settled — do not re-open or hedge.** The escape-vector ALIGN-AND-HOLD
> mechanic and the compass-glare guards are retained as ship-safety (not deleted by this resolution).
> Ref [[resume-state-2026-06-18-flow-redesign]] / the CONSOLIDATED-BLOCKERS doc.

You are a council-v2 instance. **This brief is SELF-CONTAINED and AUTHORITATIVE.** Tier: feature.

## Binding standing rules
- **DESIGN-ONLY.** Ratified design + Operator-blocker list. *(STATUS 2026-06-18: the no-build clause is
  LIFTED per the MASTER-SPEC standing rules — building is authorized for ratified scenes. This bullet is
  the historical record of the design-only round the council ran; NO-GUESSING + fail-closed below still
  bind.)*
- **NO GUESSING.** Unknown not settled in the cited sources → `BLOCKED-ON-KYLE: <question>`. Read docs.

## Shared context — read FIRST
- `docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md` (operator intent + settled truths).

## YOUR SCOPE — design the SMACK RECOVERY scene exactly as authored (LOCKED 2026-06-18)
1. `set_throttle 100`
2. `nav_target_star`
3. `pitch_compass`
4. `target_ahead`
5. `wait_cooldown_clear`
6. `engage_supercruise` (Key_J) — **SETTLED/LOCKED (operator 2026-06-18).** Re-enter SC from NORMAL space
   after the smack; this spawns the BLUE/CYAN escape vector the ship must ALIGN-AND-HOLD to
   `SupercruiseEntry`. This is the SC-entry mechanic, **NOT** `engage_jump_clearance` (Key_K, the
   hyperspace jump-clearance loop). The earlier "(enter supercruise)" note vs the
   `engage_jump_clearance` token was resolved in the operator's favour of `engage_supercruise` — it is
   no longer a `BLOCKED-ON-KYLE`. (`engage_jump_clearance` named here only to mark what was NOT chosen.)
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
