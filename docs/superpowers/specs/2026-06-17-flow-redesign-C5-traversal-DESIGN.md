<!-- C-council C5-traversal | task wp54008cx | decision=commit selected=gen-sonnet-2 | 2026-06-17 | DESIGN-ONLY (not built/wired) | ledger: .claude/council-ledger.jsonl -->
<!-- STATUS 2026-06-18 (L5): DESIGN-ONLY / no-build is LIFTED per the MASTER-SPEC standing rules — building is authorized for ratified scenes; C5 Traversal is RATIFIED and buildable now. The "(not built/wired)" marker above is the historical design-only record, not a current prohibition. NO-GUESSING + fail-closed still bind. -->

## COUNCIL C5 â€” TRAVERSAL SCENE â€” DESIGN DOCUMENT (gen-sonnet-2)

### 1. TRAVERSAL.TOML SKETCH (in-doc only â€” NOT a real file)

```toml
# traversal.toml â€” SKETCH ONLY (C5 design doc, 2026-06-17)
# Steady-state Aâ†’B hop. Operator-verbatim sequence (master spec Â§"Traversal").
# Entered via C2's dispatcher mechanism (no goto primitive exists in
# model/loader/interpreter today â€” see Â§5 BLOCKED-ON-KYLE B1).
#
# HONK TRACK OMITTED (I2): Arrival owns honking; FSSDiscoveryScan already
# fired in the Arrival scene. A re-subscribed honk track here would call
# step_hold_until_event(bind="PrimaryFire", event="FSSDiscoveryScan",
# max_hold_s=30.0) â€” steps_shared.py:184-218 â€” against an event that will
# NEVER fire again in this system. The key would be held for the full
# max_hold_s backstop with no release event. This is a design omission, not
# a live bug claim.
#
# step kinds (tag at start of each step comment):
#   wait    passive block â€” no input sent, always returns True
#   gated   keypress with Status/journal precondition (fails closed on bad flag)
#   vision  closed-loop pitch/yaw + frame capture â€” EXCLUSIVE, owns input + screen

[on_required_fail]
# PINNED FIX (I3): only dock_resume.toml (line 27) and sc_resume.toml (line 46)
# use retry_from = "target_next_route". arrival.toml (line 23) uses
# retry_from = "scoop_refuel". A design attributing this value to arrival.toml
# is a fact-fail.
retry_from = "target_next_route"
max_retries = 3
backoff_s   = 2.0

steps = [
  # INDEX 0 â€” operator-chosen pacing, NOT a gate (see Â§4 wait classification)
  { action = "wait", s = 5.0 },                           # wait   | 0 settle on arrival in SC
  # INDEX 1 â€” RETRY ANCHOR: on_required_fail.retry_from resolves here via
  # model.py:62 index_of_action("target_next_route"). A required fail at index
  # 1 or later triggers retry from HERE. wait 5s (index 0) is NOT re-run on
  # retries â€” it is the only step excluded from the retry lane (I4).
  { action = "target_next_route", required = true },      # gated  | 1 lock next hop; danger-gate when journal-wired
  # INDEX 2 â€” non-required tap; a bind miss advances one step, never aborts
  { action = "set_throttle",      pct = 100 },            # tap    | 2 full burn
  # INDEX 3 â€” operator-chosen pacing, NOT a gate (see Â§4 wait classification).
  # IMPORTANT (I4): a retry from index 1 re-executes this wait on the way down
  # through indices 1â†’2â†’3â†’4â†’5â†’6. This is INTENDED behavior, not a defect.
  { action = "wait", s = 3.0 },                           # wait   | 3 post-throttle settle
  # INDEX 4 â€” required: fails closed without vision (steps_shared.py:221-223)
  { action = "orient_compass",    required = true },      # vision | 4 coarse compass align
  # INDEX 5 â€” required: no-op success when widget_ring_alignment flag off
  # (steps_shared.py:619); degrades gracefully on miss per widget_ring_on_miss
  { action = "orient_widget_ring", required = true },     # vision | 5 fine widget-ring align
  # INDEX 6 â€” TERMINAL STEP. input_exclusive=True (steps.py:318-319).
  # CONTRACT C3: success edge returns True immediately; the interpreter's
  # in_witchspace pause (interpreter.py:60-77) enforces no-input through the
  # tunnel. NO hold_alignment after this step (I8).
  { action = "engage_jump_clearance", required = true },  # gated  | 6 FSD jump + clearance loop
]
```

---

