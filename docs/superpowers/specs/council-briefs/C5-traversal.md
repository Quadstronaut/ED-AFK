# COUNCIL C5 — TRAVERSAL SCENE (ED-AFK flow redesign, 2026-06-17)

You are a council-v2 instance. **This brief is SELF-CONTAINED and AUTHORITATIVE.** Tier: feature.

## Binding standing rules
- **DESIGN-ONLY.** Ratified design + Operator-blocker list. *(STATUS 2026-06-18: the no-build clause is LIFTED
  per the MASTER-SPEC standing rules — building is authorized for ratified scenes; C5 Traversal is RATIFIED
  and buildable now. This bullet records the design-only round this council ran; NO-GUESSING + fail-closed
  still bind.)*
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

## ROUND-2 PINNED FIXES (round 1, task ws7q708sc, route_back to Stage 0 — apply these, do not re-litigate)
- **FACT FIX (T3):** `arrival.toml` `on_required_fail.retry_from` = **`scoop_refuel`** (NOT
  `target_next_route`). ONLY `dock_resume.toml` uses `retry_from = "target_next_route"`. Traversal SHOULD
  use `retry_from = "target_next_route"` (its first action). Cite correctly; verify against live files.
- **NO HONK IN TRAVERSAL:** omit `parallel_tracks = ["honk"]`. Arrival owns honking (operator honks only
  in Arrival). Re-honking in Traversal subscribes AFTER the system's `FSSDiscoveryScan` already fired in
  Arrival → the honk track is event-deaf and holds `PrimaryFire` for `max_hold_s` with no release event.
- **DANGER-GATE CONTRACT (accuracy, NOT a live bug):** `step_target_next_route` returns True at
  `steps.py:94-95` ONLY when `ctx.event_waiter is None` — the UNWIRED / unit-test path (no journal). In
  LIVE operation `event_waiter` IS wired, so the danger-class (D*/N/H/W) refusal DOES run. C5's per-step
  contract must say "danger-refusal fails closed WHEN journal-wired (live)", NOT claim it unconditionally.
  Do NOT flag a live safety bug — there is none.
- **RETRY-LANE FACT:** `retry_from = "target_next_route"` = index 1; sequential retry re-executes
  `wait 3s` (index 3). Only `wait 5s` (index 0, before the anchor) is excluded from retries. State correctly.
