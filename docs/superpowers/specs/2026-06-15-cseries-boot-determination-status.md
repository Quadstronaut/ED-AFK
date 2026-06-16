# C-series boot determination — council status (route_back → Stage 0)

**Date:** 2026-06-15
**Council:** run `wf_28d4c266-0cc` (arch; templates + pure-telemetry primitives, design-only)
**Decision:** **route_back → Stage 0** (no committable result; 0/4 unanimous; 2 blocker fails).
The gate working again — it caught a **non-terminating `bounded_poll` under a frozen clock in
3 of 4 candidates** (a hung boot loop once wired) plus a `SupercruiseExit → True` arrival
inversion. Root cause = a few UNDER-SPECIFIED contracts in the Stage-0 spec, which the arbiter
pinned precisely (below).

**The deliverables are basically right** — the spec doc, the 3 primitives, the 11 inert
templates, and critically the **live-path-byte-untouched check passed (empty diff)**, so nothing
threatens the live bot. The defects are small, precisely located, and the merge target is
known. This is a Stage-0 amend + one regeneration, NOT a redesign.

## Stage-0 contract amendments to PIN before re-running (the arbiter's spec gaps)
1. **`bounded_poll` MUST be read-count-bounded** (a `max_reads` cap) so it terminates under a
   frozen/never-advancing clock. A clock-deadline-only loop hangs forever when the clock doesn't
   move. (gen-opus-2 implements the cap correctly — lines 247-287.)
2. **Arrival event semantics bound explicitly:** `FSDJump → True`;
   `SupercruiseExit / SupercruiseEntry → False`. (One candidate inverted SupercruiseExit→True,
   which fires the orbit get-around on every routine normal-space drop cold-start.)
3. **`reconstruct_arrival_from_journal` input contract:** accept BOTH a typed event model AND a
   raw dict (`{'event': 'FSDJump'}`), with NO `type(ev).__name__` class-name fallback (that
   spoofs a ghost arrival). (gen-opus-1's reconstruct is correct.)
4. **`PollResult` MUST carry an `aborted` field** so abort vs ceiling-timeout are distinguishable
   (the LP3 abort path must be observable). (gen-opus-1 has it.)
5. **`EXPLORATION.determine()` is telemetry-wired** (SC=True + route empty + no latch →
   EXPLORATION), NOT `None` — scene-detection is telemetry-sufficient; the FSS action is the
   Phase-2 part.
6. **`ArrivalLatch.consume()` threading:** document the single-threaded precondition (the engine
   loop is single-threaded) OR add a `threading.Lock`. State it so the concurrency lens scores it
   uniformly.
7. **`fsd_cooldown_blocked(None) → False` direction:** state the intended consumer
   (block-detection: "don't assert a block without evidence"), so the fail-closed direction is
   contract, not judgment.

## Merge target (assemble AFTER the spec amendment, then RE-ENTER Stage 2 — never commit un-reviewed)
- Base: **gen-opus-2's** `bounded_poll` (read-count-capped, abort-before-read-and-sleep, frozen
  `PollResult`, clean on boundary/concurrency/security/AC7).
- Replace its `reconstruct_arrival_from_journal` with **gen-opus-1's** (SupercruiseExit→False,
  dict-aware, no class-name fallback).
- Add `aborted` to `PollResult` (gen-opus-1 has it).
- Wire `EXPLORATION.determine()` to telemetry.

## Lesson for the re-run
The Stage-2 self-pass reported all acceptance tests green but did NOT catch the frozen-clock
non-termination — each candidate's own verifier used an eventually-matching predicate. The AC12
determinism test MUST use a **never-advancing clock + never-matching predicate** and assert
termination via the read-count cap.

