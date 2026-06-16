# Q2 RESOLVED — jump obstruction is HUD-only (operator capture 2026-06-16)

**Test:** undock at a station, attempt an immediate hyperspace jump while still close to
the station + arrival star (system: near SS Automotua / Shinrarta Dezhra area; target HEVERI 19.4Ly).

**Result (operator + journal-verified):**
1. The FSD **REFUSES** the obstructed jump: HUD shows `FRAME SHIFT CANCELLED: DESTINATION
   TARGET OBSCURED`. The bot will NOT ram a star. Frame pinned: `jump_obstructed_dashed_ring.png`.
2. The obstruction signal is **VISUAL ONLY**:
   - HUD text `DESTINATION TARGET OBSCURED` (top-right).
   - The **jump-ring around the target widget is DASHED** (solid = clear, dashed = obstructed —
     matches the Q9 finding; this is the SAME jump-obstruction ring).
3. It is **NOT journaled and NOT a Status flag.** Searched the 2 latest journals: zero
   `Obscured`/`Cancel` lines. The event trace: `FSDTarget` 08:10:20 + 08:10:38, then **NO
   `StartJump`** — the cancelled jump leaves no journal trace. (MASS LOCKED was also lit from
   station proximity, but that is a separate, distance-based condition; once mass-lock clears,
   an obscured target still cancels with no flag.)

**Design implication (supersedes the route-complete council's "Q2 OPEN"):**
- Do NOT naively delete `dock_resume.toml`'s `wait s=13.0` and rely on `engage_jump`'s 5 Status
  flags — they do NOT cover "destination obscured." Confirmed.
- REPLACE the blind 13s wait with EITHER:
  (a) **CV gate** on the jump-ring (dashed→solid) and/or the `DESTINATION TARGET OBSCURED` HUD
      text — fly anti-star until the ring goes SOLID, then jump. (New CV detector; ties to the
      Q9 jump-ring + council C action layer. NOTE: operator reports the ring is HARD to read
      against the bright star — glare is a real CV challenge, cf. smack-compass-glare.)
  (b) **closed loop on `StartJump`** — attempt jump; if no `StartJump` event fires within the
      bounded poll, pitch anti-star + fly a bit, retry; repeat until `StartJump` fires. Gates on
      a real journal event, not a blind timer; reuses the existing pitch-star-off maneuver. No
      new CV strictly required (CV would make it faster/cleaner).

**CHOSEN (operator-confirmed 2026-06-16): option (b), CV-free.** The operator named the clean
simplification: the **absence of a `StartJump` event** after pressing jump IS the obstruction
signal — no CV, no calibration. Loop: press jump → bounded-poll for `StartJump` → if it does NOT
fire, pitch anti-star + fly clear a moment → retry → repeat until `StartJump` fires. The retry MUST
move (pitch off the star), not re-press in place (operator: "if I didn't move it would still be
obstructed"). Gates on a real journal event; reuses the existing pitch-star-off maneuver. No new CV.
- **(a) the jump-ring CV is DEMOTED to a maybe-later optimization** (jump the instant the ring goes
  solid vs over-flying). NOT needed for correctness; no SOLID/clear calibration frame is required.
  `jump_obstructed_dashed_ring.png` kept as reference only.