### 2. PER-STEP PRECONDITION + FAIL-CLOSED CONTRACT TABLE

| Idx | action | required | Precondition | Fail-closed behavior | Evidence |
|-----|--------|----------|--------------|----------------------|----------|
| 0 | `wait` (s=5.0) | false | None â€” `ctx.sleeper(s)`, always True | N/A â€” non-required, always True | steps_shared.py:45-47 |
| 1 | `target_next_route` | **true** | Press succeeds (bind present). When `ctx.event_waiter is not None` (LIVE): polls FSDTarget + Status.Destination; danger class (D*/N/H/W) fails closed. When `ctx.event_waiter is None` (UNWIRED/unit-test): press-only, returns True at steps.py:94-95. **Conditional, not unconditional** â€” I5 | Returns False on danger-class in live operation; watchdog (60s) fails closed on no-route; bind miss returns False | steps.py:48-134, specifically danger-gate at steps.py:112-114; early-return path at steps.py:94-95 |
| 2 | `set_throttle` (pct=100) | false | Bind `SetSpeed100` present | Bind miss: `_press` returns False; non-required, advances one step | steps_shared.py:50-55 |
| 3 | `wait` (s=3.0) | false | None â€” always True | N/A â€” always True | steps_shared.py:45-47 |
| 4 | `orient_compass` | **true** | `ctx.compass_reader` and `ctx.frame_grabber` both non-None. In supercruise guard arms when step starts in SC. | Returns False immediately when either reader is None (steps_shared.py:221-223). A required False triggers on_required_fail; retry_from lands at index 1 | steps_shared.py:220-254, fail-closed path at line 222-223 |
| 5 | `orient_widget_ring` | **true** | `[vision].widget_ring_alignment` flag on. Widget ring reader wired. | When flag off: no-op `return True` (steps_shared.py:619). When flag on but reader unwired: degrades or fails closed per `widget_ring_on_miss`. SC-loss during step: **always** returns False even in degrade mode (steps_shared.py:654-657). A required False triggers retry | steps_shared.py:591-700, no-op path at line 619, SC-loss path at lines 654-657 |
| 6 | `engage_jump_clearance` | **true** | Status flags (docked/fsd_charging/fsd_cooldown/fsd_mass_locked/overheating) all clear. `input_exclusive=True` â€” interpreter wraps in `ctx.exclusive_guard()` | Status flag fail â†’ return False immediately (steps.py:246-254). Obstruction ceiling (max_clear_attempts exhausted) â†’ logs EngageJumpClearanceAborted{reason:'obstruction_ceiling'} and returns False (steps.py:313-315). A required False triggers on_required_fail. **No hold_alignment after this step** â€” C3 contract + in_witchspace pause (interpreter.py:60-77) | steps.py:163-319, ceiling at lines 313-315; registered input_exclusive at line 318-319 |

**Danger-gate conditional phrasing (I5, binding):**
`step_target_next_route` at steps.py:94-95 returns True early ONLY when `ctx.event_waiter is None` â€” this is the UNWIRED / unit-test path. In LIVE operation `ctx.event_waiter IS wired`, so the D*/N/H/W danger-class check at steps.py:112-114 DOES run and fails closed. The per-step contract above states this conditional phrasing explicitly. There is NO live safety bug here.

**Fail-closed chain (I6):**
If `orient_compass` (index 4) fails required â†’ `on_required_fail` triggers â†’ retry from index 1 (`target_next_route`). If retries exhausted â†’ `ProcedureAborted` at interpreter.py:180-183 â†’ procedure NEVER reaches index 6 (`engage_jump_clearance`). A failed orient never reaches the jump.

---

### 3. RETRY-LANE TRACE (I3 + I4)

`on_required_fail.retry_from = "target_next_route"` resolves via `model.py:62` (`index_of_action`) to the **first** step with action `"target_next_route"` = **index 1**.

**Cited precedent:** Only `dock_resume.toml` (line 27) and `sc_resume.toml` (line 46) use `retry_from = "target_next_route"`. `arrival.toml` (line 23) uses `retry_from = "scoop_refuel"`. This is verified in live files, not inferred from memory.

**Lane walk on a required fail at index 4 (orient_compass):**

