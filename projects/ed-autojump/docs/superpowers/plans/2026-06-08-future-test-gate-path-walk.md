# Future Test — Interactive 1-by-1 Gate/Path Walk

**Status:** LINED UP (the next major testing session; not yet run). Authored 2026-06-08.
**Driver:** Operator, in-game, one condition at a time. I monitor + instrument.

> Operator's framing (2026-06-08): "iteratively, interactively, 1 by 1 test every
> condition, gate, path of the code … Exact code extractions must be made, nothing new
> generated in the test that doesn't exist in the code … I'll let you monitor through
> script and active log monitors every action, at every distance: a full jump, a refuel,
> a dock, an undock."

---

## 1. The one inviolable principle: exact code extractions only

Every test exercises the **real code path** — the actual `STEP_REGISTRY` function, the
actual `FlowRunner` dispatch branch, the actual gate — fed real game state. **Nothing is
re-implemented or synthesized in the test that does not exist in the code.** If a gate
reads `Status.json GuiFocus`, the test reads the *same* field through the *same* code; we
do not fake a parallel version. This keeps the session a true audit of the shipped
behaviour, not a test of a lookalike.

Concretely: Operator puts the ship in a known state (e.g. *normal space, at a star*), I
**take a reading** (journal + `Status.json` + `NavRoute.json` + a frame), then we step the
ship one transition (e.g. → *supercruise*) and **take another reading**. All data is
available between steps for inspection. We walk the state machine by hand.

## 2. Format

- **I run** a live monitor: the session recorder (`run --record`, NullSender so no keys),
  plus active tails of the journal, `Status.json`, and `NavRoute.json`, printing every
  state transition.
- **Operator drives** the ship into each condition and calls the mark.
- For each gate/path we record: the inputs that reach it, the branch it takes, and whether
  that branch is **correct, a bad gate, or a missing hook**.
- Output per item: ✅ correct · ⚠️ needs a stability hook / more robust handling ·
  ❌ bad gate or condition.

## 3. Coverage matrix (every action × distance × state)

Walk each, at near / mid / far distance where distance matters:

- **Startup routing** (`_maybe_startup`): normal-space + route, normal-space + no route,
  in-supercruise parked-terminal, in-supercruise fresh-arrival (smack guard), in-SC stale
  loiter (sc_resume), smacked + cooldown.
- **Full jump**: arrival → scoop → star lock (early + bounded) → orbit → target-next →
  orient (compass + widget) → engage → hold → witchspace → arrival. At each: every gate.
- **Refuel** (`scoop_refuel`): healthy-skip, scoopable, approach→scoop→standoff→hold→full,
  stall/re-approach, stale-arrival skip, non-scoopable skip.
- **Dock**: target-station, sc-assist, approach, no-fire-zone, request, ADC land, services.
- **Undock / pit-stop resume**: `Undocked`, new route while docked → `dock_resume`.
- **Smack recovery**: drop-in-exclusion-zone, the escape-vector dance, cooldown gate,
  re-smack, the glare false-pass guard (D2).
- **Route complete**: system destination (park) vs station destination (dock), terminal
  idle on restart.

## 4. ReleaseClient1 candidates

Operator's call (2026-06-08): **jumping and refuelling** have not failed in practice — "we
even went around some class T's and other dangerous stars" — and are candidates for a
`ReleaseClient1` stability tier. This session is where we either confirm that (sustained
clean runs across the matrix) or find the edge that disqualifies them. *(Tonight's live
data-gen run is the first systematic evidence toward this.)*

## 5. Known-issue targets to resolve

Two behaviours we specifically want to root-cause:

1. **It never targets the station.** Why does the route-complete → dock path fail to lock
   the station? (Capture-at-plot is live-test-gated and unconfirmed — see dispatcher
   `_dock_target`.) Determine the real mechanic.
2. **Confusion at different starting locations.** Why does the same ship state route
   differently from different start points? Walk every `_maybe_startup` branch with real
   states and find the misclassification.

## 6. Output pipeline: KNOWN ISSUES → HOWTO → GitHub issues → roadmap

For anything we **cannot easily fix in code**, we do NOT leave it silent:

1. Write it up as a **KNOWN ISSUE** with the exact reproducer (the real state + the real
   branch it mis-takes).
2. Fold the workaround into a **user HOWTO** ("operate within these restrictions").
3. When the set is aggregated, **open each as a GitHub issue** so users know we're aware of
   the shortcoming, and the issue set **defines the public roadmap**.
   *(Outward-facing — the actual `gh issue create` runs only on Operator's explicit go.)*

### GitHub issue template (drafted, ready to fill)

```markdown
**Title:** [KNOWN ISSUE] <short behaviour, e.g. "Dock flow does not lock the station">

**State to reproduce:** <exact ship state — system, distance, SC/normal, route, GuiFocus>
**Expected:** <what the gate/path should do>
**Actual:** <the branch it takes, with the journal/Status evidence>
**Root cause (if known):** <code ref: file:line, gate name>
**Workaround (HOWTO):** <how the user avoids/operates around it>
**Fix difficulty:** easy-code | hard-code | needs-game-mechanic-confirmation
**Roadmap tier:** blocker | next | someday
```

## 7. What I bring to the session

- The live monitor harness (recorder + 3 file tails) ready to launch.
- This matrix as the checklist; each row gets a ✅/⚠️/❌ and, on ❌, a drafted issue.
- The `ACTION_MEGASHEET.md` audit as the master list of auditable actions to cross-ref.
