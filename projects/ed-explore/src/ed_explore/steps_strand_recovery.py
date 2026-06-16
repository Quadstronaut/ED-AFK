"""Wired station-strand recovery step for ed-explore (PIN-C).

boot_routes._route_sc_exit (boot_routes.py:515) only routes body_type=='Star'
SupercruiseExit events.  A station/POI/carrier drop during the explore tour
produces NO automatic recovery route from the boot machinery.  This step fills
that gap; it is WIRED between explore and target_next_route in arrival.toml.

Best-effort contract (NEVER-RAISE / NEVER-FALSE):
  step_station_strand_recovery returns True on every path, never raises,
  never returns False.  Failure is logged as StationStrandRecover(ok=False)
  and the step hands the abnormal scene to the existing smack/preempt machinery.

Status-GATED: reads ctx.status_supplier() and StatusFlags from
ed_core.status.status (NOT magic literals — build fix 2).  Never wall-clock
waits; Status flags + journal events only (no-arbitrary-timed-waits rule).

Registers `station_strand_recovery` into the merged core step table on import.
"""

from __future__ import annotations

from ed_core.flow.context import StepContext
from ed_core.flow.step_registry import register_step
from ed_core.flow.steps_shared import step_engage_supercruise
from ed_core.status.status import StatusFlags


def step_station_strand_recovery(ctx: StepContext) -> bool:
    """Status-GATED re-engage sweep after an explore tour.

    Detects stranded-in-normal-space (not Supercruise AND not Docked AND not
    Landed) using real StatusFlags from ed_core.status.status — no magic
    integer literals (build fix 2 / INV [14]).

    If stranded: attempt supercruise re-engage (presses>1, between_press_s
    spacers).  On success: return True (tour resumes in SC).  On failure: log
    StationStrandRecover(ok=False) and return True (the smack/preempt machinery
    owns the abnormal scene).

    If NOT stranded (the normal in-SC tour exit): no-op return True.

    Best-effort: never raises, never returns False.
    """
    try:
        st = ctx.status_supplier()
        if st is None:
            # Cannot read status — assume not stranded (fail-safe).
            ctx.log("StrandRecoveryStatusNone", {})
            return True

        flags = getattr(st, "flags", 0)
        # Stranded := in normal space — not supercruise, not docked, not landed.
        # Use StatusFlags enum values (NOT magic literals, build fix 2).
        in_supercruise = bool(flags & StatusFlags.Supercruise)
        is_docked = bool(flags & StatusFlags.Docked)
        is_landed = bool(flags & StatusFlags.Landed)

        stranded = not in_supercruise and not is_docked and not is_landed

        if not stranded:
            # Normal path: ship is in supercruise (or safely docked/landed).
            ctx.log("StrandRecoveryNotStranded",
                    {"sc": in_supercruise, "docked": is_docked,
                     "landed": is_landed})
            return True

        # Stranded in normal space — attempt re-engage.
        ctx.log("StrandRecoveryAttempt",
                {"sc": in_supercruise, "docked": is_docked,
                 "landed": is_landed})
        ok = step_engage_supercruise(ctx, presses=3, between_press_s=8.0)
        ctx.log("StationStrandRecover", {"ok": ok})
        if not ok:
            # Log the failure; hand the scene to smack/preempt machinery.
            # Never raises, never returns False.
            pass
        return True

    except Exception as exc:
        # Defensive catch-all: log and return True (never raises, INV [1]).
        ctx.log("StrandRecoveryError",
                {"exc": type(exc).__name__, "msg": str(exc)})
        return True


# Register into the merged core step table (surface #3).
register_step("station_strand_recovery", step_station_strand_recovery)