## Worktree deliverables (ephemeral)
`.claude/worktrees/wf_28d4c266-0cc-*/` — candidate `primitives.py` / `scenes.py` / the spec doc.
gen-opus-2 = `-3`, gen-opus-1 = `-2`. The intended spec path was
`docs/superpowers/specs/2026-06-15-cseries-boot-determination-spec.md`; primitives at
`projects/ed-core/src/ed_core/boot/{primitives,scenes}.py`.

## The 11-state design (Operator-confirmed, authoritative — survives the route_back)
DOCKED, STARTUP, ARRIVAL, REFUEL, TRAVERSAL, EXPLORATION, STARSMACK, NO-ROUTE, PAUSE, RESUME,
PARKED. The 4 LOCKED PATTERNS (LP1 FSDJump arrival latch; LP2 cooldown bit-18 pause; LP3
bounded-poll-with-ceiling; LP4 cooperative pause via loop-flag + RESUME re-derive, triggered by
log/state divergence) are correct and carried forward verbatim into the re-run.

---

## RE-RUN 1 RESULT (council `wf_10093608-303`) — route_back → Stage 1, but VERY close

The re-run with the 7 pins confirmed the pins fixed the prior Stage-0 under-specification — **the
spec is now SOUND**. **gen-opus-2 is the proven build base:** the SOLE candidate clean on
spec-conformance + concurrency + boundaries, conforms to the spec verbatim, and **INV1 (live-path
byte-untouched) verified clean by the arbiter** (zero tracked-file modifications). It has exactly
**2 localized build defects** left:

1. **MAJOR (failure-recovery) — STARSMACK→NO_ROUTE fallthrough.** `_det_starsmack(smacked=True,
   fsd_cooldown=False)` returns `None` (CV-pending abstention), so `scene_for` skips STARSMACK and
   `_det_no_route` fires `True` for the same context → a smacked ship (cooldown already cleared)
   routes to NO_ROUTE/idle instead of the STARSMACK escape. The STARSMACK fail-closed branch is a
   doc string no code runs. **FIX:** guard `_det_no_route` / `_det_parked` / `_det_startup`
   against `smacked=True`, and make the STARSMACK fail-closed branch executable (smacked +
   cooldown-cleared → ARRIVAL/STARSMACK, never NO_ROUTE).
2. **MINOR (security) — capability-trust in `_event_name`.** It trusts any object exposing
   `.get('event')` (not `isinstance(ev, dict)`), so a non-dict mock with `.get()→'FSDJump'` injects
   a false arrival. **FIX:** gate the dict branch on `isinstance(ev, dict)`.

The other 3 candidates are non-viable (spec-conformance blockers: missing the determination-routing
layer / `CSeriesState` not an `Enum`). **No merge needed — gen-opus-2 + these 2 surgical fixes is
the whole path.**

**gen-opus-2's artifacts are STASHED durably** (before worktree cleanup) at
`docs/superpowers/specs/cseries_artifacts/`: `primitives.py`, `scenes.py`, `__init__.py`,
`2026-06-15-cseries-boot-determination-spec.md`. (These have the 2 known defects — NOT wired,
NOT moved into `projects/ed-core/src/ed_core/boot/` yet.)

### Path to commit (fast)
- **Option A (recommended):** apply the stash + the 2 fixes above → run the council's standalone
  acceptance tests (T1–T8) + a STARSMACK-fallthrough repro (smacked=True, fsd_cooldown=False →
  STARSMACK, not NO_ROUTE) + `whole_tree_import_check.py projects` (DAG) + the INV1 live-path
  `git diff` empty check → move into `ed_core/boot/` + commit. ~10 min, no 4th council.
- **Option B:** one more council round seeded with gen-opus-2 + the 2 fixes as additional pins for
  the unanimous stamp. ~45 min.

Given these are INERT Phase-2 templates (INV1-clean, not wired to live dispatch) and the fixes are
surgical + executably-verifiable, Option A is the proportionate call. Open dissent to carry: the
single-threaded ArrivalLatch invariant (PIN 6) is documented not enforced — honor it at Phase-2
engine-loop wiring.
