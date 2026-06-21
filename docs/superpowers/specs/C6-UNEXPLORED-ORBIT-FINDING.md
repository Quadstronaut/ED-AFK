# C6 — SC-assist on an UNEXPLORED body ORBITS (no strand-drop) — 2026-06-21

Pins the operator-witnessed confirmation that the council register
(`2026-06-21-COUNCIL-INCONSISTENCY-REGISTER.md`, INC-C6-DROP-HEDGE-13) capped at `likely` for lack of a
committed artifact. This doc is that artifact.

## Finding (operator-witnessed, LIVE)
Operator tested in-game 2026-06-21 (Shinrarta Dezhra, Mandalay): engaging **SC-assist on an UNEXPLORED
body** (the box-in-hollow-box marker, not yet scanned) behaves **identically to any explored body — it
ORBITS / holds in supercruise. It does NOT drop the ship to normal space.** Operator: "test #6 unexplored
makes no difference, it acts the same."

## Evidence class
**Operator-witnessed = LIVE** (per evidence-class discipline). This is a BEHAVIOURAL observation, NOT a
pinned frame — #8 (smack frames) was deferred, and #6 produced no screenshot. Corroborated by the
SC-assist button label on an orbitable body: **`SUPERCRUISE ASSIST AND ORBIT`** (the "AND ORBIT" is
literal; a station reads plain `SUPERCRUISE ASSIST` = drop). See
`navpanel_detail_sc_activate_1080.png`. Consistent with [[sc-assist-orbit-vs-drop-mechanics]] (SC-assist
orbits bodies, drops only at stations/POI).

## Implication for C6 (`nav_supercruise_unexplored`)
The explore loop uses the **ORBIT** branch only. **No DROP / SupercruiseExit re-engage / strand-recovery
branch is needed.** The C6-exploration DESIGN's B4 hedge (and the IN-GAME-CAPTURE-SHEET test #6) are
RESOLVED. Caveat retained: this is behavioural, not frame-pinned — if a future run ever observes a drop on
an unexplored row, re-open B4 before assuming the orbit path is total.
