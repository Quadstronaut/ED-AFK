# Phase-2 forward-direction councils — outcomes (2026-06-16)

Operator chose to pursue all three forward directions in parallel after the C-series
wiring hit its wall. Three more councils (generic guardrailed engine).

## Council A — C-series SAFE wiring → **COMMIT → LANDED `947b8c5`** (`wf_9caf0f7c`, gen-opus-r1-2)
Unanimous, 1 round. `classify_startup` now routes through `build_determine_context → scene_for
→ _STATE_TO_PROC` (total over 11 states, asserted at import). Unambiguous scenes map to existing
live procedures; ARRIVAL gated on the live-armed `ArrivalLatch` (armed in `_route_fsd_jump`),
STARSMACK keeps the legacy `AND fsd_cooldown` gate (relaxation **held for operator sign-off**).
TRAVERSAL/REFUEL/EXPLORATION/PAUSE/RESUME + any abstention + **any exception** → `_classify_startup_legacy`
(verbatim old body; the 30s `FRESH_ARRIVAL_WINDOW` survives only here → no Robigo regression).
PARKED → `ParkedIdleNormalSpace`. INV1: diff bounded to `boot_routes.py`. Strictly ship-safe
(worst case = behaves exactly like today). The determination layer is now LIVE.

## Council C — Phase-2 ACTION layer → **COMMIT** (`wf_2852eb0a`, gen-opus-r2-1) — follow-on, not yet built
Headline: **RETIRE the 11 inert `act()` bodies** — they paraphrase procedures that already exist;
replace the callables + `_act_pending` with an inert `proc: str | None` field on `SceneTemplate`
(scenes.py stays ship-untouching). Removes the NotImplementedError trap + the fork-flight-logic
temptation. **Precondition:** valid only because Council A's mapping covers all 11 states; EXPLORATION
(honk/FSS/body-tour) + RESUME (re-derive) have no standalone live proc → stay OPEN. Buckets:
- **A (buildable now):** `step_await_orbit_acquired` scaffold (guarded wait until B1 lands), engage_jump
  5-flag gate test scaffold, Status-flag-gate pattern reuse.
- **B (CV-detector-blocked):** `detect_orbiting`/`detect_align_target` (the `hud_sc_indicators` matcher —
  consumer `step_confirm_orbiting` already exists; module does NOT). Shared with Council B.
- **C (game-truth-blocked):** the close-system jump confirmation = **Q2, now RESOLVED** (see the Q2 doc:
  obstruction is HUD-only → CV-free `StartJump`-absence loop).
Do the act()-retire AFTER the C-series wiring proves out live. Design: `_council_actionlayer_*` (scratch).

## Council B — nav-panel ARRIVAL-vs-LOITER CV → **route_back** (`wf_e5066c4e`, gen-opus-r2-1) — DEFERRED optimization
Designed a fusion discriminator (HUD prompt > Destination+distance band > nav row-1 distance >
jump_age) but route_back: it needs net-new HUD matchers (`detect_orbiting`/`detect_align`, shared
with Council C bucket B) + a nav-panel row-1 **distance** read (the distance column is currently
cropped OUT) + calibration frames + OQ-1 (does fresh-arrival-nose-on-star show ALIGN/ORBITING/neither?).
**Verdict: deferred.** The 30s window already works for cold-start (Council A preserves it), so this
CV is an OPTIMIZATION to retire a blind proxy, not a correctness fix — not worth the net-new CV +
frame-capture now. One generator seat (gen-sonnet-r1-2) died on a transient API 500; the workflow
filtered it and completed on the other three (this was the "failed agent" the operator saw).

## Session net
LANDED: route-complete safe delta (`1cb5fd3`), exploration Stage-1 design baseline, **C-series
determination wiring live (`947b8c5`)**, Q2 game-truth resolved (CV-free). RATIFIED designs: action
layer (act()-retire + buckets). DEFERRED: arrival-vs-loiter CV (optimization, frame-blocked).
NEXT buildable (CV-free, operator-unblocked): the `dock_resume` 13s-wait → `StartJump`-absence
retry loop. PENDING operator: STARSMACK `smacked`-alone sign-off (held safe meanwhile).