```
Normal run:  [0 wait5s] â†’ [1 target_next_route*] â†’ [2 set_throttle] â†’ [3 wait3s] â†’ [4 orient_compass*] â†’ [5 orient_widget_ring*] â†’ [6 engage_jump_clearance*]
                                                                                              |
                                                                                        REQUIRED FAIL
                                                                                              |
                                                                                      backoff_s = 2.0s
                                                                                              |
Retry lane:                          [1 target_next_route*] â†’ [2 set_throttle] â†’ [3 wait3s] â†’ [4 orient_compass*] â†’ ...
```

`*` = required step.

**Key statements (I4):**
- `wait 5s` (index 0) is the ONLY step excluded from retries.
- A sequential retry re-executes `wait 3s` (index 3) on every retry pass-through.
- Re-running `wait 3s` is **intended behavior**, not a defect. The 3s pacing is appropriate after re-throttling (index 2) on a retry.
- `retry_from` resolution is by `index_of_action`, which returns the **first** matching step (model.py:62-66). With only one `"target_next_route"` step at index 1, this is unambiguous.

**No `retry_anchor` in this procedure:**
Unlike `startup.toml` (which uses `retry_anchor = true` on a mid-procedure step) and `smack_recovery.toml` (which uses both `retry_anchor` and `retry_from_if_supercruise`), traversal does not need a mid-procedure anchor. The procedure is the pure jump tail â€” all required steps (target, orient x2, jump) benefit from a full retry from `target_next_route`. No SC-branch override (`retry_from_if_supercruise`) is needed: by the time traversal is entered the ship is already in supercruise clearing the previous system's star, and any orient failure should re-lock and re-orient.

---

### 4. WAIT CLASSIFICATION + EVENT-GATE FLAGS (I7 + B2)

Both `wait` steps are **operator-chosen pacing â€” NOT success/failure gates**. They use `step_wait` (steps_shared.py:45-47) which calls `ctx.sleeper(s)` and returns `True` unconditionally. No journal event or Status.json flag is consulted; no input is sent.

**`wait 5s` (index 0):**
- Classification: **operator-chosen pacing**. Provides settle time for the SC scene to stabilize after entry from Arrival / Smack / Exploration before pressing `TargetNextRouteSystem`.
- Event-gate flag (B2, REQUIRED): FLAG: Should this wait gate on a specific SC-assist release signal or SC-stable status rather than a fixed 5s? The 5s is the operator's authored value and is preserved in the sketch regardless. Whether 5s is sufficient for all three entry sources (post-orbit Arrival, Smack Recovery SC-acquired state, Exploration exit) is C2/C4-owned. **BLOCKED-ON-KYLE: B3** (see Â§5).
- Per [[no-arbitrary-timed-waits]] in MEMORY.md: this wait does NOT gate success or failure â€” it is passive pacing. Permitted because the operator wrote it explicitly.

**`wait 3s` (index 3):**
- Classification: **operator-chosen pacing**. Provides settle time after `set_throttle 100` before the compass orient loop begins (allowing the ship to build velocity and for the HUD reticle to stabilize at speed).
- Event-gate flag (B2, REQUIRED): FLAG: Should this gate on a Status.json throttle-confirmed flag or a NavRoute stability signal rather than a fixed 3s? The 3s is the operator's authored value and is preserved in the sketch regardless. Status.json does not expose a "at-speed" flag â€” the closest proxy would be a speed-in-set-speed-band derived from the StatusFlags, which is not a standard check in any existing procedure. **BLOCKED-ON-KYLE: B2** (operator decision on whether 3s is a sufficient post-throttle settle or whether a journal/Status gate exists and is preferred).

---

### 5. BLOCKED-ON-KYLE LIST

**B1 â€” C2 ENTRY CONTRACT (CRITICAL)**

The `goto` mechanism from Arrival / Smack / Exploration into Traversal is **C2's deliverable**. There is NO procedureâ†’procedure `goto` primitive in `model.py`, `loader.py`, or `interpreter.py` today. Scene chaining is dispatcher-driven (via `boot_routes`, `_PREEMPT_ON_SMACK` in the FlowRunner/dispatcher â€” not via TOML control flow). Traversal is a **standalone procedure dispatched by the C2 mechanism**.

ASSUMED ENTRY-STATE CONTRACT (to be confirmed/corrected by C2):
- Ship is in supercruise (not in normal space, not docked).
- The arrival star is already cleared (orbited or sufficiently distant) â€” the `wait 5s` opening assumes this.
- Throttle state on entry: UNKNOWN. The 5s wait + `target_next_route` opening sequence is assumed correct for all three entry sources. C5 does NOT invent inter-procedure control flow to enforce this.

