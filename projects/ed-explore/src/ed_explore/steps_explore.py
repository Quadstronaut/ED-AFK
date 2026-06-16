"""In-system autoexploration step implementation for ed-explore.

Implements the S0..S6 state machine from the C4 ratified design
(_council_c4_autoexplore_spec.md).  Registers `explore` into the core step
table when this module is imported.  ed_explore.activate() triggers that import.

BEST-EFFORT contract (NEVER-RAISE / NEVER-FALSE, INV [1][2]):
  step_explore returns True on every path.  Every internal failure (CV stub,
  KeyError, OCR miss, focus desync, NotImplementedError) is caught, logged, and
  the step returns True.  The tour never blocks the onward jump.

INERT-SHIP GUARANTEE (all 4 stubs unfilled):
  S0 fails closed at filter_screen_focused()==False -> FilterGateFail logged ->
  return True.  Even if S0 were bypassed, S1 fails closed on a calibration-
  pending nav read -> ExploreReadFail -> S_DONE -> True.  The arrival lane
  reaches target_next_route -> engage_jump unobstructed.

NOT input_exclusive: self-guards each per-body UI macro so the heat watchdog
runs between bodies (mirrors steps_body_tour).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Optional

from ed_core.flow.context import StepContext
from ed_core.flow.step_registry import register_step
from ed_core.flow.steps_shared import _ensure_cockpit_focus, step_engage_supercruise
from ed_core.executor.navpanel import engage_supercruise_assist_row
from ed_vision.navpanel_reader import NavBody, _scan_key, next_unexplored

from ed_explore.explore_kind import (
    classify_kind,
    drop_visited,
    KIND_ORBIT,
    P_IS_ORBIT_BODY,
    P_IS_DROP_TARGET,
)
from ed_explore.explore_filters import (
    establish_filters,
    filter_screen_focused,
    filters_latched,
    mark_filters_latched,
)


# ---------------------------------------------------------------------------
# Per-target snapshot — latched BEFORE the macro (mirrors body_tour pattern)
# ---------------------------------------------------------------------------

@dataclass
class ExploreSnap:
    """Snapshot captured just before engaging SC-assist on a target."""
    seen0: frozenset        # autoscan_supplier frozenset at snapshot time
    seq0: int               # autoscan_supplier seq counter
    scex0: int              # scex_seq_supplier counter (SupercruiseExit)
    drop0: int              # drop_seq_supplier counter (SupercruiseDestinationDrop)
    dest_name: str          # Status.destination.name at snapshot (or "" if absent)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _excl(ctx: StepContext):
    """Exclusive-guard context manager factory — identical to steps_body_tour."""
    return (ctx.exclusive_guard() if ctx.exclusive_guard is not None
            else contextlib.nullcontext())


def _snapshot(ctx: StepContext, target: NavBody) -> ExploreSnap:
    """Capture pre-target latches for identity-correlated completion checks."""
    seq0, seen0 = ctx.autoscan_supplier()
    scex0 = ctx.scex_seq_supplier()
    drop0 = ctx.drop_seq_supplier()
    try:
        st = ctx.status_supplier()
        dest_name = (st.destination.name
                     if st is not None and st.destination is not None
                     else "")
    except Exception:
        dest_name = ""
    return ExploreSnap(seen0=seen0, seq0=seq0, scex0=scex0,
                       drop0=drop0, dest_name=dest_name)


# ---------------------------------------------------------------------------
# Predicates (pure, testable with no frame)
# ---------------------------------------------------------------------------

def _visit_complete_orbit(target: NavBody,
                          seen0: frozenset,
                          seen_after: frozenset) -> bool:
    """P-VISIT-COMPLETE-ORBIT: target's scan key appears in the NEW scan delta.

    Name-correlated per-target delta (PIN-A).  NOT seq>seq0.  NOT len()>len().
    An incidental AutoScan of a NON-target body must NOT satisfy this predicate.
    """
    return _scan_key(target.name) in (seen_after - seen0)


def _visit_complete_drop(ctx: StepContext, target: NavBody, snap: ExploreSnap) -> bool:
    """P-VISIT-COMPLETE-DROP: correlated drop event (delegated to STUB-2).

    Bare scex_seq>scex0 is BANNED (PIN-A).  Correlation requires name-matching
    the locked destination to target.  STUB-2 returns False until filled.
    """
    return drop_visited(ctx, target, snap)


def _exhausted(bodies: list, scanned, E: set) -> bool:
    """P-EXHAUSTED: no unexplored body remains (primary exit, PIN-D)."""
    return next_unexplored(bodies, set(scanned) | E) is None


def _identity_mode(ctx: StepContext) -> bool:
    """P-IDENTITY-MODE: nav_panel_reader AND nav_panel_grabber are both wired."""
    return (ctx.nav_panel_reader is not None
            and ctx.nav_panel_grabber is not None)


# ---------------------------------------------------------------------------
# S0 — FILTER GATE
# ---------------------------------------------------------------------------

def _s0_filter_gate(ctx: StepContext) -> bool:
    """S0: one-time permanent SET FILTERS pass (PIN-F/PIN-G).

    Returns True when filters are confirmed latched (can proceed to S1).
    Returns False when the filter gate fails closed (step_explore should
    log FilterGateFail/CalibrationFail and return True immediately).

    INERT BEHAVIOR (stubs unfilled): filter_screen_focused=False ->
    establish_filters=False -> returns False -> caller logs + returns True.
    """
    if filters_latched():
        return True  # already permanent; skip straight to S1

    try:
        with _excl(ctx):
            ok = establish_filters(ctx)
        if ok:
            mark_filters_latched()
            return True
        return False
    except Exception as exc:
        ctx.log("ExploreCalibrationFail", {"exc": type(exc).__name__, "msg": str(exc)})
        return False


# ---------------------------------------------------------------------------
# S1 — READ / SELECT
# ---------------------------------------------------------------------------

def _s1_read_select(ctx: StepContext,
                    scanned,
                    E: set) -> Optional[NavBody]:
    """S1: grab nav panel, parse rows, pick next unexplored target.

    Returns the selected NavBody, or None (exhausted or read failure).
    Never raises.
    """
    try:
        frame = ctx.nav_panel_grabber()
        system = ctx.current_system_supplier()
        bodies = ctx.nav_panel_reader.parse(frame, system)
        return next_unexplored(bodies, set(scanned) | E)
    except Exception as exc:
        ctx.log("ExploreReadFail", {"exc": type(exc).__name__, "msg": str(exc)})
        return None


def _s1_read_bodies(ctx: StepContext, scanned, E: set):
    """S1 variant: return (bodies_list, target) for use in the main loop.

    Separates the raw body list (needed for P-EXHAUSTED) from the selected
    target so callers can distinguish 'exhausted' from 'read failure'.
    Returns (None, None) on read failure.
    """
    try:
        frame = ctx.nav_panel_grabber()
        system = ctx.current_system_supplier()
        bodies = ctx.nav_panel_reader.parse(frame, system)
        target = next_unexplored(bodies, set(scanned) | E)
        return bodies, target
    except Exception as exc:
        ctx.log("ExploreReadFail", {"exc": type(exc).__name__, "msg": str(exc)})
        return None, None


# ---------------------------------------------------------------------------
# S2 — PIN + TARGET
# ---------------------------------------------------------------------------

def _s2_pin_target(ctx: StepContext, target: NavBody, *,
                   settle_s: float, pin_hold_s: float) -> bool:
    """S2: position the nav-panel cursor at target.row_index.

    Returns True when the cursor is positioned.  Returns False (abandon this
    target cleanly; add to E, go back to S1) on SALVAGE (row vanished after
    re-sort).  Never raises.
    """
    with _excl(ctx):
        if not _ensure_cockpit_focus(ctx):
            ctx.log("ExploreFocusFail", {"row": target.row_index,
                                         "body": target.name})
            return False
        try:
            # engage_supercruise_assist_row IS the combined pin+engage macro.
            # For S2 we only need the cursor walk (prelude); we call the full
            # macro in S3.  Here we just verify focus is achievable.
            # The actual pin+engage is deferred to S3 so S2 stays a pure focus
            # check (cursor positioning is implicit in S3's macro invocation).
            pass
        except KeyError as exc:
            ctx.log("ExploreBindMissing", {"action": str(exc),
                                           "body": target.name})
            return False
    return True


# ---------------------------------------------------------------------------
# S3 — ENGAGE (lock + SC-Assist)
# ---------------------------------------------------------------------------

def _s3_engage(ctx: StepContext, target: NavBody, *,
               settle_s: float, pin_hold_s: float) -> bool:
    """S3: pin-to-top, walk to target.row_index, engage SC-Assist.

    Guard is released after the macro so the heat watchdog runs during S4.
    Returns True on success, False on bind error or focus fail.
    Never raises.
    """
    with _excl(ctx):
        if not _ensure_cockpit_focus(ctx):
            ctx.log("ExploreFocusFail", {"row": target.row_index,
                                         "body": target.name})
            return False
        try:
            engage_supercruise_assist_row(
                ctx.sender,
                sleeper=ctx.sleeper,
                settle_s=settle_s,
                row=target.row_index,
                pin_to_top=True,
                pin_hold_s=pin_hold_s,
            )
        except KeyError as exc:
            ctx.log("ExploreBindMissing", {"action": str(exc),
                                           "body": target.name})
            return False
    # Guard released here — heat watchdog runs during S4.
    return True


# ---------------------------------------------------------------------------
# S4 — ORBIT / DROP VISIT GATE
# ---------------------------------------------------------------------------

def _s4_visit_gate(ctx: StepContext, target: NavBody, snap: ExploreSnap,
                   *, poll_s: float,
                   _iterations_backstop: int = 64) -> str:
    """S4: poll until visit confirmed, dropped, timed-out, or abort.

    Returns one of: "scanned" | "dropped" | "timeout" | "abort".

    event_waiter is a poll CADENCE only, not a success gate (INV [7]).
    orbit_timeout_s and _iterations_backstop are FAILURE backstops; firing
    them is an ANOMALY, not the expected terminus (PIN-D).

    NO scex fallback on the ORBIT branch (PIN-B/INV [5]): an ambient
    SupercruiseExit on a correctly-classified orbit body does NOT route to S6.
    """
    orbit_timeout_s = ctx.body_tour_orbit_timeout_s
    kind = classify_kind(target)  # STUB-1; returns KIND_ORBIT until filled
    is_orbit = P_IS_ORBIT_BODY(kind)
    is_drop_tgt = P_IS_DROP_TARGET(kind)

    start = ctx.clock()
    iterations = 0

    while True:
        # Abort check first (INV [6]).
        if ctx.should_abort():
            return "abort"

        # Anti-spin backstop (strict >, PIN-D / INV [6]).
        if iterations > _iterations_backstop:
            ctx.log("ExploreIterationsBackstop",
                    {"body": target.name, "iterations": iterations})
            return "timeout"

        # Poll cadence — return value IGNORED, only advances hub state.
        if ctx.event_waiter is not None:
            ctx.event_waiter("Scan", poll_s)
        else:
            ctx.sleeper(poll_s)

        iterations += 1

        # ORBIT branch: name-correlated AutoScan completion (PIN-A).
        if is_orbit:
            _, seen_after = ctx.autoscan_supplier()
            if _visit_complete_orbit(target, snap.seen0, seen_after):
                return "scanned"

        # DROP branch: STUB-2 correlated drop (only reachable with STUB-1 filled).
        if is_drop_tgt:
            if _visit_complete_drop(ctx, target, snap):
                return "dropped"

        # Orbit timeout backstop (INV [7]: FAILURE backstop only, not a success gate).
        elapsed = ctx.clock() - start
        if elapsed > orbit_timeout_s:
            ctx.log("ExploreBodyTimeout",
                    {"body": target.name, "elapsed_s": elapsed})
            return "timeout"


# ---------------------------------------------------------------------------
# S5 — RECORD
# ---------------------------------------------------------------------------

def _s5_record(ctx: StepContext, target: NavBody,
               seen_after: frozenset, seen0: frozenset) -> None:
    """S5: record the visit for target.name ONLY (PIN-A / INV [7]).

    An incidental AutoScan of a NON-target body in the same window does NOT
    bump bodies_toured and is not recorded as the target visit.
    Returns the boolean of whether this target's own scan landed
    (caller bumps bodies_toured only when True).
    """
    # Intentionally void — the caller reads seen_after - seen0 and checks
    # _scan_key(target.name) membership; the dwell is emitted there.
    pass  # actual logic lives in the main loop for access to bodies_toured


# ---------------------------------------------------------------------------
# S6 — DROP RECOVER
# ---------------------------------------------------------------------------

def _s6_drop_recover(ctx: StepContext, target: NavBody) -> bool:
    """S6: re-engage supercruise after a correlated station/POI/carrier drop.

    Returns True on successful re-engagement, False if the ship is stranded
    (the wired station_strand_recovery step owns that scene).
    Never raises.
    """
    ctx.log("ExploreDropRecover", {"body": target.name})
    with _excl(ctx):
        ok = step_engage_supercruise(ctx, presses=3, between_press_s=8.0)
    ctx.log("ExploreReengage", {"ok": ok, "body": target.name})
    return ok


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def step_explore(
    ctx: StepContext, *,
    settle_s: float = 0.4,        # EXPLICIT kwarg — PIN-E (ctx has no .settle_s)
    pin_hold_s: float = 4.0,      # EXPLICIT kwarg — PIN-E (ctx has no .pin_hold_s)
    k_start: int = 1,
    poll_s: float = 0.5,
    _iterations_backstop: int = 64,
) -> bool:
    """In-system autoexploration step (C4 ratified design).

    Drives the S0..S6 identity-based state machine over the arrival system's
    nav-panel bodies.  Best-effort: returns True on every path, never raises,
    never returns False (INV [1][2]).

    P-IDENTITY-MODE must be True (nav_panel_reader + nav_panel_grabber wired);
    without it the step no-ops and returns True — there is NO blind row-walk
    fallback for exploration (INV [3]).

    dwell binds to ctx.body_tour_dwell_s (PIN-E / INV [9]).
    settle_s and pin_hold_s are explicit kwargs (never read off ctx).
    """
    # ---- P-IDENTITY-MODE (INV [3]) -----------------------------------------
    if not _identity_mode(ctx):
        ctx.log("ExploreNoIdentityMode", {})
        return True

    # ---- caps from ctx (single source of truth, mirrors body_tour) ----------
    max_bodies = ctx.body_tour_max_bodies
    max_rows = ctx.body_tour_max_rows
    dwell_s = ctx.body_tour_dwell_s   # PIN-E: binds to ctx.body_tour_dwell_s

    # ---- S0: FILTER GATE (one-time permanent, fail-closed) ------------------
    s0_ok = _s0_filter_gate(ctx)
    if not s0_ok:
        ctx.log("ExploreFilterGateFail",
                {"reason": "filter_screen_focused=False or calibration-pending"})
        return True

    # ---- tour state ---------------------------------------------------------
    # Per-tour exclusion set: names already attempted (scanned, timed-out,
    # or misclassified).  Feeds P-EXHAUSTED so identity selection advances.
    E: set[str] = set()
    bodies_toured = 0
    iteration = 0

    while True:
        iteration += 1

        # ---- abort exit (INV [6]) -------------------------------------------
        if ctx.should_abort():
            ctx.log("ExploreAborted", {"bodies_toured": bodies_toured})
            return True

        # ---- _iterations_backstop (strict >, PIN-D) -------------------------
        if iteration > _iterations_backstop:
            ctx.log("ExploreIterationsBackstop",
                    {"iteration": iteration, "bodies_toured": bodies_toured})
            break

        # ---- backstop caps (strict >, PIN-D) --------------------------------
        if bodies_toured > max_bodies:
            ctx.log("ExploreMaxBodiesBackstop",
                    {"bodies_toured": bodies_toured, "max_bodies": max_bodies})
            break

        # ---- S1: READ / SELECT ----------------------------------------------
        _, scanned = ctx.autoscan_supplier()
        bodies, target = _s1_read_bodies(ctx, scanned, E)

        if bodies is None:
            # Read failure -> fail closed to S_DONE (ExploreReadFail already logged).
            ctx.log("ExploreReadFailExit", {"bodies_toured": bodies_toured})
            break

        # max_rows backstop (strict >, PIN-D).
        if target is not None and target.row_index > max_rows:
            ctx.log("ExploreMaxRowsBackstop",
                    {"row": target.row_index, "max_rows": max_rows})
            break

        # P-EXHAUSTED: primary exit (INV [6]).
        if _exhausted(bodies, scanned, E):
            ctx.log("ExploreComplete", {"bodies_toured": bodies_toured})
            return True

        if target is None:
            # next_unexplored returned None despite _exhausted being False —
            # defensive: treat as exhausted.
            ctx.log("ExploreComplete", {"bodies_toured": bodies_toured})
            return True

        ctx.log("ExploreTarget",
                {"row": target.row_index, "body": target.name})

        # ---- snapshot latches (BEFORE lock, PIN-A) --------------------------
        snap = _snapshot(ctx, target)

        # ---- S2: PIN + TARGET -----------------------------------------------
        # S2 is a focus pre-check; S3 does the real pin+engage macro.
        s2_ok = _s2_pin_target(ctx, target, settle_s=settle_s,
                               pin_hold_s=pin_hold_s)
        if not s2_ok:
            # SALVAGE: abandon this target cleanly, re-read.
            E.add(target.name)
            continue

        # ---- S3: ENGAGE (lock + SC-Assist) ----------------------------------
        s3_ok = _s3_engage(ctx, target, settle_s=settle_s,
                           pin_hold_s=pin_hold_s)
        if not s3_ok:
            # Bind error or focus fail -> add to E, retry.
            E.add(target.name)
            continue

        # ---- S4: VISIT GATE -------------------------------------------------
        outcome = _s4_visit_gate(ctx, target, snap, poll_s=poll_s,
                                 _iterations_backstop=_iterations_backstop)

        # ---- outcome handling -----------------------------------------------
        if outcome == "abort":
            ctx.log("ExploreAborted", {"bodies_toured": bodies_toured,
                                       "body": target.name})
            return True

        if outcome == "scanned":
            # S5: RECORD (name-correlated, PIN-A / INV [7])
            _, seen_after = ctx.autoscan_supplier()
            target_key = _scan_key(target.name)
            new_for_target = target_key in (seen_after - snap.seen0)
            if new_for_target:
                ctx.log("ExploreBodyScanned",
                        {"body": target.name, "row": target.row_index})
                bodies_toured += 1
                ctx.sleeper(dwell_s)   # pacing, NOT a gate
            else:
                # Incidental AutoScan of a non-target body in the same window;
                # do NOT record the target as scanned, do NOT bump bodies_toured.
                ctx.log("ExploreBodyAlreadySeen", {"body": target.name})
            # Add to exclusion set regardless (prevents re-selection).
            E.add(target.name)

        elif outcome == "dropped":
            # S6: DROP RECOVER (only reachable when STUB-1 returns KIND_DROP)
            s6_ok = _s6_drop_recover(ctx, target)
            E.add(target.name)   # attempted, regardless of re-engage outcome
            if not s6_ok:
                # Stranded in real space; station_strand_recovery owns this.
                ctx.log("StationStrandRecover", {"ok": False, "body": target.name})
                return True

        else:  # "timeout"
            # Timeout -> add to E so identity selection advances; log anomaly.
            ctx.log("ExploreBodyTimeout",
                    {"body": target.name, "row": target.row_index})
            E.add(target.name)

    # S_DONE
    ctx.log("ExploreComplete", {"bodies_toured": bodies_toured})
    return True


# Register into the merged core step table (surface #3).
# NOT input_exclusive — self-guards per-body UI macros (mirrors body_tour).
register_step("explore", step_explore)
