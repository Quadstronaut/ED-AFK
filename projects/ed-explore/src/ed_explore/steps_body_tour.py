"""Body-tour step implementation for ed-explore.

Relocated from ed_autojump.flow.steps (Step 5 of Phase-1 reorg). The step
itself is byte-identical — only the import paths changed (relative -> absolute,
ed_autojump.executor.navpanel stays in ed-autojump, imported deferred).

Registers `body_tour` into the core step table (surface #3) when this module
is imported. ed_explore.activate() triggers that import.
"""

from __future__ import annotations

import contextlib

from ed_core.flow.context import StepContext
from ed_core.flow.step_registry import register_step
# _ensure_cockpit_focus lives in ed-core shared prims (steps_shared).
from ed_core.flow.steps_shared import _ensure_cockpit_focus
# Predicates relocated to ed_core (Phase-1 reorg).
from ed_core.flow.predicates import _destination_is_local_star
# Navpanel macro relocated to ed_core (Phase-1 reorg).
from ed_core.executor.navpanel import engage_supercruise_assist_row
# engage_supercruise relocated to ed_core (Phase-1 reorg).
from ed_core.flow.steps_shared import step_engage_supercruise


def _body_tour_identity_target(ctx: StepContext, tried: set):
    """IDENTITY targeting helper (task #45): read the NAVIGATION panel and return
    the next UNEXPLORED in-system body — one not in the journal scanned-set and
    not already tried this tour. Its `row_index` drives the nav-panel cursor
    walk. Returns None when none remain. FAIL-OPEN: any read/OCR error (no
    tesseract, bad frame, uncalibrated region) is logged and returns None, which
    ends the tour and lets the jump resume — never raises into the loop."""
    try:
        from ed_vision.navpanel_reader import next_unexplored
        frame = ctx.nav_panel_grabber()
        _, scanned = ctx.autoscan_supplier()
        system = ctx.current_system_supplier()
        bodies = ctx.nav_panel_reader.parse(frame, system)
        return next_unexplored(bodies, set(scanned) | tried)
    except Exception as e:  # fail-open: OCR/env/frame issues never block the jump
        ctx.log("BodyTourReadFail", {"err": type(e).__name__})
        return None


