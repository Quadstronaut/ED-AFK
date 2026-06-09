# BC1 Body-Touring — SPEC BLOCKED: open questions for Operator

**Status:** spec gate did NOT pass (3-council, 0/3 approve). The council stopped
BEFORE plan/implement (by design) and surfaced blockers instead of guessing.
Authored 2026-06-08. BC1 resumes once Operator answers the in-game tests below.

The blockers are real and several depend on **unverified ED mechanics** — per the
standing rule ([[ask-kyle-to-test-game-mechanics]], [[no-assumptions-ever]]), these
must be tested in-game, not coded against an assumption.

---

## A. In-game tests I need from Operator (numbered — the gating mechanics)

**TEST 1 — THE CRITICAL ONE. SC-Assist toward a planet: drop or orbit?**
Lock a planet/moon as your target and engage Supercruise Assist toward it. Does the
ship **auto-drop into normal space at the body** (a `SupercruiseExit`), or does it
**stay in supercruise and orbit/hold** near it?
→ This reshapes the entire per-body loop. If it DROPS, each next body needs a fresh
supercruise re-engage (from normal space); if it ORBITS, the loop is just
lock→assist→dwell→next, all in supercruise. The spec cannot be correct without this.

**TEST 2 — Does the SC-Assist control even exist for a non-star body?**
Open the left nav panel, `UI_Select` a planet/moon row. Does its detail pane have the
**"LOCK AND SUPERCRUISE"** (assist) option in the same position a star's does (one
`UI_Right` then `UI_Select`)? Or is the planet pane laid out differently?
→ The existing `engage_supercruise_assist` macro assumes the star layout; if planets
differ, the engage primitive needs a different walk.

**TEST 3 — Nav-panel cursor after close/reopen (confirm the memory).**
After honking, open the nav panel, walk DOWN to a non-top body (say row 3), then CLOSE
and immediately RE-OPEN the panel. Is the cursor still on that body, or reset to the
top? (Existing memory [[ed-navpanel-cursor-mechanics]] says the cursor *persists* — this
test confirms it persists at the **last-walked row** after our `target_via_navpanel`
close sequence specifically, which decides whether lock-then-engage can re-open safely
or needs a single combined open.)

**TEST 4 (journal-checkable — I can do this if you point me at a session).**
When you drop at a planet, does `SupercruiseDestinationDrop` carry the **body name** in
its `Type` field (like it does the station name)? Determines the arrive-at-body gate.

## B. Design decisions for Operator (not mechanics — your call)

- **Far-star arrival skips the tour.** `arrival.toml`'s bounded star-lock vaults to
  `target_next_route` when the primary star is far (the skip_to gate). That means in a
  system where you arrive far from the primary, the body tour would never run. Intended
  (tour only when the star is close), or should the tour run regardless?
- **7-second dwell** — is ~7s confirmed enough at each body to register the proximity
  auto-scan (the explorer data), or does it need tuning per body size/distance?

## C. Implementation-completeness gaps (for the fast re-spec, once A is answered)

The council also flagged spec gaps that are mechanical to close once the mechanics are
known — folding these into the next spec round:

1. **Combined lock+engage primitive.** `engage_supercruise_assist` opens the panel and
   acts on the highlighted row; if the cursor doesn't reliably sit on the just-locked
   body, the lock and the engage must happen in **one** panel open (walk to row k →
   `UI_Select` → `UI_Right` → `UI_Select`) rather than two — a new `navpanel.py` fn.
2. **`FSSDiscoveryScan` gate must read a latched flag, not an event-waiter.** The honk
   event is consumed by the hub fan-out before `body_tour` runs; set
   `self._fss_discovered` in `_apply_state` (like `_arrival_star_class`) and expose it
   via a supplier — an `event_waiter` query would miss the already-consumed event and
   could deadlock.
3. **Enumerate the new wiring explicitly:** `STEP_REGISTRY.update({"body_tour": ...})`,
   `INPUT_EXCLUSIVE_ACTIONS += body_tour`, new `StepContext` fields + `FlowRunner`
   params + `_make_context` lambdas (and the test-call-site surface that touches).
4. **Per-body arrival gate** uses `SupercruiseDestinationDrop.type` matched to the body
   name (pending Test 4).

## D. Why this is the right call

BC1 is the marquee feature and opt-in by design — it deserves verified mechanics, not an
overnight implementation committed against a guess about how SC-Assist treats planets. The
gate worked: it caught a design that would have engaged the assist toward the wrong body
or mis-modeled the drop/orbit behavior. Answer §A and the re-spec → plan → implement runs
clean.