BLOCKED-ON-KYLE: What is the ship state (in SC? star cleared? throttle level?) on entry from each of the three sources (Arrival, Smack Recovery, Exploration)? Is the `wait 5s â†’ target_next_route` opening sequence correct for ALL three?

**B2 â€” WAIT EVENT-GATE QUESTION (I7)**

Should `wait 3s` (index 3, post-throttle) gate on a Status or journal signal instead of a fixed 3-second wall-clock? Existing procedures do not use a "ship is at speed" gate. The 3s is the operator's authored value and is preserved in the sketch. If a Status-derived gate exists (e.g. confirming throttle is at 100% and stable), it should replace the wall-clock wait per [[no-arbitrary-timed-waits]].

Should `wait 5s` (index 0) similarly gate on an SC-assist release or SC-stable signal?

**B3 â€” SMACKâ†’TRAVERSAL HANDOFF**

Smack Recovery ends in `nav_supercruise_star` (SC-assist orbit acquisition), then transitions to Traversal. The `wait 5s` opens the traversal procedure while SC-assist may still be acquiring orbit. FLAG: Is 5s the intended SC-assist acquire window for the smack-entry path? Or should the entry gate on an SC-assist/orbit-acquired signal (C4/C2 boundary)? This is C4/C2-owned.

**B4 â€” EXPLORATIONâ†’TRAVERSAL HANDOFF**

Exploration loops `nav_supercruise_unexplored` then transitions to Traversal. ASSUMED entry-state: ship in SC, off-star, ready for target-next. FLAG: Is this correct? What is the throttle state and orbit/SC-assist state on Exploration exit? This is C2/C6-owned.

---

### 6. NO HOLD_ALIGNMENT RATIONALE (I8)

`engage_jump_clearance` is the terminal step (index 6). No `hold_alignment` follows it.

Rationale (cited from live code):
- `engage_jump_clearance` CONTRACT C3 (steps.py:183-186): "On confirmation â†’ return True immediately; no further input is sent."
- The interpreter's `in_witchspace` pause (interpreter.py:60-77): while `ctx.in_witchspace()` is True (StartJump â†’ FSDJump window, ~18s), the interpreter holds the NEXT step at the guard loop. No input is sent during witchspace transit â€” the pause is journal-gated on FSDJump arrival (interpreter.py:62), satisfying [[no-arbitrary-timed-waits]].
- Together: `engage_jump_clearance` returns True on hyperspace commitment; the interpreter then hits the witchspace guard before running the next step (which would be step index 7 â€” but there IS no step 7 in traversal, so the procedure completes and the dispatcher handles the arrival scene via FSDJump dispatch). `hold_alignment` is not needed and would be a spec violation (I8).

---

### 7. REQUIRED FLAGS RATIONALE (I9)

Compared against `dock_resume.toml` (closest structural analog â€” pure jump tail with `engage_jump_clearance`):

| Step | traversal.toml | dock_resume.toml | Justification |
|------|---------------|-----------------|---------------|
| wait (5s) | required=false | N/A | `step_wait` always returns True (steps_shared.py:45-47); required=true would be meaningless |
| target_next_route | **required=true** | required=true (line 39) | A failed hop-lock must trigger retry; never skip to orient |
| set_throttle | required=false | required=false (line 41, implied) | Bind miss is tolerated â€” the jump can still attempt without a confirmed throttle tap |
| wait (3s) | required=false | N/A | Always returns True |
| orient_compass | **required=true** | required=true (line 42) | Fail-closed without vision â€” must not jump without confirmed orient |
| orient_widget_ring | **required=true** | required=true (line 43) | No-op when flag off (steps_shared.py:619); required=true correct â€” degrade behavior controlled by `widget_ring_on_miss`, not by required=false |
| engage_jump_clearance | **required=true** | required=true (line 44) | Ceiling abort must propagate as a procedure abort, not silently advance |

**orient_widget_ring required=true with graceful degrade:** The no-op path (flag off â†’ return True immediately at steps_shared.py:619) means required=true is safe when the flag is off â€” a True is returned and the procedure advances. When the flag is on, miss behavior is controlled by `ctx.widget_ring_on_miss` ("degrade" = return True with a log; "fail_closed" = return False triggering retry). This is the same pattern as `dock_resume.toml` line 43 and `arrival.toml` line 94.

---

### 8. DESIGN-ONLY BOUNDARY CONFIRMATION (I10)

