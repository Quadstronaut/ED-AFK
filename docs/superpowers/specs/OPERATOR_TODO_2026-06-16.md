# OPERATOR TODO — game-truth gaps Operator fills (2026-06-16)

All 4 councils LANDED (C1-C4) + the stale-editable fix. NO answer was guessed — every gap is STUBBED
fail-closed with a TODO. Operator answered 1/2/3 on 2026-06-16 (applied below); only the FRAMES remain.

## Autoexploration

### RESOLVED by Operator (2026-06-16) — applied as game-truth, not guessed
1. **Body kind / selection (was OPEN-3).** The per-row marker is EXPLORED vs UNEXPLORED (unexplored =
   a small box inside a hollow box), NOT a kind icon. Stars self-explore via the honk, so the tour
   targets UNEXPLORED PLANETS/MOONS — all the ORBIT case. So `classify_kind -> ORBIT` (current default)
   is CORRECT and the DROP branch is intentionally unused for autoexplore. Selection runs off the journal
   scan-set (next_unexplored), which already skips honked stars. STUB-1 resolved (doc only, no behavior change).
2. **DROP-target visited signal.** No special signal needed — SC-assist drops automatically at a targeted
   station unless a body blocks the path (edge case); the journal shows `SupercruiseExit body_type = Station`
   (vs Star). Claude scrapes the exact field from REAL logs (no guess). Secondary, since the tour is
   planets/moons (ORBIT). STUB-2 direction set.
3. **SET FILTERS GuiFocus.** There is NO special filter screen — it is part of the left/NAV panel,
   GuiFocus = 2 (already known). FOLLOW-ON (code, not operator): the council's automated SET-FILTERS pass
   (step_explore S0) is over-built — Operator sets nav filters manually; the bot just reads the panel. S0 must
   be SIMPLIFIED so it does not perpetually self-block the tour (today S0 fails closed -> tour never runs).

### REMAINING autoexplore blocker (operator — frames)
4. **Nav-panel calibration frame.** One screenshot of the NAVIGATION tab with a few bodies listed (ideally
   an UNEXPLORED system showing the box-in-hollow-box markers) — to calibrate the reader's region + row/
   column crops. THE last blocker for the tour to actually run. Operator grabs one next time in-game (not
   launching now). Until then the reader is calibration-pending and step_explore fail-closes (no keypresses).

## Smack (your correction)
5. **Escape-vector frames** for `detect_escape_vector`: (a) a BLUE star-smack escape vector, (b) a
   PURPLE planet-smack escape vector, (c) a deliberate drop showing NO vector. And confirm the model:
   escape vector PRESENT = smacked; color = body (blue=star, purple=planet); no vector = deliberate
   drop. Anything else that distinguishes a smack from a deliberate drop?

## Smack — additional confirmations (LOW priority; safe defaults already coded, confirm when free)
6. **Escape-vector PERSISTENCE (OQ1).** After a smack-drop completes, does the escape vector STAY on the
   HUD/compass or clear? For how long / what clears it? Matters for restart-while-smacked: if it clears,
   a cold restart can't CV-confirm a smack → it safely abstains (no auto-recovery). Default today = abstain
   on restart (safe, but won't auto-recover a restart-while-smacked).
7. **Planet-smack recovery mechanic (OQ6).** Does the existing STAR `smack_recovery` dance (nav-panel
   row-0 lock → pitch-180 body-astern → FsdCooldown gate → escape-vector charge → 13s clear) work
   UNCHANGED for a PLANET, or is any step star-specific? Default = reuse the star procedure for planets,
   flagged as a risk until you confirm.
8. **Planet preempt OK? (OQ5).** Widening the mid-scene smack-preempt to planets means a *deliberate*
   planet drop will ABORT a live arrival/dock scene (re-dispatch then continues benign — no recovery).
   Acceptable, or does a real planet-approach flow get disrupted? Default = wide preempt + narrow CV-gated
   recovery (safe, but may briefly abort a benign planet scene).

## Jump flow / clearance loop (C1) — confirmations (safe defaults coded)
9. **Pitch direction to clear an obstruction (OQ2).** When a jump is obstructed and the bot pitches off
   the blocking body to retry, which way? Default coded = pitch DOWN (your existing fixed pick). Does that
   hold for a planet's dark side / a glaring star, or must it be body-aware?
10. **Arrival "let orbit acquire" 13s wait (OQ3).** arrival.toml has a 13s settle AFTER sc-assist orbit
    that is an orbit-ACQUISITION wait, not a jump-clearance wait. Killing it (per "kill the 13s waits") may
    change when the next-hop target locks. I'm HOLDING this one's deletion pending your call — the genuine
    jump-clearance 13s waits ARE being killed. Keep it, or is orbit-acquire reliably done sooner?
11. **Scope: sc_resume.toml + startup.toml have the SAME blind-wait jump tail (OQ4).** Your kill list named
    dock_resume + arrival only. Want the same clearance step in sc_resume + startup too (consistent), or
    leave them? startup's tail has a retry_anchor (step 19) that needs careful re-homing if we touch it.

## Already answered — thank you (no action)
- Q2 jump obstruction: HUD-only, stars+planets block, stations don't. CV-free StartJump-loop chosen.
- STARSMACK fires only on a real star-smack; planet-smack is the separate purple-vector case.
