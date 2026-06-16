# Obstruction + smack game-truth (operator-confirmed 2026-06-16)

## FSD jump obstruction = MASSIVE BODIES only
Operator-tested in-game:
- **STATIONS do NOT block** a jump in real space (tried blocking with a station: SOLID ring,
  jump SUCCEEDED).
- **STARS block** (frame: `jump_obstructed_dashed_ring.png` — bright star, glare).
- **PLANETS block** (frame: `jump_obstructed_planet_darkside.png` — Founders World, ship on the
  DARK side, dashed ring VERY clear, HUD `>Dropping - too close`, MASS LOCKED lit).
- Caveat (operator): "does not mean this is true everywhere, but stations don't, stars do, planets do."
Signal is HUD-only (dashed jump-ring + `FRAME SHIFT CANCELLED: DESTINATION TARGET OBSCURED`), NOT
journaled, NOT a Status flag — so clearance gates on `StartJump`-absence (see the Q2 doc), and the
clearance maneuver pitches away from the nearest MASSIVE body (star/planet), not stations.

## Smack semantics (operator CORRECTION — supersedes the old "_smacked on any star-drop")
A "smack" = a FORCED drop from getting too close to a massive body (HUD `Dropping - too close`).
- **STARSMACK fires ONLY on an actual star-smack.** A SupercruiseExit / drop does NOT mean we smacked
  — a deliberate drop is not a smack. The current `_route_sc_exit` firing `smack_recovery` on ANY
  `SupercruiseExit` body_type==Star is WRONG (conflates deliberate star-drops with star-smacks).
- **PLANET-SMACK is a real, separate case** — same recovery mechanics as star-smack, but the **escape
  vector is PURPLE instead of BLUE.**
- Discriminator is VISION, journal-blind (cf. smack-journal-blind-vision-discriminator): an escape
  vector PRESENT = smacked; color = body type (BLUE=star, PURPLE=planet). A deliberate drop shows no
  escape vector. → needs a CV escape-vector detector (blue/purple) — **TODO: operator frames** (blue
  star-smack vector; purple planet-smack vector; a deliberate-drop no-vector case).
