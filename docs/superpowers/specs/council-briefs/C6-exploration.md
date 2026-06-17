# COUNCIL C6 — EXPLORATION SCENE (ED-AFK flow redesign, 2026-06-17)

You are a council-v2 instance. **This brief is SELF-CONTAINED and AUTHORITATIVE.** Tier: feature.

## Binding standing rules
- **DESIGN-ONLY.** Ratified design + Operator-blocker list. Do NOT build, edit flight code, or commit.
- **NO GUESSING.** Unknown not settled in the cited sources → `BLOCKED-ON-KYLE: <question>`. Read docs.

## Shared context — read FIRST
- `docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md` (operator intent + settled truths).

## YOUR SCOPE — design the EXPLORATION scene exactly as authored
1. `nav_supercruise_unexplored` — LOOP: SC-assist the next `UNEXPLORED` row down the list; the loop knows
   it has reached the list bottom when the **star/system glyph (`✦`) appears in column 0 at the row
   selector** (start of the nearby-systems section). Terminates when the row selector lands on a
   system-icon row. Operator: "track by row selector — long sessions, but covers all unexplored."
2. → **Traversal**

Settled truths to honor: unexplored bodies render as the literal text `UNEXPLORED` (no name until
scanned) — see pinned frame `projects/ed-autojump/tests/fixtures/navpanel/lhs2509_unexplored_1080.png`.
The READ layer (`ocr_winrt.py`) already reads these rows; the per-row icon (box-in-hollow-box for
unexplored vs `✦` for system) is the loop's termination signal — design the column-0 icon discrimination
(box-in-box vs `✦`) from the pinned frames; flag if more frames are needed.

`nav_supercruise_unexplored` is primarily defined in council **C1** (the action); THIS council designs the
SCENE that drives the loop + its entry (from Arrival's branch, council C2) + exit (→ Traversal). Also
reconcile with the EXISTING `step_explore` / `body_tour` (`projects/ed-explore/src/ed_explore/`) — the
operator's loop SUPERSEDES the old body_tour; state what is replaced.

## Ground in
- `projects/ed-explore/src/ed_explore/steps_explore.py`, `steps_body_tour.py` (existing tour).
- `projects/ed-vision/src/ed_vision/navpanel_reader.py` (NavBody, row_index, next_unexplored).
- Pinned unexplored frame (above) + populated frame `shinrarta_populated_1080.png` (has a `✦` for LTT 4550).

## Deliverable
The EXPLORATION scene DESIGN (loop termination logic + the column-0 icon discrimination + scene entry/exit)
as a proposed `.toml` sketch + the `nav_supercruise_unexplored` loop contract inside the doc (no real
file) + a `BLOCKED-ON-KYLE:` list. Do NOT modify flight code.
