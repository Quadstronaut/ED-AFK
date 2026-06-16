REVIEWED-SPEC SCAFFOLD (Stage 0) â€” Hyperspace-Jump Flow centered on StartJump. STATUS: arbiter-drafted design spec for blind Stage-1 generation. This is a design contract, not a code diff. Every downstream stage is judged against it.

=== GAME-TRUTH ANCHORS (from grounding docs, do NOT re-derive) ===
G1. An obstructed hyperspace jump is HUD-ONLY: `FRAME SHIFT CANCELLED: DESTINATION TARGET OBSCURED` + dashed jump-ring. It is NOT journaled and NOT a Status flag. (q2-jump-obstruction-finding.md; obstruction-and-smack-game-truth.md.)
G2. Obstruction is from MASSIVE BODIES: STARS block, PLANETS block, STATIONS do NOT. The clearance maneuver pitches away from the nearest massive body (star/planet), never a station.
G3. The ONLY reliable obstruction signal CV-free: after `engage_jump` presses the key, NO hyperspace `StartJump` event fires. Absence-of-StartJump == obstructed (chosen option (b), operator-confirmed 2026-06-16).
G4. The retry MUST MOVE the ship (pitch off the body + throttle + bounded fly), not re-press in place. Operator: "if I didn't move it would still be obstructed."
G5. POST-StartJump (HYPERSPACE leg ONLY): the moment a Hyperspace StartJump fires, the FSD auto-aligns the ship to the destination. ALL ship input may STOP. Post-StartJump alignment steps (hold_alignment / orient) on a hyperspace jump are REDUNDANT. Does NOT apply to the real-space->SC (supercruise-entry) transition.
G6. CODEBASE FACT (verified in repo, load-bearing): `StartJump` is emitted for BOTH hyperspace AND supercruise entry; the typed event carries `JumpType` ("Hyperspace" | "Supercruise") -> events.py:StartJump.jump_type. The name-only `event_waiter(name, timeout)` (context.py:38) CANNOT read JumpType. The dispatcher already disambiguates at the event-route layer (dispatcher.py:631,650) and the Status side has bit 30 `fsd_jump` ("hyperspace committed", steps_shared.py:427). The clearance loop's success edge MUST be a HYPERSPACE StartJump, not a supercruise one â€” this drives the bounded-poll contract (see OQ1).

=== PART 1 â€” CLEARANCE LOOP (kill the blind 13s waits) ===

NEW STEP (file:symbol): ed_autojump/flow/steps.py : step_engage_jump_clearance, registered register_step("engage_jump_clearance", step_engage_jump_clearance, input_exclusive=True) (it owns input: presses jump AND drives a pitch+burn maneuver). This step REPLACES the engage_jump + hold_alignment pair AND the preceding blind wait s=13.0 "clear the star" step in the hyperspace-jump tail.

Signature (proposed; tune in Stage 1):
  step_engage_jump_clearance(ctx, *, poll_s=0.8, max_jump_polls=12, max_clear_attempts=3, pitch_dir="down", pitch_hold_s=<ship-scaled, TODO OQ2>, clear_burn_s=7.0, retry_throttle_pct=100) -> bool

