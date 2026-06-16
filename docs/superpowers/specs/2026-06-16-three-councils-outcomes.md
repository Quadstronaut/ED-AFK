# Phase-2 parallel councils — outcomes (2026-06-16)

Three design councils run in parallel (generic guardrailed council-v2 engine,
`council.generic.workflow.js`; arch tier; 4 blind worktree-isolated generators ×
5 adversarial lenses × Opus arbiter; route_back self-iteration ≤2 rounds; no
pytest, no live edits, no auto-commit). Each ran ~37 min / ~1.9M tokens / 21 agents.

## Thread 3 — route-complete delta → **COMMIT** (`wf_d9bc9d37`, gen-sonnet-r2-4)
Unanimous 5-lens pass. The council confirmed the live route-complete flow is
**already solid** (`_is_route_complete`, `dispatch_route_complete`, the `dock.toml`
chain, pit-stop resume) — the delta is small. **Landed `1cb5fd3`** (safe,
game-truth-independent subset): `StationSettleExhausted` telemetry; the D5
same-system-replot guard (suppress needless relaunch, fails open on None);
`_docked_system_addr` tracking in the dispatcher; a fail-soft `confirm_orbiting`
observability step wired into `route_complete_park.toml` (dormant until the HUD
detector lands). **DEFERRED — blocked on operator/CV:**
- **Q2 (journal capture):** deleting `dock_resume.toml`'s blind `wait s=13.0`
  relies on `engage_jump`'s 5-flag Status gate catching star-obstruction. There
  is **no star-proximity Status flag** (grounded fact) — needs a post-launch
  pit-stop jump on a close-in system captured to confirm the FSD flags refuse an
  obstructed jump before the 13s wait can be safely removed.
- **Q1 (mechanic):** capture-at-plot assumes `Status.Destination.Body != 0` at
  the NavRoute event. Fail-safe today (misses → settle re-poll → park).
- **D3 (CV gap):** the ORBITING/ALIGN HUD detector does **not exist**
  (`hud_sc_indicators.json` is data-only, no fixtures). `confirm_orbiting` stays
  fail-soft (returns False) until a separate ed-vision detector + fixtures land.

## Thread 2 — C-series → live wiring → **route_back to Stage 0** (`wf_53c6214d`)
**The important finding.** All four candidates failed spec-conformance at BLOCKER
severity, the same way: wiring `scene_for` into `classify_startup` to kill the 30s
`FRESH_ARRIVAL_WINDOW` **regresses the Robigo fast-resume case by construction.**
Root cause (arbiter verified against the live tree):
- `classify_startup` runs **only on the cold-start/restart path** (dispatcher
  fires `run_classifiers` on the empty-poll branch; live running dispatches via
  the **event routes**, not the classifier).
- On a cold restart there is **no live FSDJump** to arm the LP1 ArrivalLatch, so
  the latch can't decide arrival. The only durable signals — `_last_fsdjump_utc`
  present, `_smacked=False`, `_navroute_cleared=False` — are **identical** for a
  fresh hyperspace arrival vs a multi-hour Robigo SC loiter. The 30s window was
  the **only** discriminator; removing it from the primary path with no
  journal-event replacement breaks AC8 (the named Robigo regression) in every
  candidate. The gate refused to launder a unanimous-but-wrong consensus.

**Implication:** the ArrivalLatch helps the LIVE path (which already works); it
cannot replace the 30s window on the restart path. Wiring C-series into
`classify_startup` is **cold-start-only and low-value** — `classify_startup`'s
existing proximity ladder is already correct. The C-series layer's real value is
realized later, with the Phase-2 action bodies + CV driving the live loop, not by
refactoring the cold-start classifier. **Decision pending operator** (see below).

Contract pins the arbiter logged for any future amend: TRAVERSAL must map to
fallback→legacy sc_resume (the only thing protecting fast-resume); the ARRIVAL
discriminator must yield False for a loiter; PARKED normal-space idle must not emit
the in-SC `RouteCompleteIdleOnRestart` label; **STARSMACK now fires on `smacked`
alone vs legacy `smacked AND fsd_cooldown` — needs explicit operator sign-off.**

## Thread 1 — exploration Stage-1 spec → **route_back to Stage 0**, RE-RUNNING
(`wf_0008160b` then `wf_df2fa2a5` both route_back; re-fired as `wf_a29b643c` with
the Stage-0 spec amended to pin the contract gaps the gate found: name-correlated
visited-set; conservative ORBIT branch with no scex-fallback until OPEN-3; mandatory
wired station-strand recovery; strict `>` on counters; no assumed ctx fields;
S-FILTER under `_excl`; fail-closed ground-truth calibration.) Design-only; in flight.

## Net
The gate did its job — it stopped a Robigo regression (thread 2) and refused to
fabricate game mechanics (thread 3's Q1/Q2). The path to "bot fully off old code"
is gated on real dependencies: operator journal captures (Q1/Q2) and CV detectors
that aren't built (D3, nav-panel arrival discriminator) — not on more council cycles.
