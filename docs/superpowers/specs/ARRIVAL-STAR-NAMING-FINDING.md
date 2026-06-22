# Arrival star is NOT necessarily the system name — use the ICON, not the name (2026-06-21, LIVE)

**Operator-witnessed, LIVE. Refutes the Name-based star/station discrimination** asserted in
`D1-DESTINATION-DISCRIMINATOR-FINDING.md`, `predicates.py:_destination_is_local_star`, and the
`_captured_name_is_local_star` guard landed at cc50366. Those all assume an arrival/local star's Name
equals the system name (or `<system> <letter>`). **That assumption is false.**

## The counterexample
System **LAWD 26** (arriving from Shinrarta Dezhra). The **arrival star is `GLIESE 293 B`** — a
**Wolf-Rayet star** (detail pane: `STAR CLASS: WOLF-RAYET STAR`, `CAN FUEL SCOOP: NO`). Both `GLIESE 293 B`
and `LAWD 26` are stars in the system (nav list filtered to STARS only confirms it). The arrival star's
name (`GLIESE 293 B`) has **no relation** to the system name (`LAWD 26`).

## The rules (operator-confirmed)
1. **On entering a system, the ship ALWAYS arrives at a star.** (Hyperspace drops at the primary arrival star.)
2. **The arrival star's NAME is NOT necessarily the system name.** Name-pattern matching (`Name==system` /
   `<system> A..Z`) is an UNRELIABLE star discriminator — it misses off-pattern arrival stars like GLIESE 293 B.
3. **The authoritative "this is a star" signals are:**
   - **The column-0 ICON** — the ✦ four-point star glyph (the nav-panel icon classifier
     `ed_vision/navpanel_column0.py` already detects this). USE THE ICON.
   - **The detail pane** has a `STAR CLASS` row (e.g. `WOLF-RAYET STAR`) and a `CAN FUEL SCOOP` row
     (`YES`/`NO`). Only a STAR carries these rows — CV-readable, definitive per-body confirmation. (`CAN FUEL
     SCOOP` also feeds the scoop decision: Wolf-Rayet = NO.)
4. **The right-side TARGET panel's `FACTION / ECONOMY` (e.g. EAST INDIA COMPANY / CORPORATE / EXTRACTION) is
   SYSTEM-level info, NOT proof of a station.** A star shows it too. Do not infer "station" from it.

## What this breaks (tonight's committed work)
- **D1 finding's Name-disambiguation** (`station iff Body!=0 AND Name!=currentSystemName`) — REFUTED for the
  star side: a star can be `Body!=0` with `Name!=system` (GLIESE 293 B). Name cannot separate star from station.
- **`_destination_is_local_star`** (predicates.py) and **`_captured_name_is_local_star`** (boot_routes.py,
  cc50366) — both Name-based; both UNDER-detect off-pattern arrival stars. (cc50366's guard is still strictly
  better than no guard — it catches `Name==system` stars — so NOT a regression, but it is INCOMPLETE and must
  be replaced by an icon/STAR-CLASS-based check.)

## Correct direction (to be designed, NOT guessed)
Star-vs-station (and "is the locked target a star to park at") must key on the **column-0 ✦ icon** and/or the
**detail-pane STAR CLASS row**, plus the journal arrival-star / BodyType — NOT the name. Flag for the column-0
classifier: the ✦ glyph marks BOTH in-system stars AND the nearby-systems section — distinguish by the
distance unit (Ls in-system vs Ly nearby) / position, not the glyph alone.
