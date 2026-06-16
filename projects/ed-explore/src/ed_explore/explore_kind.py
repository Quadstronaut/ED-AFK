"""Body-KIND classifiers for step_explore — STUB-1 + STUB-2 isolation.

All operator-blocked reads that depend on an as-yet-unknown per-row KIND
source (STUB-1) or the visited journal event for drop-targets (STUB-2) live
HERE so the rest of the exploration logic never reaches into unverified
territory. Fill both TODOs and nothing else in the codebase needs to change.

DAG: imports ONLY ed_vision (NavBody type); never imports a sibling domain.
"""

from __future__ import annotations

from ed_vision.navpanel_reader import NavBody

# Sentinel strings for body KIND — the two branches the S4 gate selects on.
KIND_ORBIT: str = "orbit"   # planets, moons, stars, BH, wolf-rayet -> SC-assist orbit
KIND_DROP: str = "drop"     # stations, nav beacons, POI, carriers -> drop into real space


# ---------------------------------------------------------------------------
# STUB-1  classify_kind — per nav-panel row KIND (operator-blocked, OPEN-3)
# ---------------------------------------------------------------------------

def classify_kind(row: NavBody) -> str:
    """Return the KIND sentinel for *row*.

    Total function over NavBody -> {KIND_ORBIT, KIND_DROP}; pure; never raises.
    The only caller is step_explore's S4 branch selector.

    STUB-1 — returns KIND_ORBIT unconditionally (conservative PIN-B default).
    A misclassified drop-target costs one wasted SC-assist approach that TIMES
    OUT into the exclusion set E; the DROP/S6 branch is dead in live flight
    until this stub is filled.

    TODO(operator): how to read body KIND per nav-panel row?
    Planet/moon/star/BH/wolf-rayet -> KIND_ORBIT.
    Station/POI/nav-beacon/carrier -> KIND_DROP. (Q6)
    No KIND field exists on NavBody and no per-row kind source is currently
    known; fill this once the authoritative source is identified.
    """
    return KIND_ORBIT  # STUB-1: default ORBIT-conservative


# ---------------------------------------------------------------------------
# Pure classifiers over the Q6 kind sets
# ---------------------------------------------------------------------------

def P_IS_ORBIT_BODY(kind: str) -> bool:
    """True when *kind* indicates a planet/moon/star/BH/wolf-rayet (Q6)."""
    return kind == KIND_ORBIT


def P_IS_DROP_TARGET(kind: str) -> bool:
    """True when *kind* indicates a station/nav-beacon/POI/carrier (Q6)."""
    return kind == KIND_DROP


# ---------------------------------------------------------------------------
# STUB-2  drop_visited — journal event correlation for drop-targets (PIN-A)
# ---------------------------------------------------------------------------

def drop_visited(ctx, target: NavBody, snap) -> bool:
    """Return True when a drop CORRELATED to *target.name* has been confirmed.

    Pure read over ctx suppliers + snap; never raises; called only from the S4
    DROP branch (which is itself dead until STUB-1 returns KIND_DROP for real
    drop-targets).

    STUB-2 — returns False (fail-closed / not-visited) until filled.
    Fail-closed: a station target times out into the exclusion set E (S4)
    rather than marking itself visited on a bare scex_seq counter (BANNED, PIN-A).

    TODO(operator): which journal event marks a station/POI/beacon DROP as
    visited, and how to correlate it to target.name?
    Candidates: SupercruiseDestinationDrop / Docked / ApproachSettlement (Q7).
    Correlation MUST be: Status.Destination.Name == target.name at drop (or a
    drop event whose body field matches target.name).  NEVER a bare
    scex_seq > snap.scex0 (PIN-A).
    """
    return False  # STUB-2: fail-closed not-visited
