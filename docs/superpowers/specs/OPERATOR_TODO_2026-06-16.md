# OPERATOR TODO — game-truth gaps Operator fills (2026-06-16)

These are the ONLY blockers between the autonomous councils and testable autoexploration.
Everything else is being built tonight. NO answer was guessed — each gap below is STUBBED in code
with a clear interface + a TODO marker; fill these and the stubs become live.

## Autoexploration (unblocks the test you wanted)
1. **Body KIND per nav-panel row (OPEN-3).** In the NAVIGATION tab list, can you tell a row's TYPE
   *before* selecting it — an icon or text marking planet/moon/star (=ORBIT) vs station/outpost/POI/
   nav-beacon/carrier (=DROP)? Or do you only learn the type after SC-assist (orbit vs drop)?
   (Stub currently defaults every row to ORBIT-conservative.)
2. **DROP-target visited signal.** When SC-assist DROPS you at a station/outpost/POI/nav-beacon
   (a drop, not an orbit), which journal event fires? (SupercruiseDestinationDrop? ApproachSettlement?
   Docked? ApproachBody?) — needed to mark drop-targets visited.
3. **SET FILTERS GuiFocus.** Open the nav panel → SET FILTERS sub-screen, and tell me the Status.json
   `GuiFocus` number there.
4. **Nav-panel calibration frame.** A screenshot of the NAVIGATION tab with a few bodies listed
   (ideally an UNEXPLORED system) — so I can calibrate the reader's region + row/column crops
   (current region is an estimate, so reads are unreliable until calibrated).

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