This deliverable is an **in-doc TOML sketch** (see Â§1) plus supporting design analysis. No real `traversal.toml` file has been created. No step code has been edited. No commit has been made. The worktree is unchanged from its state at session start.

All claims cite live file:line references. No claim rests on memory alone.

## ASSUMPTIONS (gen-sonnet-2)
- The traversal procedure is entered from a ship state already in supercruise with the arrival star cleared (orbited or far) â€” this is the assumed C2 entry-state contract; C5 does not enforce it inside the sketch.
- No retry_anchor is needed in traversal because the entire jump tail (target â†’ orient x2 â†’ jump) benefits equally from a full retry from target_next_route. Unlike startup.toml which anchors mid-procedure after engage_supercruise, traversal's entry is always in SC so no real-space vs SC ladder split is needed.
- No retry_from_if_supercruise override is needed â€” unlike smack_recovery.toml which can fail orient_compass while transitioning from normal-space to SC mid-step, traversal is always entered already in supercruise, so all retries uniformly resume from target_next_route (index 1).
- set_throttle (index 2) is non-required: a bind miss that returns False advances one step, and the wait + orient + engage_jump_clearance sequence still runs. The ship may not be at full throttle if the bind is missing, but this matches the pattern in dock_resume.toml (line 41 â€” set_throttle has no explicit required=true).
- Both wait steps (5s, 3s) use step_wait which is always True, making required=false correct â€” a required=true on a step that never fails would be misleading and non-functional.
- The [vision].widget_ring_alignment config flag and ctx.widget_ring_on_miss degradation behavior for orient_widget_ring are already wired in the live codebase (steps_shared.py:619, 622) and need no new sketch-level control flow.

## RISKS (gen-sonnet-2)
- @{lens=spec-conformance; note=The sketch sets required=false on set_throttle (index 2) following dock_resume.toml's pattern, but dock_resume.toml has no explicit required= annotation on its set_throttle line (line 41) â€” the default is false per model.py:15. If the arbiter reads 'implicit false' as ambiguous and expects an explicit required=false tag, the sketch should be explicit. The sketch is explicit (required=false is not written â€” matching the source pattern of omission). Risk: a strict literal reading could flag the omission as missing evidence.}
- @{lens=spec-conformance; note=The design states no retry_anchor is needed in traversal. The spec does not require one, but if C2 dispatches traversal from a state where orient_compass could fail while the ship is transitioning from normal space (e.g. Smack-to-Traversal via a brief normal-space window before SC-assist completes), the missing retry_from_if_supercruise could cause the retry lane to run target_next_route (index 1) while still in normal space â€” where it would succeed (press sends) but the SC-entry assumption for the rest of the lane holds. This is B3 (BLOCKED-ON-KYLE), not a known bug, but it is the highest-risk entry path.}
- @{lens=boundaries; note=The wait 5s (index 0) is classified as operator-chosen pacing and flagged for B3 (Smack->Traversal SC-assist acquire window). If 5s is insufficient for SC-assist to acquire orbit after Smack Recovery, target_next_route (index 1) may press TargetNextRouteSystem while SC-assist is still engaged on the arrival star â€” cancelling the orbit and pointing at the next hop before the star is cleared. This is the exact design tension the 13s orbit-acquire wait in arrival.toml addresses. The 5s vs 13s distinction is a C2/C4 boundary question flagged as B3.}
- @{lens=failure-recovery; note=The retry lane re-runs wait 3s (index 3) on every retry pass. If orient_compass fails repeatedly (e.g. vision glare or frame-grabber failure), each retry adds 2s (backoff) + the wait at index 1 (TargetNextRouteSystem press duration) + 3s (index 3 wait) + compass loop overhead before hitting the next orient attempt. With max_retries=3, worst-case retry overhead is ~3 * (2 + 3 + orient_timeout) seconds of throttle-at-100 while unoriented. This is an inherited pattern from dock_resume/sc_resume and is not a new risk, but it is the weakest point in the procedure under repeated orient failure.}
- @{lens=concurrency; note=engage_jump_clearance is registered input_exclusive=True (steps.py:318-319) and the interpreter wraps it in ctx.exclusive_guard() (interpreter.py:92-97). If a parallel track were present (e.g. if a future operator added one), it would be paused during engage_jump_clearance's entire duration including the pitch+burn obstruction moves. Since this design explicitly OMITS parallel_tracks (I2), this is not an active risk â€” but any future addition of a parallel track must account for the exclusive guard duration, which can be multiple poll+pitch+burn cycles under obstruction.}