CONTRACT (bounded-poll-on-StartJump):
  C1. Press jump exactly as step_engage_jump does today (Status-flag preconditions docked/fsd_charging/fsd_cooldown/fsd_mass_locked/overheating -> False fail-closed; then SetSpeed100; then `Hyperspace` bind). REUSE step_engage_jump's gate logic â€” do NOT duplicate or weaken it.
  C2. After the press, BOUNDED-POLL for a HYPERSPACE StartJump. Bound is a READ-COUNT (max_jump_polls), NEVER a wall-clock deadline. Precedent: dispatch_route_complete's max_reads read-count bound (boot_routes.py:400) â€” chosen explicitly because the test harness freezes the clock (clock=lambda:0.0), so any wall-clock deadline either never trips (spins forever) or is non-deterministic. Each poll iteration MUST honor ctx.should_abort() (operator backstop).
  C3. SUCCESS EDGE: a hyperspace StartJump is confirmed -> return True immediately, ceasing all input (this IS the PART 2 release point â€” see C7). Confirmation source priority: (a) the hyperspace-discriminated StartJump signal (OQ1 â€” name-only event_waiter insufficient per G6); (b) Status bit 30 fsd_jump going true as the state-side fallback (event-gates-need-state-check; matches _HOLD_SUCCESS_FLAG["StartJump"]="fsd_jump"). The dispatcher's in_witchspace latch (context.py:51, set only on JumpType==Hyperspace) is a candidate clean hyperspace-only signal â€” see OQ1.
  C4. OBSTRUCTED EDGE (StartJump absent after max_jump_polls reads): MOVE TO CLEAR. Pitch AWAY from the nearest massive body, then throttle retry_throttle_pct, then fly a BOUNDED bit (clear_burn_s as TRAJECTORY-PACING â€” explicitly NOT a gate, same class as dock_blind_maneuver's burn_s/pitch_s). Then RETRY from C1. The maneuver MUST move the ship (G4).
  C5. REUSE the existing pitch-off maneuver. Candidates in-repo: step_dock_blind_maneuver's body (PitchDownButton hold scaled by ship size via ship_sizes.pitch_s_for_ship, then SetSpeed100, then burn â€” steps.py:1072+), OR step_pitch(dir,hold_s) / step_pitch_compass(until="behind"). The pitch DIRECTION when the obstructing body is the arrival star vs a planet is NOT specified by the docs -> STUB (OQ2). Do NOT hardcode a direction as game-truth; expose pitch_dir, default to the operator's existing "down = the fixed pick" with a TODO.
  C6. CEILING ABORT (fail-backstop, NOT a success gate): bound the NUMBER of move+retry cycles by an attempt ceiling (max_clear_attempts, e.g. 3). When hit with still no StartJump, return a NAMED failure: log EngageJumpClearanceAborted{reason:"obstruction_ceiling",attempts,polls_per_attempt} and return False, routing to on_required_fail. The ceiling is a backstop against an un-clearable scene, NEVER a way to declare success.
  C7. POST-SUCCESS: on the SUCCESS edge (C3) the step returns True and sends NO further alignment input. This subsumes the deleted hold_alignment. The interpreter's in_witchspace pause (context.py:51) then suppresses all steps through the tunnel until FSDJump â€” so "cease input" is enforced by the existing latch, and the step need only confirm the jump fired.

LOGGING (every edge observable): EngageJumpClearancePress{attempt}, EngageJumpClearanceStarted (hyperspace StartJump confirmed, via event|state), EngageJumpClearanceObscured{attempt,polls}, EngageJumpClearanceMove{pitch_dir,pitch_hold_s,burn_s}, EngageJumpClearanceAborted{reason,attempts}.

=== PART 1 â€” TOML EDITS ===

dock_resume.toml: REMOVE step 5 wait s=13.0 ("clear the station"). REPLACE steps 7+8 (engage_jump required + hold_alignment required) with a single { action = "engage_jump_clearance", required = true }. NOTE: at dock_resume the obstructing body is the arrival STAR (the station does NOT obstruct â€” G2), so the maneuver pitches off the star.

arrival.toml: REMOVE step 5 wait s=13.0 ("let orbit acquire") and step 8 wait s=13.0 ("clear the star"). REPLACE steps 10+11 (engage_jump + hold_alignment) with a single { action = "engage_jump_clearance", required = true }. CAUTION on step 5: it is the post-sc_assist_orbit "let orbit acquire" settle, NOT a jump-clearance wait â€” removing it changes orbit-acquisition timing (OQ3). The task names BOTH for KILL; spec records the distinction so downstream stages do not conflate an orbit settle with a jump-clearance burn.

=== PART 1 â€” SCOPE BEYOND THE NAMED KILL LIST (arbiter flag) ===
The SAME wait s=13.0 + engage_jump + hold_alignment hyperspace-jump tail ALSO appears in sc_resume.toml (0c/5/6) and startup.toml (16/19/9/10 and recovery 22/23, incl. retry_anchor=true on step 19). The KILL list names ONLY dock_resume + arrival. Replacing the tail in only 2 procedures leaves TWO divergent hyperspace-jump tails â€” a reviewer-catchable inconsistency. -> OQ4. NOT decided here.

=== PART 2 â€” POST-StartJump CEASE INPUT ===
PART 2 is REALIZED by PART 1's design, not a separate step: the SUCCESS edge of step_engage_jump_clearance (C3) IS the release-all-input point (C7). The redundant post-StartJump steps are the hold_alignment steps removed in the TOML edits. No orient_* step runs after StartJump in any current hyperspace tail (orient precedes engage_jump), so the only redundant post-StartJump input is hold_alignment â€” confirmed removed. The in_witchspace interpreter pause already enforces no-input through the tunnel.

SC-ENGAGE SEPARATION (hard boundary, G5): step_engage_supercruise and every supercruise-entry path (its SupercruiseEntry/in_supercruise gating, hold_alignment with until_event="SupercruiseEntry" if any) are UNTOUCHED. The cease-input fact applies to the HYPERSPACE leg only. step_engage_jump_clearance MUST confirm a HYPERSPACE StartJump specifically (G6/OQ1) so it can never mistake a supercruise StartJump for jump success and prematurely release input on the SC-entry path.

=== KEEP (do not delete) ===
step_hold_alignment stays in steps_shared (still the SupercruiseEntry-gated hold for SC-entry paths; only its USE on hyperspace tails is removed). step_engage_jump stays (clearance loop reuses its gate logic; may remain a registered primitive â€” OQ5). step_orient_compass / step_orient_widget_ring stay (they precede the jump, unaffected).


## INTERFACE

NEW: ed_autojump/flow/steps.py : step_engage_jump_clearance(ctx, *, poll_s=0.8, max_jump_polls=12, max_clear_attempts=3, pitch_dir="down", pitch_hold_s=<ship-scaled, TODO OQ2>, clear_burn_s=7.0, retry_throttle_pct=100) -> bool. Registered: register_step("engage_jump_clearance", step_engage_jump_clearance, input_exclusive=True). Reuses: step_engage_jump's Status-flag gate + SetSpeed100 + Hyperspace press (steps.py:136); a pitch-off maneuver primitive (step_dock_blind_maneuver body steps.py:1072 / step_pitch / step_pitch_compass). Confirms success via: a hyperspace-discriminated StartJump signal (OQ1) with Status bit 30 fsd_jump (_HOLD_SUCCESS_FLAG, steps_shared.py:427) as state fallback. Honors ctx.should_abort() every iteration. Context deps already present: ctx.event_waiter (context.py:38, name-only â€” see OQ1), ctx.status_supplier, ctx.in_witchspace (context.py:51), ctx.clock/sleeper (frozen in tests). TOML: dock_resume.toml (remove wait@5, replace engage_jump@7+hold_alignment@8 with engage_jump_clearance); arrival.toml (remove wait@5, wait@8, replace engage_jump@10+hold_alignment@11). UNTOUCHED: step_engage_supercruise + all SC-entry gating; step_hold_alignment definition (kept for SC-entry use).



## INVARIANTS

### [1]
No wall-clock value is ever a success or failure GATE. Every bound in step_engage_jump_clearance is a read-count / attempt-count (max_jump_polls, max_clear_attempts); poll_s and clear_burn_s are cadence/trajectory-pacing only. (no-arbitrary-timed-waits)

### [2]
The clearance loop's success edge is a HYPERSPACE StartJump (JumpType==Hyperspace), never a supercruise StartJump. A supercruise StartJump must NOT satisfy the hyperspace clearance gate. (G6)

### [3]
Every move+retry cycle MOVES the ship (pitch off the nearest massive body + throttle + bounded burn) before re-pressing jump â€” never a re-press in place. (G4)

### [4]
The clearance maneuver pitches away from a MASSIVE body (star/planet) only; stations are never the obstruction and never the pitch reference. (G2)

### [5]
The attempt ceiling (max_clear_attempts) is a FAIL backstop that yields a NAMED abort routed to on_required_fail; it is NEVER a path to declaring jump success. (C6)

### [6]
On the success edge the step ceases ALL ship input; no alignment/orient/hold input is sent after a confirmed hyperspace StartJump. The in_witchspace interpreter pause enforces no-input through the tunnel. (G5/C7)

### [7]
The SC-engage / supercruise-entry path is byte-untouched: its gating, its hold_alignment(until_event=SupercruiseEntry), and engage_supercruise are unchanged. The cease-input fact is hyperspace-only. (G5)

### [8]
step_engage_jump_clearance retains step_engage_jump's full Status-flag fail-closed gate (docked/charging/cooldown/mass_locked/overheating); it never presses jump into a forbidden state. (C1)

### [9]
Under a frozen test clock (clock=lambda:0.0) and a no-op sleeper, the loop terminates deterministically on the read-count bounds â€” no spin, no clock dependence. (C2 precedent: dispatch_route_complete max_reads)

### [10]
Removing the blind wait s=13.0 from a hyperspace tail removes a trajectory-pacing duration ONLY; the orbit-acquire wait (arrival step 5) is a DIFFERENT concern and its removal is gated on OQ3.



## ACCEPTANCE_CRITERIA

### [1]
AC1 (StartJump fires -> cease input -> completes): with status/event fakes scripted so a HYPERSPACE StartJump (and/or fsd_jump bit 30) is observed within max_jump_polls of the jump press, step_engage_jump_clearance returns True, sends NO pitch/orient/hold input after the press, and logs EngageJumpClearanceStarted. Downstream FSDJump confirms completion via the existing event route (_route_fsd_jump).

### [2]
AC2 (StartJump absent -> move + retry): with fakes scripted so NO StartJump fires on attempt 1's polls but one fires on attempt 2, the step performs exactly one move-to-clear cycle (pitch off body + throttle + bounded burn â€” observable as PitchDown/SetSpeed presses + EngageJumpClearanceMove log), re-presses jump, then returns True. The ship demonstrably MOVED between presses (G4).

### [3]
AC3 (ceiling -> named abort): with fakes scripted so StartJump NEVER fires, the step performs at most max_clear_attempts move+retry cycles, then returns False with log EngageJumpClearanceAborted{reason:'obstruction_ceiling'}, routing to on_required_fail. It NEVER returns True without a StartJump confirmation.

### [4]
AC4 (deterministic under frozen clock): with clock=lambda:0.0 and a no-op sleeper and a fake that never yields StartJump, the loop terminates via read-count bounds (no infinite spin). With the same frozen clock and a StartJump on poll N, it returns True. No assertion depends on wall-clock elapsed time.

### [5]
AC5 (SC-engage path untouched): the supercruise-entry path (engage_supercruise + its hold_alignment until_event=SupercruiseEntry, if present) is unchanged â€” its tests/behavior byte-identical. A SUPERCRUISE StartJump does NOT satisfy step_engage_jump_clearance's hyperspace gate (it keeps polling / eventually ceiling-aborts), proving hyperspace-discrimination (G6).

### [6]
AC6 (TOML wiring): dock_resume.toml no longer contains wait s=13.0 at step 5 nor a separate engage_jump+hold_alignment pair; arrival.toml no longer contains the two wait s=13.0 (steps 5,8) nor the engage_jump+hold_alignment pair â€” each replaced by a single engage_jump_clearance. All procedures still pass TOML/registry validation (every action name resolves in the merged step table).

### [7]
AC7 (fail-closed gate preserved): with a status fake reporting fsd_mass_locked (or any forbidden flag) at entry, step_engage_jump_clearance refuses to press jump and returns False (EngageBlocked-equivalent), identical to step_engage_jump today.

### [8]
AC8 (abort responsiveness): with should_abort() flipping true mid-poll or mid-move, the step returns False promptly (logs reason 'abort'), not after exhausting the bounds.



## OPEN_QUESTIONS

### [1]
OQ1 (BLOCKER for Stage 1 â€” hyperspace discrimination): the name-only event_waiter(name,timeout) (context.py:38) CANNOT read JumpType, but StartJump fires for BOTH hyperspace and supercruise (G6). The clearance loop's success edge MUST be a HYPERSPACE StartJump only. HOW does the step observe a hyperspace-specific StartJump? Candidates in-repo: (a) Status bit 30 fsd_jump (_HOLD_SUCCESS_FLAG['StartJump']='fsd_jump', steps_shared.py:427) as the hyperspace-committed state signal; (b) ctx.in_witchspace() (context.py:51, set ONLY on JumpType==Hyperspace by dispatcher.py:631) as a clean hyperspace-only latch; (c) a NEW typed/hyperspace-filtered waiter on ctx. Stage 1 MUST pick one and justify it; a bare event_waiter('StartJump',...) is a defect (T5 would fail). NOT decided here.

### [2]
OQ2 (game-truth STUB â€” anti-body pitch direction): the docs do NOT specify the pitch direction to clear obstruction when the obstructing body is the arrival STAR vs a PLANET (planet dark-side case gives no direction). The existing convention is dock_blind_maneuver's 'down = the fixed pick' (operator: 'any random direction'). Is 'down' always adequate to clear both a star and a planet, or does the planet/dark-side case need a different reference (e.g. away-from-body via compass)? STUB with pitch_dir param + TODO; do NOT assert a mechanic. Operator/game-truth input needed.

### [3]
OQ3 (arrival.toml step 5 â€” orbit-acquire vs clearance): arrival step 5 wait s=13.0 is 'let orbit acquire' (post sc_assist_orbit), a DIFFERENT concern from the step 8 'clear the star' clearance burn. The task names both for KILL, but removing the orbit-acquire settle changes orbit-acquisition behavior independent of the StartJump loop. Confirm: is the orbit-acquire 13s also dead (is orbit acquisition event/state-observable?), or only the clearance 13s? If orbit-acquire must stay, it needs a state/event gate, not a blind wait. Operator/design input needed.

### [4]
OQ4 (scope â€” all 4 hyperspace tails or only 2?): the identical blind-13s + engage_jump + hold_alignment hyperspace tail also lives in sc_resume.toml (0c/5/6) and startup.toml (16/19/9/10 and recovery 22/23, incl. retry_anchor=true on step 19). Replacing the tail in dock_resume/arrival ONLY leaves two divergent jump tails. Does engage_jump_clearance replace the hyperspace tail EVERYWHERE (recommended â€” one canonical tail), or strictly the 2 named procedures? Note startup's retry_anchor semantics on the 13s wait must be re-homed if that wait is removed. Operator/scope decision needed.

### [5]
OQ5 (does engage_jump survive as a primitive?): if the clearance loop fully subsumes engage_jump on every tail (OQ4=everywhere), is step_engage_jump retired or kept as a registered primitive reused internally? Keeping it (clearance loop calls its gate logic) avoids duplicating the Status-flag fail-closed gate. Recommend keep-and-reuse; confirm in Stage 1.