def step_body_tour(
    ctx: StepContext, *,
    settle_s: float = 0.4,
    k_start: int = 1,                    # skip row 0 = just-orbited arrival star (criterion 2)
    consecutive_nonbody_stop: int = 3,   # "past the bodies" heuristic
    poll_s: float = 0.5,
) -> bool:
    """OPT-IN body tour (spec body_tour). Between arrival's star orbit and
    target_next_route, SC-assist-orbit the arrival system's bodies one at a
    time, each gated on ITS AutoScan, then fall through to the unchanged jump.

    PURE-ORBIT MODEL (M1): SC-assist toward a BODY orbits it in supercruise —
    no drop. The ship never leaves supercruise during the tour, so the resume
    jump is always from supercruise (D7). The ONLY way the ship drops is if a
    toured row turns out to be a STATION/POI (D2) — handled by re-engaging.

    BEST-EFFORT, fail-open-to-jump: EVERY internal failure path returns True
    (or advances a row). step_body_tour can NEVER return False, so the arrival
    lane always reaches target_next_route -> engage_jump. The tour cannot
    prevent the jump (mirrors sc_assist_orbit / scoop_refuel).

    Caps/dwell/timeout/flag come from ctx (config single source of truth, the
    widget_ring_enabled precedent); the arrival.toml step carries no params.
    NOT in INPUT_EXCLUSIVE_ACTIONS — self-guards each per-body lock so the
    heat watchdog runs between bodies (D6)."""
    # 1. OFF short-circuit (criterion 1) — before ANY supplier read or keypress.
    if not ctx.body_tour_enabled:
        ctx.log("BodyTourSkipped", {"reason": "disabled"})
        return True

    # step_engage_supercruise imported from ed_core.flow.steps_shared at module level.

    # Fresh per-`with` context manager around each per-body macro (PD3): the
    # guard is a FACTORY (ctx.exclusive_guard()), NOT a context manager itself.
    def _excl():
        return (ctx.exclusive_guard() if ctx.exclusive_guard is not None
                else contextlib.nullcontext())

    # 2. Advisory honk log (PD5) — read the latch, log it, NEVER block.
    ctx.log("BodyTourFssState",
            {"fss_discovered": ctx.fss_discovered_supplier()})

    # 2b. MIN-BODY GATE (operator 2026-06-08): only tour a system whose honk
    # BodyCount >= body_tour_min_bodies (0 = tour every system). Skips small
    # systems so the tour fires only on substantial ones.
    min_bodies = ctx.body_tour_min_bodies
    if min_bodies > 0 and ctx.fss_body_count_supplier() < min_bodies:
        ctx.log("BodyTourSkippedFewBodies",
                {"body_count": ctx.fss_body_count_supplier(), "min": min_bodies})
        return True

    max_bodies = ctx.body_tour_max_bodies
    max_rows = ctx.body_tour_max_rows
    dwell_s = ctx.body_tour_dwell_s
    orbit_timeout_s = ctx.body_tour_orbit_timeout_s

    # IDENTITY mode (task #45): read the panel + target the next UNEXPLORED body
    # by name when the reader+grabber are wired; else the legacy blind row walk.
    identity_mode = (ctx.nav_panel_reader is not None
                     and ctx.nav_panel_grabber is not None)
    tried: set[str] = set()      # body names already attempted this tour (timed
                                 # out or scanned) so identity selection advances
                                 # instead of re-picking a stuck body forever.

    row = k_start
    bodies_toured = 0
    consecutive_nonbody = 0
    while True:
        # ---- exhaustion / abort exits ------------------------------------
        if ctx.should_abort():
            ctx.log("BodyTourAborted", {"row": row})
            return True
        if row >= max_rows:
            break
        if bodies_toured >= max_bodies:
            break
        if consecutive_nonbody >= consecutive_nonbody_stop:
            ctx.log("BodyTourNonBodyStop",
                    {"row": row, "consecutive": consecutive_nonbody})
            break

        # ---- IDENTITY target selection (task #45) ------------------------
        # Read the NAVIGATION panel, pick the next in-system body not yet in the
        # scanned-set OR already tried this tour. None => no unexplored bodies
        # left (clean end). Any read failure fails OPEN -> end tour, jump resumes.
        if identity_mode:
            target = _body_tour_identity_target(ctx, tried)
            if target is None:
                ctx.log("BodyTourNoUnexplored", {"toured": bodies_toured})
                break
            row = target.row_index
            tried.add(target.name)
            ctx.log("BodyTourTarget", {"row": row, "body": target.name})

        # ---- snapshot latches BEFORE locking (mirrors target_next_route) --
        seq0, seen0 = ctx.autoscan_supplier()
        scex0 = ctx.scex_seq_supplier()
        drop0 = ctx.drop_seq_supplier()

        # ---- combined lock+engage (D3), guard wraps focus + macro (PD8) ---
        with _excl():
            if not _ensure_cockpit_focus(ctx):
                ctx.log("BodyTourFocusFail", {"row": row})
                return True                # focus desync -> end tour, jump resumes
            try:
                engage_supercruise_assist_row(
                    ctx.sender, sleeper=ctx.sleeper, settle_s=settle_s,
                    row=row, pin_to_top=True, pin_hold_s=4.0)
            except KeyError:
                ctx.log("BodyTourBindMissing", {"row": row})
                return True                # best-effort -> end tour, jump resumes
        # guard RELEASED here -> heat watchdog runs during the gate + dwell.

        # ---- first-row local-star identity skip (criterion 2 layer 2) -----
        # Blind-walk only: identity mode already skips the scanned arrival star
        # via the scanned-set cross-ref, and its `row` is a panel index, not the
        # k_start cursor, so this positional skip must not fire there.
        if not identity_mode and row == k_start:
            st = ctx.status_supplier()
            system = ctx.current_system_supplier()
            if _destination_is_local_star(st, system) is True:
                ctx.log("BodyTourSkipLocalStar", {"row": row, "system": system})
                consecutive_nonbody += 1
                row += 1
                continue

        # ---- per-body GATE (D1 + PD1 + PD6 + PD7) -------------------------
        start = ctx.clock()
        outcome = "timeout"
        while True:
            if ctx.should_abort():
                outcome = "abort"
                break
            # PUMP the hub so _apply_state advances the latches. The waiter
            # RETURN VALUE IS IGNORED — only the hub poll advances state
            # (exactly how target_next_route pumps event_waiter). Unwired
            # (unit tests) -> sleep, letting scripted suppliers advance.
            if ctx.event_waiter is not None:
                ctx.event_waiter("Scan", poll_s)
            else:
                ctx.sleeper(poll_s)
            # PD7: the "dropped" outcome keys on SupercruiseExit, NOT the drop
            # (the drop fires ~5s BEFORE the exit; re-engaging on the drop
            # would no-op while still in SC, then the ship drops anyway).
            if ctx.scex_seq_supplier() > scex0:
                outcome = "dropped"
                break
            seq, seen = ctx.autoscan_supplier()
            if seq > seq0:                  # PD6: ANY new AutoScan since snapshot (loose v1 gate)
                outcome = "scanned"
                break
            if ctx.clock() - start > orbit_timeout_s:   # backstop ONLY
                outcome = "timeout"
                break

        # ---- outcome handling --------------------------------------------
        if outcome == "abort":
            ctx.log("BodyTourAborted", {"row": row})
            return True
        if outcome == "scanned":
            _, seen = ctx.autoscan_supplier()
            new = seen - seen0
            if not new:
                # Already-seen body (e.g. the arrival star auto-scanned on
                # hyperspace exit): not a fresh body, no dwell. De-dup (D5).
                ctx.log("BodyTourAlreadySeen", {"row": row})
                consecutive_nonbody += 1
            else:
                ctx.log("BodyTourBodyScanned",
                        {"row": row, "new": sorted(new)})
                bodies_toured += 1
                consecutive_nonbody = 0
                ctx.sleeper(dwell_s)        # pacing, NOT a gate
        elif outcome == "dropped":
            # Station/POI hit (M2/D2): SupercruiseExit fired -> the ship is
            # NOW in real space. Re-engage SC inside the guard (PD8).
            ctx.log("BodyTourStationDrop", {"row": row, "drop_seq": drop0})
            with _excl():
                ok = step_engage_supercruise(ctx, presses=3, between_press_s=8.0)
            ctx.log("BodyTourReengage", {"ok": ok, "row": row})
            if not ok:
                # Stranded at a station; engage_jump's Status gate + the smack/
                # preempt machinery own the abnormal scene. End the tour.
                return True
            consecutive_nonbody += 1        # station does NOT count a body
        else:  # "timeout"
            ctx.log("BodyTourBodyTimeout", {"row": row})
            consecutive_nonbody += 1

        row += 1

    ctx.log("BodyTourComplete",
            {"bodies_toured": bodies_toured, "rows_examined": row - k_start})
    return True


# Register into the merged core step table (surface #3).
register_step("body_tour", step_body_tour)
